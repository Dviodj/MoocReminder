import tempfile
import tkinter as tk
import unittest

from browser import ScanResult
from core import Course, COURSE_URL, parse_assignment
from storage import Store
from gui import App


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, Store(self.temp.name), auto_scan=False)

    def tearDown(self):
        self.app.shutdown()
        self.app.worker.join(timeout=3)
        self.root.destroy()
        self.temp.cleanup()

    def test_failed_scan_keeps_visible_cached_homework(self):
        course = Course('概率论', COURSE_URL)
        item = parse_assignment(dict(name='第2章作业', deadline='2026/10/18 23:30'), course)
        self.app.consume_result(ScanResult(items=[item], courses=[course], successful_urls=[COURSE_URL]))
        self.assertEqual(len(self.app.tree.get_children()), 1)
        self.app.consume_result(ScanResult(errors=['登录失效']))
        self.assertEqual(len(self.app.tree.get_children()), 1)
        self.assertIn('失败', self.app.status.get())

    def test_manual_completion_is_visible_and_restorable(self):
        course = Course('概率论', COURSE_URL)
        item = parse_assignment(dict(name='第2章作业', deadline='2026/10/18 23:30'), course)
        self.app.consume_result(ScanResult(items=[item], courses=[course], successful_urls=[COURSE_URL]))
        self.app.tree.selection_set(item.uid)
        self.app.toggle_completed()
        self.assertEqual(self.app.store.active_items(), [])
        self.assertIn('本地已完成', self.app.tree.item(item.uid)['values'])
        self.app.tree.selection_set(item.uid)
        self.app.toggle_completed()
        self.assertEqual(len(self.app.store.active_items()), 1)

    def test_old_account_result_cannot_repopulate_new_account_cache(self):
        course = Course('概率论', COURSE_URL)
        item = parse_assignment(dict(name='第2章作业', deadline='2026/10/18 23:30'), course)
        settings = dict(self.app.store.data['settings'], home_url='https://www.icourse163.org/home.htm?userId=123456#/home/course')
        self.app.store.update_settings(settings)
        self.app.consume_result(ScanResult(items=[item], courses=[course], successful_urls=[COURSE_URL], account_id='0'))
        self.assertEqual(self.app.store.items(), [])
        self.assertEqual(len(self.app.tree.get_children()), 0)


if __name__ == '__main__':
    unittest.main()
