import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import bundle_runtime
import package_source

TRACKED = ['version.py', 'package_source.py']


class BundleTrimTests(unittest.TestCase):
    """The bundle keeps every Qt library and drops only unused bindings.

    Qt splits its libraries across wheels, and QtWebEngineCore links against
    DLLs that ship in PySide6-Addons. Removing those libraries breaks the
    embedded browser, so trimming must never be based on "the program does not
    import this module".
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.site = self.root/'PySide6'
        self.site.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_bindings_are_dropped_but_libraries_are_kept(self):
        (self.site/'QtCharts.pyd').write_bytes(b'x')
        (self.site/'QtCharts.pyi').write_text('x')
        (self.site/'Qt6Charts.dll').write_bytes(b'x')
        (self.site/'QtWebEngineCore.pyd').write_bytes(b'x')
        (self.site/'Qt6Positioning.dll').write_bytes(b'x')
        bundle_runtime.trim_qt(self.root)
        self.assertFalse((self.site/'QtCharts.pyd').exists())
        self.assertFalse((self.site/'QtCharts.pyi').exists())
        self.assertTrue((self.site/'Qt6Charts.dll').exists(), 'Qt 库不能删')
        self.assertTrue((self.site/'Qt6Positioning.dll').exists())
        self.assertTrue((self.site/'QtWebEngineCore.pyd').exists(), '用到的绑定必须保留')

    def test_verification_rejects_a_bundle_without_the_engine(self):
        for name in ('QtCore.pyd', 'QtGui.pyd', 'QtWidgets.pyd', 'QtWebEngineCore.pyd',
                     'QtWebEngineWidgets.pyd', 'QtWebEngineProcess.exe', 'Qt6WebEngineCore.dll',
                     'Qt6Positioning.dll', 'Qt6WebChannel.dll'):
            (self.site/name).write_bytes(b'x')
        bundle_runtime.verify_qt(self.root)  # 完整时应通过
        (self.site/'Qt6WebChannel.dll').unlink()
        with self.assertRaises(RuntimeError) as error:
            bundle_runtime.verify_qt(self.root)
        self.assertIn('Qt6WebChannel.dll', str(error.exception))


class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'version.py').write_text("APP_NAME = 'MoocReminder'\nVERSION = 'v9.9.9'\n", encoding='utf-8')
        (self.root/'package_source.py').write_text('# fixture\n', encoding='utf-8')
        (self.root/'licenses').mkdir()
        (self.root/'licenses/LGPL-3.0.txt').write_text('LGPL fixture\n', encoding='utf-8')
        (self.root/'licenses/GPL-3.0.txt').write_text('GPL fixture\n', encoding='utf-8')
        (self.root/'THIRD-PARTY-NOTICES.md').write_text('notices fixture\n', encoding='utf-8')
        self.bundle = self.root/'.build/runtime-bundle'
        for name, payload in [('bundle.json', json.dumps({'platform': 'win_amd64', 'python_tags': ['cp313']})),
                              ('python/python.exe', 'binary'),
                              ('python/LICENSE.txt', 'psf'),
                              ('python/Lib/site-packages/PySide6/QtWidgets.pyd', 'binary'),
                              ('python/Lib/site-packages/pyside6_essentials-6.12.0.dist-info/licenses/LicenseRef-Qt-Commercial.txt', 'qt'),
                              ('python/Lib/site-packages/shiboken6-6.12.0.dist-info/licenses/LicenseRef-Qt-Commercial.txt', 'qt')]:
            path = self.bundle/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def build(self, with_runtime=True):
        with patch.object(package_source, 'ROOT', self.root), \
             patch.object(package_source, 'VERSION', 'v9.9.9'), \
             patch.object(package_source.subprocess, 'run', return_value=type('Result', (), {'stdout': '\0'.join(TRACKED).encode() + b'\0'})()):
            return package_source.package(with_runtime)

    def test_offline_archive_mirrors_the_published_layout(self):
        archive_path = self.build(with_runtime=True)
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        prefix = 'MoocReminder-v9.9.9/'
        # The launcher looks for the runtime bundle at the package root, so the
        # local .build/ organising folder must never appear inside the archive.
        self.assertIn(prefix + 'runtime-bundle/bundle.json', names)
        self.assertIn(prefix + 'runtime-bundle/python/python.exe', names)
        self.assertIn(prefix + 'runtime-bundle/python/Lib/site-packages/PySide6/QtWidgets.pyd', names)
        self.assertFalse([name for name in names if '.build/' in name])
        self.assertFalse([name for name in names if name.startswith(prefix + 'runtime-bundle/wheel-cache/')])

    def test_license_texts_and_notices_travel_with_the_offline_package(self):
        archive_path = self.build(with_runtime=True)
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        prefix = 'MoocReminder-v9.9.9/'
        self.assertIn(prefix + 'licenses/LGPL-3.0.txt', names)
        self.assertIn(prefix + 'THIRD-PARTY-NOTICES.md', names)
        self.assertIn(prefix + 'runtime-bundle/python/LICENSE.txt', names)
        self.assertIn(prefix + 'runtime-bundle/python/Lib/site-packages/shiboken6-6.12.0.dist-info'
                              '/licenses/LicenseRef-Qt-Commercial.txt', names)

    def test_source_archive_excludes_the_runtime_bundle(self):
        archive_path = self.build(with_runtime=False)
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        self.assertIn('MoocReminder-v9.9.9/version.py', names)
        self.assertFalse([name for name in names if 'runtime-bundle' in name])
        self.assertNotIn('MoocReminder-v9.9.9/THIRD-PARTY-NOTICES.md', names)

    def test_missing_component_license_blocks_the_release(self):
        (self.bundle/'python/Lib/site-packages/shiboken6-6.12.0.dist-info'
                    '/licenses/LicenseRef-Qt-Commercial.txt').unlink()
        with self.assertRaises(RuntimeError) as error:
            self.build(with_runtime=True)
        self.assertIn('shiboken6-6.12.0.dist-info', str(error.exception))

    def test_missing_interpreter_license_blocks_the_release(self):
        (self.bundle/'python/LICENSE.txt').unlink()
        with self.assertRaises(RuntimeError) as error:
            self.build(with_runtime=True)
        self.assertIn('license', str(error.exception).lower())


if __name__ == '__main__':
    unittest.main()
