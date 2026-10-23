"""Cloudflare zone discovery and safe single-record synchronization."""
import re
from urllib.parse import urlencode

from .config import DDNSError, validate_config
from .network import request_json, validate_ipv4

BASE = 'https://api.cloudflare.com/client/v4'


def _identifier(value) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', value):
        raise DDNSError('Cloudflare 返回了无效的对象标识。')
    return value


def _api(config, method, path, payload=None):
    response = request_json(method, BASE + path, config['token'], payload)
    if response.get('success') is not True:
        # Error messages may echo input. Never log raw API bodies or messages.
        raise DDNSError('Cloudflare API 拒绝了操作，请检查 Token 权限和 DNS 记录冲突。')
    if 'result' not in response:
        raise DDNSError('Cloudflare API 响应缺少结果。')
    return response


def sync_record(config: dict, ipv4: str) -> str:
    config = validate_config(config)
    ipv4 = validate_ipv4(ipv4)
    zones = _api(config, 'GET', '/zones?' + urlencode(dict(name=config['zone'], per_page=50)))['result']
    if not isinstance(zones, list) or len(zones) != 1 or not isinstance(zones[0], dict) or zones[0].get('name') != config['zone']:
        raise DDNSError('未找到唯一的目标区域，请检查区域域名与 Zone Read 权限。')
    zone_id = _identifier(zones[0].get('id'))
    path = f'/zones/{zone_id}/dns_records'
    records = []
    page = 1
    while True:
        response = _api(config, 'GET', path + '?' + urlencode(dict(name=config['record'], per_page=100, page=page)))
        batch = response['result']
        if not isinstance(batch, list) or any(not isinstance(item, dict) or item.get('name') != config['record'] for item in batch):
            raise DDNSError('Cloudflare 返回了无效的 DNS 记录列表。')
        records.extend(batch)
        info = response.get('result_info', {})
        if not isinstance(info, dict):
            raise DDNSError('Cloudflare 返回了无效的分页信息。')
        total = info.get('total_pages', 1)
        if type(total) is not int or not 0 <= total <= 10:
            raise DDNSError('DNS 记录分页异常或记录过多，已停止操作。')
        if page >= total:
            break
        page += 1
    addresses = [item for item in records if item.get('type') == 'A']
    if len(addresses) > 1 or any(item.get('type') == 'CNAME' for item in records):
        raise DDNSError('存在重复 A 记录或同名 CNAME；请先在 Cloudflare 手动处理。')
    payload = dict(content=ipv4, proxied=False, ttl=1)
    if addresses:
        current = addresses[0]
        record_id = _identifier(current.get('id'))
        if current.get('content') == ipv4 and current.get('proxied') is False and current.get('ttl') == 1:
            return 'unchanged'
        result = _api(config, 'PATCH', path + '/' + record_id, payload)['result']
        outcome = 'updated'
    else:
        payload.update(type='A', name=config['record'])
        result = _api(config, 'POST', path, payload)['result']
        outcome = 'created'
    if not isinstance(result, dict) or result.get('type') != 'A' or result.get('name') != config['record'] or result.get('content') != ipv4 or result.get('proxied') is not False or result.get('ttl') != 1:
        raise DDNSError('Cloudflare 写入响应与目标记录不一致，请检查远端记录。')
    return outcome
