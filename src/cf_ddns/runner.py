"""Single update transaction and non-secret status storage."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path

from .config import DDNSError, load_config, atomic_json
from .cloudflare import sync_record
from .network import detect_ipv4

STATE_DIR = Path('/var/lib/cf-ddns')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read_status(state_dir: Path = STATE_DIR) -> dict:
    try:
        text = (Path(state_dir) / 'status.json').read_text(encoding='utf-8')
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, UnicodeError):
        pass
    return {}


def run_update(config_path: Path, state_dir: Path) -> int:
    state_dir = Path(state_dir)
    try:
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
        with (state_dir / 'update.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('已有更新任务运行，本次跳过。', flush=True)
                return 1
            previous = read_status(state_dir)
            # Copy only known safe fields from prior state.
            status = {key: previous[key] for key in ('last_success', 'ipv4') if key in previous}
            status['last_attempt'] = _now()
            try:
                config = load_config(config_path)
                ipv4 = detect_ipv4()
                result = sync_record(config, ipv4)
                status.update(last_success=_now(), ipv4=ipv4, result=result)
                print(f'DDNS {result}；公网 IPv4：{ipv4}', flush=True)
                code = 0
            except Exception as error:
                message = str(error) if isinstance(error, DDNSError) else '更新失败，请检查配置、网络与服务日志。'
                status.update(result='failed', error=message)
                print('DDNS 失败：' + message, flush=True)
                code = 1
            atomic_json(state_dir / 'status.json', status, mode=0o644)
            return code
    except (OSError, DDNSError):
        print('无法读取或保存运行状态，请检查服务账户与状态目录权限。', flush=True)
        return 1


def worker() -> int:
    credentials = os.environ.get('CREDENTIALS_DIRECTORY')
    state = os.environ.get('STATE_DIRECTORY')
    if not credentials or not state:
        print('worker 仅供 systemd 服务调用；请使用 sudo cf-ddns update。', flush=True)
        return 1
    return run_update(Path(credentials) / 'config.json', Path(state))
