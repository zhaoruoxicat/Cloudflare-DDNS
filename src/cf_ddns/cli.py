"""Chinese terminal interface and systemd administration."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import warnings

from . import __version__
from .config import CONFIG_PATH, DDNSError, load_config, save_config, normalize_domain, validate_config
from .runner import STATE_DIR, read_status, worker

TIMER_DROPIN = Path('/etc/systemd/system/cf-ddns.timer.d/interval.conf')
TIMER = 'cf-ddns.timer'
SERVICE = 'cf-ddns.service'


def require_root():
    if os.geteuid() != 0:
        raise DDNSError('此操作需要管理员权限，请使用 sudo cf-ddns。')


def systemctl(*args, check=True, capture=False):
    try:
        result = subprocess.run(['/usr/bin/systemctl', *args], capture_output=capture,
                                text=True, check=False, env={**os.environ, 'LC_ALL': 'C.UTF-8'})
    except OSError:
        raise DDNSError('无法调用 systemctl，请在使用 systemd 的系统安装此 deb 包。') from None
    if check and result.returncode:
        raise DDNSError('systemd 操作失败；可运行 sudo cf-ddns logs 查看详情。')
    return result


def write_timer(interval: int):
    if type(interval) is not int or not 60 <= interval <= 86400:
        raise DDNSError('更新间隔必须是 60～86400 秒的整数。')
    temporary = None
    try:
        TIMER_DROPIN.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        fd, temporary = tempfile.mkstemp(prefix='.interval.', dir=TIMER_DROPIN.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(f'[Timer]\nOnUnitInactiveSec=\nOnUnitInactiveSec={interval}s\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, TIMER_DROPIN)
    except OSError:
        raise DDNSError('无法保存 systemd 定时配置。') from None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    systemctl('daemon-reload')


def enable():
    require_root()
    config = load_config(CONFIG_PATH)
    write_timer(config['interval'])
    systemctl('enable', '--now', TIMER)
    systemctl('start', SERVICE)
    print('已启用开机自启，并执行一次更新。')


def disable():
    require_root()
    systemctl('disable', '--now', TIMER)
    systemctl('stop', SERVICE)
    print('已停止自动更新并取消开机自启。')


def _ask(prompt, default, validate):
    while True:
        value = input(f'{prompt}' + (f' [{default}]' if default else '') + '：').strip()
        try:
            return validate(value or default)
        except (DDNSError, ValueError) as error:
            print(str(error) if isinstance(error, DDNSError) else '输入无效，请重试。')


def _interval(value):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise DDNSError('请输入 60～86400 秒的整数。') from None
    if not 60 <= number <= 86400:
        raise DDNSError('请输入 60～86400 秒的整数。')
    return number


def _yes_no(value):
    if value.lower() in ('y', 'yes', '是'):
        return True
    if value.lower() in ('n', 'no', '否'):
        return False
    raise DDNSError('请输入 y 或 n。')


def configure():
    require_root()
    if not sys.stdin.isatty():
        raise DDNSError('配置向导需要交互终端，请运行 sudo cf-ddns configure。')
    old = {}
    if CONFIG_PATH.exists():
        try:
            old = load_config(CONFIG_PATH)
        except DDNSError:
            print('现有配置无效，请重新输入；完成向导前原文件会保留。')
    print('Cloudflare DDNS 配置；Ctrl+C 取消，已有字段留空则保留。')
    print('Token 权限：目标区域的 Zone Read 和 DNS Edit；Token 输入不会显示。')
    while True:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            try:
                token = getpass.getpass('Cloudflare API Token' + (' [留空保留]' if old else '') + '：')
            except getpass.GetPassWarning:
                raise DDNSError('当前终端无法隐藏 Token 输入，请使用真实交互终端。') from None
        token = token or old.get('token', '')
        try:
            validate_config(dict(token=token, zone='example.com', record='example.com'))
            break
        except DDNSError as error:
            print(error)
    zone = _ask('DNS 区域域名（例如 example.com）', old.get('zone', ''), normalize_domain)
    def check_record(value):
        name = normalize_domain(value)
        if name != zone and not name.endswith('.' + zone):
            raise DDNSError('记录域名必须属于指定 DNS 区域。')
        return name
    record = _ask('完整记录域名（例如 vpn.example.com）', old.get('record', ''), check_record)
    interval = _ask('检查间隔（秒）', str(old.get('interval', 300)), _interval)
    was_enabled = systemctl('is-enabled', TIMER, capture=True, check=False).returncode == 0
    autostart = _ask('启用自动更新及开机自启？y/n', 'y' if was_enabled else 'n', _yes_no)
    # All prompts complete before any changes, so cancelling leaves prior files intact.
    config = dict(token=token, zone=zone, record=record, interval=interval)
    save_config(CONFIG_PATH, config)
    write_timer(interval)
    print('配置已保存；DNS 记录使用 DNS only（不经过 Cloudflare 代理）。')
    if autostart:
        # Refresh an already active timer after its interval changes.
        systemctl('enable', '--now', TIMER)
        systemctl('restart', TIMER)
        systemctl('start', SERVICE)
        print('已启用自动更新，并执行一次更新。')
    else:
        disable()
        print('需要立即更新时运行 sudo cf-ddns update。')


def status():
    print('自动更新状态：')
    systemctl('status', TIMER, '--no-pager', check=False)
    print('最近更新任务：')
    systemctl('status', SERVICE, '--no-pager', check=False)
    data = read_status(STATE_DIR)
    if not data:
        print('尚无更新记录。')
    labels = {'last_attempt': '最近尝试（UTC）', 'last_success': '最近成功（UTC）',
              'ipv4': '最近成功的公网 IPv4', 'result': '结果', 'error': '错误'}
    for key, label in labels.items():
        if key in data:
            print(f'{label}：{data[key]}')


def logs():
    try:
        return subprocess.run(['/usr/bin/journalctl', '-u', SERVICE, '-n', '50', '--no-pager', '-o', 'short-iso'], check=False).returncode
    except OSError:
        raise DDNSError('无法调用 journalctl。') from None


def update():
    require_root()
    load_config(CONFIG_PATH)
    systemctl('start', SERVICE)
    print('更新完成；运行 cf-ddns status 查看结果。')


def menu():
    actions = {'1': update, '2': configure, '3': status, '4': logs, '5': enable, '6': disable}
    while True:
        print('\nCloudflare DDNS\n1. 立即更新\n2. 修改配置\n3. 查看状态\n4. 查看日志\n5. 启用开机自启\n6. 关闭自动更新\n0. 退出')
        choice = input('请选择：').strip()
        if choice == '0':
            return 0
        if choice not in actions:
            print('请选择菜单中的数字。')
            continue
        try:
            actions[choice]()
        except DDNSError as error:
            print('错误：' + str(error), file=sys.stderr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='树莓派 Cloudflare DDNS：无参数启动中文配置向导或管理菜单。')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('command', nargs='?', choices=('configure', 'update', 'status', 'logs', 'enable', 'disable', 'worker'),
                        help='configure 配置；update 更新；status 状态；logs 日志；enable/disable 自动更新；worker 内部服务')
    args = parser.parse_args(argv)
    try:
        if args.command == 'worker':
            return worker()
        if not args.command:
            require_root()
            if not sys.stdin.isatty():
                raise DDNSError('请在交互终端运行 sudo cf-ddns configure，或指定 update/status 等子命令。')
            if not CONFIG_PATH.exists():
                configure()
                return 0
            return menu()
        action = {'configure': configure, 'update': update, 'status': status, 'logs': logs,
                  'enable': enable, 'disable': disable}[args.command]
        result = action()
        return result if isinstance(result, int) else 0
    except (KeyboardInterrupt, EOFError):
        print('\n已取消。', file=sys.stderr)
        return 130
    except DDNSError as error:
        print('错误：' + str(error), file=sys.stderr)
        return 1
    except Exception:
        # Never reveal token-bearing locals or an upstream exception body.
        print('操作失败，请检查权限、配置和 systemd 服务状态。', file=sys.stderr)
        return 1
