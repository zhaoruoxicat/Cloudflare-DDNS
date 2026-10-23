from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run(['bash', str(ROOT/'build-deb.sh')], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError('Package build failed: ' + result.stderr)
        cls.deb = ROOT/'dist/cf-ddns_0.1.1_all.deb'

    def test_package_metadata_and_installed_entry(self):
        info = subprocess.check_output(['dpkg-deb', '-f', str(self.deb)], text=True)
        self.assertIn('Package: cf-ddns\n', info)
        self.assertIn('Architecture: all\n', info)
        self.assertIn('python3 (>= 3.11)', info)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            subprocess.run(['dpkg-deb', '-x', str(self.deb), directory], check=True)
            entry = target/'usr/bin/cf-ddns'
            result = subprocess.run([str(entry), '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('configure', result.stdout)
            self.assertTrue((target/'usr/lib/systemd/system/cf-ddns.service').is_file())
            self.assertFalse((target/'etc/cf-ddns/config.json').exists())
            self.assertFalse(list(target.rglob('*.pyc')))

    def test_maintainer_scripts_are_executable_valid_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(['dpkg-deb', '-e', str(self.deb), directory], check=True)
            for name in ('postinst', 'prerm', 'postrm'):
                path = Path(directory)/name
                self.assertTrue(path.stat().st_mode & 0o111)
                result = subprocess.run(['/bin/sh', '-n', str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_archive_files_owned_by_root(self):
        listing = subprocess.check_output(['dpkg-deb', '-c', str(self.deb)], text=True)
        for line in listing.splitlines():
            self.assertEqual(line.split()[1], 'root/root')
