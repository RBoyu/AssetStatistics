"""Verify installed uv entry points and ledger paths without writing user data."""
import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from assetbook import __version__
from assetbook.cli import default_data_dir, main
from assetbook.storage import Store

TEST_TMP = Path(__file__).resolve().parent / '.tmp'
TEST_TMP.mkdir(exist_ok=True)


class CliTests(unittest.TestCase):
    def test_other_working_directory_keeps_project_ledger(self):
        project = Path(__file__).resolve().parents[1]
        db = project / 'assets_v4.db'
        before = hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else None
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as cwd:
            result = subprocess.run([sys.executable, '-B', '-m', 'assetbook', '--check'], cwd=cwd, capture_output=True, text=True, check=True)
            self.assertIn(f'Data directory: {project}', result.stdout)
            self.assertFalse((Path(cwd) / 'assets_v4.db').exists())
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest() if db.exists() else None, before)

    def test_explicit_check_does_not_create_directory_or_database(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as cwd:
            base = Path(cwd) / 'nonexistent-ledger'
            output = io.StringIO()
            with redirect_stdout(output):
                main(['--check', '--data-dir', str(base)])
            self.assertIn(f'Data directory: {base}', output.getvalue())
            self.assertFalse(base.exists())

    def test_explicit_existing_ledger_check_is_read_only(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as base:
            store = Store(Path(base), migrate=False)
            store.save(dict(bank='工行', deposit_type='定期1年', amount='10000', deposit_date='2026-01-01', term_months=12))
            store.close()
            db = Path(base) / 'assets_v4.db'
            before = db.read_bytes()
            output = io.StringIO()
            with redirect_stdout(output):
                main(['--check', '--data-dir', base])
            self.assertIn('1 records, readable', output.getvalue())
            self.assertEqual(db.read_bytes(), before)
            self.assertFalse((Path(base) / 'logs').exists())

    def test_wheel_install_uses_user_data_directory(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as base:
            location = Path(base) / 'site-packages' / 'assetbook' / 'cli.py'
            with patch('assetbook.cli.__file__', str(location)), patch('assetbook.cli.sys.platform', 'win32'), patch.dict('os.environ', {'LOCALAPPDATA': base}):
                self.assertEqual(default_data_dir(), Path(base) / 'AssetsStatistics')

    def test_console_entry_and_package_version_agree(self):
        self.assertEqual(version('asset-statistics'), __version__)
        executable = Path(sys.executable).parent / ('assetbook.exe' if sys.platform == 'win32' else 'assetbook')
        result = subprocess.run([str(executable), '--version'], capture_output=True, text=True, check=True)
        self.assertIn(__version__, result.stdout)


if __name__ == '__main__':
    unittest.main()
