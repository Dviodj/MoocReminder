import json
from pathlib import Path
import tempfile
import unittest
from source_launcher import bundled_environment


class OfflineRuntimeTests(unittest.TestCase):
    def test_bundle_uses_native_module_for_matching_python(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);bundle=root/'.runtime-bundle'
            for name in ('common/PySide6/QtWidgets.pyd','common/pythonnet/__init__.py','native/cp313/_cffi_backend.cp313-win_amd64.pyd'):
                path=bundle/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
            (bundle/'bundle.json').write_text(json.dumps(dict(platform='win_amd64',python_tags=['cp313'])))
            env=bundled_environment(root,[3,13,9])
            self.assertIn(str(bundle/'common'),env['PYTHONPATH'])
            self.assertIn(str(bundle/'native/cp313'),env['PYTHONPATH'])
            self.assertEqual(env['QT_PLUGIN_PATH'],str(bundle/'common/PySide6/plugins'))
            self.assertEqual(env['QT_QPA_PLATFORM_PLUGIN_PATH'],str(bundle/'common/PySide6/plugins/platforms'))
            self.assertIsNone(bundled_environment(root,[3,12,0]))
            (bundle/'native/cp313/_cffi_backend.cp313-win_amd64.pyd').unlink()
            self.assertIsNone(bundled_environment(root,[3,13,9]))
