"""Bounded direct HTTPS requests. Environment proxies are never used."""
import ipaddress
import json
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, ProxyHandler, HTTPRedirectHandler, build_opener

from . import __version__
from .config import DDNSError

MAX_RESPONSE = 1024 * 1024
IP_SOURCES = ('https://api4.ipify.org', 'https://ipv4.icanhazip.com', 'https://ip.3322.net')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DDNSError('服务器返回重定向，已拒绝转发请求。')


def _request(method: str, url: str, token: str | None = None, payload: dict | None = None) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise DDNSError('只允许不含用户名和密码的 HTTPS 地址。')
    if token is not None and (parsed.hostname != 'api.cloudflare.com' or parsed.port not in (None, 443)):
        raise DDNSError('凭证只能发送到 Cloudflare 官方 API。')
    headers = {'User-Agent': 'cf-ddns/' + __version__, 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = None
    if payload is not None:
        body = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    request = Request(url, data=body, headers=headers, method=method)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for attempt in range(3):
        try:
            with opener.open(request, timeout=10) as response:
                data = response.read(MAX_RESPONSE + 1)
                if len(data) > MAX_RESPONSE:
                    raise DDNSError('服务器响应过大。')
                return data
        except HTTPError as error:
            status = error.code
            retry_after = error.headers.get('Retry-After', '') if error.headers else ''
            error.close()
            if status in (401, 403):
                raise DDNSError('Cloudflare 认证失败，请检查 Token 与区域权限。') from None
            if status != 429 and not 500 <= status <= 599:
                raise DDNSError(f'HTTPS 请求失败（HTTP {status}）。') from None
            if attempt == 2:
                raise DDNSError(f'服务器暂时不可用（HTTP {status}），已达到重试次数。') from None
            delay = min(int(retry_after), 10) if retry_after.isdigit() and len(retry_after) < 6 else 2 ** attempt
        except (URLError, OSError, TimeoutError, ssl.SSLError):
            if attempt == 2:
                raise DDNSError('HTTPS 连接失败或超时，请检查网络与系统时间。') from None
            delay = 2 ** attempt
        time.sleep(delay)
    raise DDNSError('HTTPS 请求未完成。')


def request_text(url: str) -> str:
    try:
        return _request('GET', url).decode('utf-8').strip()
    except UnicodeError:
        raise DDNSError('服务器返回了无法识别的文本。') from None


def request_json(method: str, url: str, token: str | None = None, payload: dict | None = None) -> dict:
    try:
        data = json.loads(_request(method, url, token, payload))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, UnicodeError):
        raise DDNSError('服务器返回了无效的 JSON 数据。') from None


def validate_ipv4(value: str) -> str:
    try:
        address = ipaddress.IPv4Address(value.strip())
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise ValueError()
        return str(address)
    except (ValueError, AttributeError):
        raise DDNSError('未检测到有效的公网 IPv4 地址。') from None


def detect_ipv4() -> str:
    failures = []
    for source in IP_SOURCES:
        try:
            return validate_ipv4(request_text(source))
        except DDNSError as error:
            failures.append(f'{urlsplit(source).hostname}：{error}')
    raise DDNSError('所有公网 IPv4 查询源均失败；未修改 DNS。' + '；'.join(failures))
