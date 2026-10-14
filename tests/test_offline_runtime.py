import json
from pathlib import Path
import tempfile
import unittest
from source_launcher import bundled_environment

ENGINE_FILES = ('PySide6/QtWidgets.pyd', 'PySide6/QtWebEngineCore.pyd',
                'PySide6/QtWebEngineWidgets.pyd', 'PySide6/QtWebEngineProcess.exe')


def make_bundle(root, folder):
    """A bundle shaped like the portable package: interpreter plus Qt wheels."""
    bundle = root/folder
    site = bundle/'python/Lib/site-packages'
    (bundle/'python').mkdir(parents=True, exist_ok=True)
    (bundle/'python/python.exe').write_bytes(b'fixture')
    for name in ENGINE_FILES:
        path = site/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
    (bundle/'bundle.json').write_text(json.dumps(dict(
        platform='win_amd64', python_version='3.13.9', python_tags=['cp313'])))
    return bundle


class OfflineRuntimeTests(unittest.TestCase):
    def test_bundle_provides_a_usable_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); bundle = make_bundle(root, '.runtime-bundle')
            site = bundle/'python/Lib/site-packages'
            env = bundled_environment(root, [3, 13, 9])
            self.assertIn(str(site), env['PYTHONPATH'])
            self.assertEqual(env['QT_PLUGIN_PATH'], str(site/'PySide6/plugins'))
            self.assertEqual(env['QT_QPA_PLATFORM_PLUGIN_PATH'], str(site/'PySide6/plugins/platforms'))
            self.assertIsNone(bundled_environment(root, [3, 12, 0]), '其他 Python 版本应被拒绝')

    def test_a_bundle_without_the_interpreter_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); bundle = make_bundle(root, '.runtime-bundle')
            (bundle/'python/python.exe').unlink()
            self.assertIsNone(bundled_environment(root, [3, 13, 9]))

    def test_a_bundle_without_qtwebengine_is_refused(self):
        # Qt alone would import, but nothing could render the MOOC pages.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); bundle = make_bundle(root, '.runtime-bundle')
            (bundle/'python/Lib/site-packages/PySide6/QtWebEngineProcess.exe').unlink()
            self.assertIsNone(bundled_environment(root, [3, 13, 9]))

    def test_current_build_layout_is_read_as_well_as_legacy_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); bundle = make_bundle(root, '.build/runtime-bundle')
            env = bundled_environment(root, [3, 13, 9])
            self.assertIn(str(bundle/'python/Lib/site-packages'), env['PYTHONPATH'])
