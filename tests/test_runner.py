import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cf_ddns.config import DDNSError, save_config
from cf_ddns.runner import run_update
from test_config import config


class RunnerTests(unittest.TestCase):
    def test_success_and_failure_preserve_last_success_without_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            conf = root / 'config' / 'config.json'
            state = root / 'state'
            save_config(conf, config())
            with patch('cf_ddns.runner.detect_ipv4', return_value='8.8.8.8'), patch('cf_ddns.runner.sync_record', return_value='updated'):
                self.assertEqual(run_update(conf, state), 0)
            before = json.loads((state / 'status.json').read_text())
            self.assertEqual(before['ipv4'], '8.8.8.8')
            self.assertEqual(before['result'], 'updated')
            with patch('cf_ddns.runner.detect_ipv4', side_effect=RuntimeError('test-token-do-not-log')):
                self.assertEqual(run_update(conf, state), 1)
            after = json.loads((state / 'status.json').read_text())
            self.assertEqual(after['last_success'], before['last_success'])
            self.assertEqual(after['result'], 'failed')
            self.assertNotIn('test-token-do-not-log', (state / 'status.json').read_text())
            self.assertEqual((state / 'status.json').stat().st_mode & 0o777, 0o644)

    def test_lock_prevents_concurrent_update(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with (state / 'update.lock').open('w') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch('cf_ddns.runner.detect_ipv4', side_effect=AssertionError('must not query')):
                    self.assertEqual(run_update(state / 'missing.json', state), 1)

    def test_missing_config_records_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.assertEqual(run_update(state / 'missing.json', state), 1)
            data = json.loads((state / 'status.json').read_text())
            self.assertEqual(data['result'], 'failed')
            self.assertNotIn('last_success', data)
