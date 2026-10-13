import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from webview_runtime import select_runtime, extract_sdk, SDK_MEMBERS


class RuntimeTests(unittest.TestCase):
    def test_highest_matching_stable_desktop_runtime(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for family, versions in {'Microsoft.WindowsDesktop.App': ['8.0.23','9.0.12','10.0.0-preview.1'], 'Microsoft.NETCore.App': ['8.0.23','9.0.12']}.items():
                for version in versions:
                    (root/family/version).mkdir(parents=True)
            self.assertEqual(select_runtime([root]), (root, '9.0.12'))

    def test_missing_matching_core_runtime_is_explained(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'Microsoft.WindowsDesktop.App/9.0.12').mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, '.NET'):
                select_runtime([root])

    def test_extract_only_exact_sdk_assets(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            for member in SDK_MEMBERS:
                archive.writestr(member, b'MZ-fixture')
            archive.writestr('../escape.dll', b'bad')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'sdk'
            extract_sdk(stream.getvalue(), target)
            self.assertEqual({p.name for p in target.iterdir()}, set(SDK_MEMBERS.values()))
            self.assertFalse((Path(folder)/'escape.dll').exists())

    def test_missing_sdk_member_leaves_no_partial_install(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(next(iter(SDK_MEMBERS)), b'MZ-fixture')
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'sdk'
            with self.assertRaisesRegex(RuntimeError, 'SDK'):
                extract_sdk(stream.getvalue(), target)
            self.assertFalse(target.exists())
