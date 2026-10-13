"""Verify displayed scan updates on the actual desktop widgets."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qt_bootstrap import prepare_qt
prepare_qt(Path(__file__).resolve().parents[1] / 'qt-test-data')
from PySide6.QtWidgets import QApplication
from qt_desktop import Window
from core import Course, COURSE_URL, parse_assignment, BEIJING
from datetime import datetime
from storage import Store


class QtGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_changed_deadline_and_status_replace_visible_old_values(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = Store(folder)
            course = Course('概率论', COURSE_URL)
            first = parse_assignment(dict(name='第2章作业', deadline='2026-10-18 23:30', status=''), course)
            second = parse_assignment(dict(name='第2章作业', deadline='2026-10-18 23:45', status='未提交'), course)
            store.merge_scan([first], [COURSE_URL])
            with patch('portal.now_beijing', return_value=datetime(2026,10,13,12,tzinfo=BEIJING)):
                window = Window(store)
                try:
                    self.assertEqual(window.table.item(0,3).text(), '10月18日 23:30')
                    self.assertEqual(window.table.item(0,5).text(), '请核对')
                    store.merge_scan([second], [COURSE_URL])
                    window.refresh()
                    self.assertEqual(window.table.item(0,3).text(), '10月18日 23:45')
                    self.assertEqual(window.table.item(0,5).text(), '未提交')
                finally:
                    window.controller.stop()
                    window.controller.worker.join(timeout=2)
                    window.close()


if __name__ == '__main__':
    unittest.main()
