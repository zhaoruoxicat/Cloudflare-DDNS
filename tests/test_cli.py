from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cf_ddns import cli
from cf_ddns.config import load_config, save_config
from test_config import config


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in [('CONFIG_PATH', self.root/'config/config.json'), ('TIMER_DROPIN', self.root/'timer.d/interval.conf'), ('STATE_DIR', self.root/'state')]:
            mock = patch.object(cli, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.commands = []
        def systemctl(*args, **kwargs):
            self.commands.append(args)
            return subprocess.CompletedProcess(args, 1 if args[0] == 'is-enabled' else 0, stdout='disabled\n', stderr='')
        mock = patch.object(cli, 'systemctl', side_effect=systemctl)
        mock.start()
        self.addCleanup(mock.stop)
        mock = patch('cf_ddns.cli.os.geteuid', return_value=0)
        mock.start()
        self.addCleanup(mock.stop)

    def run_cli(self, arguments):
        with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
            result = cli.main(arguments)
        return result, out.getvalue()+err.getvalue()

    def test_noninteractive_unconfigured_never_prompts(self):
        with patch('cf_ddns.cli.sys.stdin.isatty', return_value=False), patch('builtins.input', side_effect=AssertionError('must not prompt')):
            code, text = self.run_cli([])
        self.assertEqual(code, 1)
        self.assertIn('configure', text)

    def test_first_run_configures_with_hidden_token(self):
        with patch('cf_ddns.cli.sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['example.com', 'vpn.example.com', '300', 'n']), patch('cf_ddns.cli.getpass.getpass', return_value='test-token-do-not-log'):
            code, text = self.run_cli([])
        self.assertEqual(code, 0)
        self.assertEqual(load_config(cli.CONFIG_PATH), config())
        self.assertNotIn('test-token-do-not-log', text)
        self.assertIn('OnUnitInactiveSec=300s', cli.TIMER_DROPIN.read_text())
        self.assertIn(('disable', '--now', 'cf-ddns.timer'), self.commands)

    def test_cancel_keeps_old_configuration(self):
        save_config(cli.CONFIG_PATH, config())
        before = cli.CONFIG_PATH.read_bytes()
        with patch('cf_ddns.cli.sys.stdin.isatty', return_value=True), patch('cf_ddns.cli.getpass.getpass', side_effect=KeyboardInterrupt):
            code, text = self.run_cli(['configure'])
        self.assertEqual(code, 130)
        self.assertEqual(cli.CONFIG_PATH.read_bytes(), before)
        self.assertFalse(cli.TIMER_DROPIN.exists())

    def test_edit_keeps_token_and_retries_bad_interval(self):
        save_config(cli.CONFIG_PATH, config())
        with patch('cf_ddns.cli.sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['', '', 'bad', '30', '600', 'n']), patch('cf_ddns.cli.getpass.getpass', return_value=''):
            code, text = self.run_cli(['configure'])
        self.assertEqual(code, 0)
        self.assertEqual(load_config(cli.CONFIG_PATH)['token'], 'test-token-do-not-log')
        self.assertEqual(load_config(cli.CONFIG_PATH)['interval'], 600)
        self.assertIn('OnUnitInactiveSec=600s', cli.TIMER_DROPIN.read_text())
        self.assertNotIn('test-token-do-not-log', text)

    def test_enable_and_disable_use_timer_and_immediate_update(self):
        save_config(cli.CONFIG_PATH, config())
        self.assertEqual(self.run_cli(['enable'])[0], 0)
        self.assertIn(('enable', '--now', 'cf-ddns.timer'), self.commands)
        self.assertIn(('start', 'cf-ddns.service'), self.commands)
        self.assertEqual(self.run_cli(['disable'])[0], 0)
        self.assertIn(('disable', '--now', 'cf-ddns.timer'), self.commands)

    def test_update_validates_configuration_before_starting(self):
        code, text = self.run_cli(['update'])
        self.assertEqual(code, 1)
        self.assertNotIn(('start', 'cf-ddns.service'), self.commands)
        save_config(cli.CONFIG_PATH, config())
        self.assertEqual(self.run_cli(['update'])[0], 0)
        self.assertIn(('start', 'cf-ddns.service'), self.commands)

    def test_status_hides_token_and_worker_needs_systemd(self):
        save_config(cli.CONFIG_PATH, config())
        self.assertEqual(self.run_cli(['status'])[0], 0)
        with patch.dict('os.environ', {}, clear=True):
            code, text = self.run_cli(['worker'])
        self.assertEqual(code, 1)
        self.assertNotIn('test-token-do-not-log', text)

    def test_menu_can_exit_and_requires_root_for_config(self):
        save_config(cli.CONFIG_PATH, config())
        with patch('cf_ddns.cli.sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['0']):
            self.assertEqual(self.run_cli([])[0], 0)
        with patch('cf_ddns.cli.os.geteuid', return_value=1000):
            code, text = self.run_cli(['configure'])
        self.assertEqual(code, 1)
        self.assertIn('sudo', text)

    def test_corrupt_config_can_be_replaced_and_cancel_preserves_it(self):
        cli.CONFIG_PATH.parent.mkdir(parents=True)
        cli.CONFIG_PATH.write_text('{')
        with patch('cf_ddns.cli.sys.stdin.isatty', return_value=True), patch('cf_ddns.cli.getpass.getpass', side_effect=KeyboardInterrupt):
            code, text = self.run_cli(['configure'])
        self.assertEqual(code, 130)
        self.assertEqual(cli.CONFIG_PATH.read_text(), '{')
        with patch('cf_ddns.cli.sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['example.com', 'vpn.example.com', '300', 'n']), patch('cf_ddns.cli.getpass.getpass', return_value='test-token-do-not-log'):
            code, text = self.run_cli(['configure'])
        self.assertEqual(code, 0)
        self.assertEqual(load_config(cli.CONFIG_PATH), config())
        self.assertNotIn('test-token-do-not-log', text)
