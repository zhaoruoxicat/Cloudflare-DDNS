import io
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from cf_ddns.config import DDNSError
from cf_ddns.network import detect_ipv4, request_json, request_text, validate_ipv4, NoRedirect


class Response(io.BytesIO):
    status = 200


class NetworkTests(unittest.TestCase):
    def test_only_global_ipv4(self):
        self.assertEqual(validate_ipv4(' 8.8.8.8\n'), '8.8.8.8')
        for value in ('192.168.1.1', '100.64.1.1', '127.0.0.1', '169.254.1.1', '192.0.2.1', '224.0.0.1', '::1', '<html>'):
            with self.subTest(value=value), self.assertRaises(DDNSError):
                validate_ipv4(value)

    def test_ip_fallback_on_invalid_primary(self):
        with patch('cf_ddns.network.request_text', side_effect=['192.168.1.1', '8.8.4.4']):
            self.assertEqual(detect_ipv4(), '8.8.4.4')

    def test_ip_all_sources_fail(self):
        with patch('cf_ddns.network.request_text', side_effect=DDNSError('unavailable')):
            with self.assertRaises(DDNSError):
                detect_ipv4()

    def test_network_disables_proxy_and_preserves_auth(self):
        seen = {}
        class Opener:
            def open(self, req, timeout):
                seen['authorization'] = req.get_header('Authorization')
                seen['timeout'] = timeout
                return Response(b'{"success":true,"result":[]}')
        def builder(*handlers):
            seen['proxies'] = handlers[0].proxies
            return Opener()
        with patch('cf_ddns.network.build_opener', side_effect=builder):
            self.assertEqual(request_json('GET', 'https://api.cloudflare.com/client/v4/zones', 'secret')['result'], [])
        self.assertEqual(seen['proxies'], {})
        self.assertEqual(seen['authorization'], 'Bearer secret')
        self.assertGreater(seen['timeout'], 0)

    def test_retry_transient_errors_and_no_secret_in_failure(self):
        opener = unittest.mock.Mock()
        opener.open.side_effect = [URLError('secret'), HTTPError('https://x', 503, 'secret', {}, None), Response(b'{}')]
        with patch('cf_ddns.network.build_opener', return_value=opener), patch('cf_ddns.network.time.sleep'):
            self.assertEqual(request_json('GET', 'https://api.cloudflare.com/client/v4/zones', 'secret'), {})
        for code, attempts in ((401, 1), (403, 1), (429, 3), (500, 3)):
            opener.open.reset_mock()
            opener.open.side_effect = HTTPError('https://x', code, 'secret', {}, None)
            with patch('cf_ddns.network.build_opener', return_value=opener), patch('cf_ddns.network.time.sleep'):
                with self.assertRaises(DDNSError) as raised:
                    request_json('GET', 'https://api.cloudflare.com/client/v4/zones', 'secret')
            self.assertNotIn('secret', str(raised.exception))
            self.assertEqual(opener.open.call_count, attempts)

    def test_rejects_malformed_and_oversized_responses(self):
        for body in (b'not json secret', b'[]', b'x' * (1024 * 1024 + 1)):
            opener = unittest.mock.Mock()
            opener.open.return_value = Response(body)
            with patch('cf_ddns.network.build_opener', return_value=opener):
                with self.assertRaises(DDNSError) as raised:
                    request_json('GET', 'https://api.cloudflare.com/client/v4/zones', 'secret')
                self.assertNotIn('secret', str(raised.exception))

    def test_redirect_and_insecure_url_rejected(self):
        with self.assertRaises(DDNSError):
            NoRedirect().redirect_request(None, None, 302, 'Found', {}, 'https://evil.example')
        with self.assertRaises(DDNSError):
            request_text('http://example.com')

    def test_uses_reachable_fallback_when_initial_sources_are_blocked(self):
        requested = []
        def query(url):
            requested.append(url)
            if url == 'https://ip.3322.net':
                return '8.8.4.4\n'
            raise DDNSError('HTTPS 连接失败或超时，请检查网络与系统时间。')
        with patch('cf_ddns.network.request_text', side_effect=query):
            self.assertEqual(detect_ipv4(), '8.8.4.4')
        self.assertIn('https://ip.3322.net', requested)

    def test_failed_sources_are_named_in_diagnostic(self):
        with patch('cf_ddns.network.request_text', side_effect=DDNSError('HTTPS 连接失败或超时。')):
            with self.assertRaises(DDNSError) as raised:
                detect_ipv4()
        message = str(raised.exception)
        self.assertIn('api4.ipify.org', message)
        self.assertIn('ipv4.icanhazip.com', message)
        self.assertIn('ip.3322.net', message)
        self.assertIn('未修改 DNS', message)
