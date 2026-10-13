import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from core import Course, parse_deadline, parse_assignment, normalize_course_url, due_reminders
from storage import Store, default_data_dir

TZ = timezone(timedelta(hours=8))


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.course = Course('概率论与数理统计', 'https://www.icourse163.org/learn/EXAMPLE-1000?tid=1000#/learn/testlist')
        self.now = datetime(2026, 10, 18, 21, 30, tzinfo=TZ)

    def item(self, **updates):
        row = dict(name='第2章作业', deadline='截止时间：2026/10/18 23:30', status='', text='作业提交阶段 截止时间：2026/10/18 23:30 学生互评 截止时间：2026/10/25 23:30')
        row.update(updates)
        return parse_assignment(row, self.course)

    def test_parses_both_real_and_chinese_dates_as_beijing_time(self):
        expected = datetime(2026, 10, 18, 23, 30, tzinfo=TZ)
        for s in ['截止时间：2026/10/18 23:30', '2026年10月18日 23:30', '2026-10-18 23:30:00']:
            self.assertEqual(parse_deadline(s), expected)

    def test_does_not_invent_time_for_missing_or_invalid_date(self):
        for s in ['', '2026/10/18', '2026/02/31 23:30', '互评截止时间：待公布']:
            self.assertIsNone(parse_deadline(s))

    def test_submission_deadline_wins_over_review_deadline(self):
        item = self.item()
        self.assertEqual(item.deadline, datetime(2026, 10, 18, 23, 30, tzinfo=TZ))
        self.assertEqual(item.status, 'unknown')

    def test_description_does_not_mark_homework_submitted(self):
        self.assertEqual(self.item(text='提交作业后不参与互评将影响成绩').status, 'unknown')
        self.assertEqual(self.item(status='未提交').status, 'pending')
        self.assertEqual(self.item(status='已提交').status, 'completed')

    def test_test_named_as_test_is_still_a_quiz(self):
        self.assertEqual(self.item(name='第四周 振动单元测试', kind='测验').kind, '测验')

    def test_rejects_external_course_link_and_preserves_term_id(self):
        self.assertEqual(normalize_course_url('https://www.icourse163.org/course/EXAMPLE-1000?tid=1000'), self.course.url)
        for url in ['https://evil.example/learn/NEU-1?tid=123', 'https://www.icourse163.org.evil.example/learn/NEU-1?tid=123', 'javascript:alert(1)', 'https://www.icourse163.org/course/NEU-1']:
            self.assertIsNone(normalize_course_url(url))

    def test_late_first_run_issues_only_nearest_window_and_deduplicates(self):
        state = {}
        alerts = due_reminders([self.item()], [72, 24, 3], state, self.now)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][1], 3)
        self.assertEqual(due_reminders([self.item()], [72, 24, 3], state, self.now), [])

    def test_more_urgent_window_still_reminds(self):
        state = {}
        self.assertEqual(due_reminders([self.item()], [72, 24, 3], state, self.now - timedelta(hours=30))[0][1], 72)
        self.assertEqual(due_reminders([self.item()], [72, 24, 3], state, self.now - timedelta(hours=10))[0][1], 24)
        self.assertEqual(due_reminders([self.item()], [72, 24, 3], state, self.now)[0][1], 3)

    def test_completed_and_overdue_items_do_not_alert(self):
        self.assertEqual(due_reminders([self.item(status='已提交')], [72, 24, 3], {}, self.now), [])
        self.assertEqual(due_reminders([self.item()], [72, 24, 3], {}, self.now + timedelta(hours=3)), [])

    def test_deadline_change_gets_new_reminder(self):
        state = {}
        due_reminders([self.item()], [72, 24, 3], state, self.now)
        updated = self.item(deadline='截止时间：2026/10/19 23:30')
        self.assertEqual(len(due_reminders([updated], [72, 24, 3], state, self.now)), 1)

    def test_storage_retains_failed_course_and_persists_reminders(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            store.merge_scan([self.item()], [self.course.url])
            store.merge_scan([], [])  # a failed scan has no successful course URLs
            self.assertEqual(len(store.items()), 1)
            due_reminders(store.items(), [72, 24, 3], store.data['reminders'], self.now)
            store.save()
            restarted = Store(folder)
            self.assertEqual(due_reminders(restarted.items(), [72, 24, 3], restarted.data['reminders'], self.now), [])
            restarted.merge_scan([], [self.course.url])  # confirmed empty replaces cache
            self.assertEqual(restarted.items(), [])

    def test_local_completion_only_suppresses_same_deadline(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            original = self.item()
            store.merge_scan([original], [self.course.url])
            store.mark_completed(original, True)
            self.assertEqual(store.active_items(), [])
            store.merge_scan([self.item(deadline='2026/10/19 23:30')], [self.course.url])
            self.assertEqual(len(store.active_items()), 1)

    def test_unwritable_appdata_falls_back_to_portable_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            blocked = Path(folder) / 'blocked'
            blocked.write_text('not a directory')
            with patch.dict('os.environ', {'LOCALAPPDATA': str(blocked)}):
                self.assertEqual(default_data_dir(portable_base=Path(folder)), Path(folder) / 'data')

    def test_appdata_permission_probe_does_not_retry_denied_file_names(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict('os.environ', {'LOCALAPPDATA': folder}), patch('storage.os.open', side_effect=PermissionError('denied')) as create, patch('tempfile.TMP_MAX', 4):
                self.assertEqual(default_data_dir(portable_base=Path(folder)), Path(folder)/'data')
                self.assertEqual(create.call_count, 1)

    def test_repeated_assignment_names_are_both_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            first = self.item(name='章末作业', duplicate_ordinal=1)
            second = self.item(name='章末作业', deadline='2026/10/19 23:30', duplicate_ordinal=2)
            store.merge_scan([first, second], [self.course.url])
            self.assertEqual(len(store.items()), 2)

    def test_changing_account_does_not_reuse_completion_or_reminders(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            original = self.item()
            store.merge_scan([original], [self.course.url])
            store.mark_completed(original, True)
            due_reminders([original], [72, 24, 3], store.data['reminders'], self.now)
            settings = dict(store.data['settings'], home_url='https://www.icourse163.org/home.htm?userId=123456#/home/course')
            self.assertTrue(store.update_settings(settings))
            self.assertEqual(store.items(), [])
            self.assertEqual(store.data['completed'], [])
            self.assertEqual(store.data['reminders'], {})

    def test_invalid_saved_settings_are_backed_up_and_defaults_remain_usable(self):
        for bad in [[], {'interval_minutes': 'abc'}, {'reminder_hours': []}]:
            with tempfile.TemporaryDirectory() as folder:
                (Path(folder) / 'data.json').write_text(json.dumps({'settings': bad, 'assignments': []}))
                store = Store(folder)
                self.assertTrue(store.warning)
                self.assertEqual(store.data['settings']['interval_minutes'], 30)
                self.assertTrue((Path(folder) / 'data.corrupt.backup.json').exists())

    def test_invalid_saved_nested_values_are_restored_without_startup_crash(self):
        for bad in [{'completed': [[]]}, {'reminders': {'id': [None]}}, {'courses': [{'name': [], 'url': 123}]}, {'settings': {'home_url': 123}}]:
            with tempfile.TemporaryDirectory() as folder:
                (Path(folder) / 'data.json').write_text(json.dumps(bad))
                store = Store(folder)
                self.assertTrue(store.warning)
                self.assertEqual(store.active_items(), [])


if __name__ == '__main__':
    unittest.main()
