import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cf_ddns.config import DDNSError, normalize_domain, validate_config, load_config, save_config


def config(**kwargs):
    return dict(token='test-token-do-not-log', zone='example.com', record='vpn.example.com', interval=300, **kwargs)


class ConfigTests(unittest.TestCase):
    def test_normalizes_domain_and_idna(self):
        self.assertEqual(normalize_domain(' VPN.Example.COM. '), 'vpn.example.com')
        self.assertEqual(normalize_domain('bücher.example.com'), 'xn--bcher-kva.example.com')

    def test_rejects_invalid_domains_and_zone_boundaries(self):
        for domain in ('*.example.com', '-x.example.com', 'a..com', 'http://example.com', 'a_b.com'):
            with self.subTest(domain=domain), self.assertRaises(DDNSError):
                normalize_domain(domain)
        for record in ('evil-example.com', 'example.com.evil.org'):
            data = config()
            data['record'] = record
            with self.assertRaises(DDNSError):
                validate_config(data)

    def test_validates_interval_and_token_without_disclosure(self):
        for interval in (59, 86401, True, '300'):
            data = config()
            data['interval'] = interval
            with self.assertRaises(DDNSError):
                validate_config(data)
        for interval in (60, 86400):
            data = config()
            data['interval'] = interval
            self.assertEqual(validate_config(data)['interval'], interval)
        data = config()
        data['token'] = 'secret\nvalue'
        with self.assertRaises(DDNSError) as raised:
            validate_config(data)
        self.assertNotIn('secret', str(raised.exception))

    def test_atomic_save_permissions_and_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config' / 'config.json'
            save_config(path, config())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(load_config(path)['record'], 'vpn.example.com')
            before = path.read_bytes()
            with patch('os.replace', side_effect=OSError('write failed')):
                with self.assertRaises(DDNSError):
                    save_config(path, config())
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_missing_malformed_and_invalid_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            for text in (None, '{', '[]', '{"token":"do-not-echo"}'):
                if text is not None:
                    path.write_text(text)
                with self.subTest(text=text), self.assertRaises(DDNSError) as raised:
                    load_config(path)
                self.assertNotIn('do-not-echo', str(raised.exception))
