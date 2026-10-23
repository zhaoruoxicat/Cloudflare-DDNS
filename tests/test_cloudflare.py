import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs

from cf_ddns.config import DDNSError
from cf_ddns.cloudflare import sync_record
from test_config import config

ZONE = 'a' * 32
RECORD = 'b' * 32


def record(**changes):
    data = dict(id=RECORD, type='A', name='vpn.example.com', content='8.8.8.8', proxied=False, ttl=1, comment='keep me', tags=['owner:home'])
    data.update(changes)
    return data


class API:
    def __init__(self, records, zones=None):
        self.records = records
        self.zones = zones if zones is not None else [dict(id=ZONE, name='example.com')]
        self.writes = []

    def __call__(self, method, url, token=None, payload=None):
        assert token == 'test-token-do-not-log'
        parsed = urlsplit(url)
        if method == 'GET' and parsed.path.endswith('/zones'):
            assert parse_qs(parsed.query)['name'] == ['example.com']
            return dict(success=True, result=self.zones)
        if method == 'GET' and parsed.path.endswith('/dns_records'):
            assert parse_qs(parsed.query)['name'] == ['vpn.example.com']
            return dict(success=True, result=self.records, result_info=dict(page=1, total_pages=1))
        self.writes.append((method, parsed.path, payload))
        return dict(success=True, result=record(**payload))


class CloudflareTests(unittest.TestCase):
    def run_sync(self, api):
        with patch('cf_ddns.cloudflare.request_json', side_effect=api):
            return sync_record(config(), '8.8.8.8')

    def test_missing_record_is_created_dns_only(self):
        api = API([])
        self.assertEqual(self.run_sync(api), 'created')
        self.assertEqual(api.writes, [('POST', f'/client/v4/zones/{ZONE}/dns_records', dict(type='A', name='vpn.example.com', content='8.8.8.8', proxied=False, ttl=1))])

    def test_update_uses_patch_and_preserves_metadata(self):
        api = API([record(content='1.1.1.1')])
        self.assertEqual(self.run_sync(api), 'updated')
        self.assertEqual(api.writes, [('PATCH', f'/client/v4/zones/{ZONE}/dns_records/{RECORD}', dict(content='8.8.8.8', proxied=False, ttl=1))])

    def test_unchanged_skips_write(self):
        api = API([record()])
        self.assertEqual(self.run_sync(api), 'unchanged')
        self.assertEqual(api.writes, [])

    def test_repairs_proxy_or_ttl_without_ip_change(self):
        for changes in (dict(proxied=True), dict(ttl=300)):
            api = API([record(**changes)])
            self.assertEqual(self.run_sync(api), 'updated')
            self.assertEqual(api.writes[0][2], dict(content='8.8.8.8', proxied=False, ttl=1))

    def test_conflicts_do_not_write(self):
        for records in ([record(), record(id='c'*32)], [record(type='CNAME')], [record(type='CNAME'), record()]):
            api = API(records)
            with self.assertRaises(DDNSError):
                self.run_sync(api)
            self.assertEqual(api.writes, [])

    def test_zone_missing_and_bad_api_response_fail_safely(self):
        for api in (API([], zones=[]), API([], zones=[dict(id='wrong', name='example.com')])):
            with self.assertRaises(DDNSError):
                self.run_sync(api)
        for response in ({'success':False, 'errors':[{'code':9109,'message':'test-token-do-not-log'}]}, {'success':True,'result':None}):
            with patch('cf_ddns.cloudflare.request_json', return_value=response):
                with self.assertRaises(DDNSError) as raised:
                    sync_record(config(), '8.8.8.8')
                self.assertNotIn('test-token-do-not-log', str(raised.exception))

    def test_private_ip_never_reaches_api(self):
        api = API([])
        with self.assertRaises(DDNSError):
            with patch('cf_ddns.cloudflare.request_json', side_effect=AssertionError('API must not be called')):
                sync_record(config(), '192.168.1.1')
