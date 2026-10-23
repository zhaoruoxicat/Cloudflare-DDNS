"""Validation and private, atomic configuration storage."""
import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile

CONFIG_PATH = Path('/etc/cf-ddns/config.json')


class DDNSError(Exception):
    """A controlled error safe to display without credentials."""


def normalize_domain(value: str) -> str:
    if not isinstance(value, str):
        raise DDNSError('域名必须是文本。')
    try:
        name = value.strip().removesuffix('.').encode('idna').decode('ascii').lower()
    except (UnicodeError, ValueError):
        raise DDNSError('域名格式无效。') from None
    labels = name.split('.')
    if len(name) > 253 or len(labels) < 2 or any(
        not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels
    ):
        raise DDNSError('请输入有效的完整域名，不支持通配符。')
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name
    raise DDNSError('请输入域名，而不是 IP 地址。')


def validate_config(data: dict) -> dict:
    if not isinstance(data, dict):
        raise DDNSError('配置格式无效，请重新配置。')
    token = data.get('token')
    if not isinstance(token, str) or not token or len(token) > 4096 or any(
        ord(c) < 33 or ord(c) > 126 for c in token
    ):
        raise DDNSError('API Token 不能为空且不能包含空白或控制字符。')
    zone = normalize_domain(data.get('zone'))
    record = normalize_domain(data.get('record'))
    if record != zone and not record.endswith('.' + zone):
        raise DDNSError('记录域名必须属于指定的 DNS 区域。')
    interval = data.get('interval', 300)
    if type(interval) is not int or not 60 <= interval <= 86400:
        raise DDNSError('更新间隔必须是 60～86400 秒的整数。')
    return dict(token=token, zone=zone, record=record, interval=interval)


def load_config(path: Path = CONFIG_PATH) -> dict:
    try:
        with Path(path).open('r', encoding='utf-8') as stream:
            text = stream.read(65537)
        if len(text) > 65536:
            raise ValueError()
        return validate_config(json.loads(text))
    except DDNSError:
        raise
    except (OSError, ValueError, UnicodeError):
        raise DDNSError('配置不存在、不可读或已损坏，请运行 sudo cf-ddns configure。') from None


def atomic_json(path: Path, data: dict, mode: int = 0o600) -> None:
    """Replace a private JSON file without leaving partial content."""
    path = Path(path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        raise DDNSError('无法安全保存文件，请检查权限与磁盘空间。') from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def save_config(path: Path, data: dict) -> None:
    checked = validate_config(data)
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        Path(path).parent.chmod(0o700)
    except OSError:
        raise DDNSError('无法创建私有配置目录。') from None
    atomic_json(Path(path), checked)
