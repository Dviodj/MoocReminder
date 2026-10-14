import json
import tempfile
import unittest
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from threading import Thread

from storage import Store
from portal import Controller, create_server, ASSETS
from version import VERSION
from core import Assignment, now_beijing
from datetime import timedelta


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.controller = Controller(Store(self.temp.name), auto_scan=False)
        self.server = create_server(self.controller)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.controller.stop()
        self.server.shutdown()
        self.server.server_close()
        self.controller.worker.join(timeout=3)
        self.temp.cleanup()

    def request(self, path, data=None, token=True):
        headers = {'Authorization': 'Bearer ' + self.controller.token} if token else {}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def test_private_state_rejects_missing_token(self):
        with self.assertRaises(HTTPError) as error:
            self.request('/api/state', token=False)
        self.assertEqual(error.exception.code, 401)

    def test_settings_and_pause_remain_responsive(self):
        status, result = self.request('/api/pause', {'paused': True})
        self.assertEqual(status, 200)
        self.assertTrue(self.request('/api/state')[1]['paused'])
        status, result = self.request('/api/settings', {'interval_minutes': 15, 'reminder_hours': [48, 12, 1]})
        self.assertEqual(status, 200)
        self.assertEqual(self.request('/api/state')[1]['settings']['interval_minutes'], 15)

    def test_invalid_settings_do_not_corrupt_saved_values(self):
        with self.assertRaises(HTTPError):
            self.request('/api/settings', {'interval_minutes': 0})
        self.assertEqual(self.controller.store.data['settings']['interval_minutes'], 30)

    def test_test_notification_reaches_frontend_without_blocking_api(self):
        self.request('/api/test-notification', {})
        notices = self.request('/api/state')[1]['notifications']
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0]['test'])

    def test_qt_profile_schedules_a_fresh_check_on_restart(self):
        folder = Path(self.temp.name)
        (folder / 'web-profiles' / '0').mkdir(parents=True)
        resumed = Controller(Store(folder), context_provider=lambda *args: None)
        try:
            self.assertLessEqual(resumed.snapshot()['next_check'], 5)
            self.assertFalse(resumed.snapshot()['connected'])
        finally:
            resumed.stop()
            resumed.worker.join(timeout=3)

    def test_served_page_shows_the_version_from_version_py(self):
        with urlopen(self.base + '/', timeout=3) as response:
            page = response.read().decode('utf-8')
        self.assertIn(f'<b>{VERSION}</b>', page)
        self.assertNotIn('{{VERSION}}', page)
        raw = (ASSETS / 'index.html').read_text(encoding='utf-8')
        self.assertIn('{{VERSION}}', raw)
        self.assertNotIn('<b>v0.1</b>', raw)

    def test_failed_login_is_not_overwritten_by_late_ready(self):
        self.controller.busy = True
        self.controller.job = 'login'
        self.controller.events.put(('error', '网页渲染进程退出（代码 18）'))
        self.controller.events.put(('progress', '请登录'))
        self.controller.events.put(('login_ready', None))
        self.controller.tick()
        state = self.controller.snapshot()
        self.assertEqual(state['job'], 'error')
        self.assertIn('代码 18', state['phase'])
        self.assertFalse(state['busy'])


class AccountSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.controller = Controller(Store(self.temp.name), auto_scan=False,
                                     context_provider=lambda *args: None)

    def tearDown(self):
        self.controller.stop()
        self.controller.worker.join(timeout=3)
        self.temp.cleanup()

    def test_switching_profile_replaces_the_worker_and_clears_leftover_state(self):
        old_worker = self.controller.worker
        self.controller.busy = True
        self.controller.job = 'login'
        self.controller.connected = True
        self.controller.events.put(('progress', '上一个账号的消息'))
        target = Store(self.temp.name)
        target.data['settings']['name'] = '小号'
        self.assertFalse(self.controller.replace_store(target))
        self.assertIsNot(self.controller.worker, old_worker)
        self.assertFalse(old_worker.is_alive())
        self.assertTrue(self.controller.worker.is_alive())
        self.assertEqual(self.controller.store, target)
        state = self.controller.snapshot()
        self.assertFalse(state['busy'])
        self.assertFalse(state['connected'])
        self.assertIn('小号', state['phase'])

    def test_a_profile_with_saved_cookies_schedules_an_immediate_check(self):
        folder = Path(self.temp.name)
        (folder / 'web-profiles' / '0').mkdir(parents=True, exist_ok=True)
        self.assertTrue(self.controller.replace_store(Store(folder)))
        self.assertLessEqual(self.controller.snapshot()['next_check'], 5)

    def test_mailer_is_restarted_for_the_new_account(self):
        target = Store(self.temp.name)
        self.controller.replace_store(target)
        self.assertTrue(self.controller.mailer.thread.is_alive())


class CapturingMailer:
    """Stands in for the delivery queue; records what would be sent."""

    def __init__(self):
        self.queued = []

    def enqueue(self, key, config, mail):
        self.queued.append((key, config, mail))
        return True

    def start(self):
        pass

    def stop(self):
        pass


class EmailSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.controller = Controller(Store(self.temp.name), auto_scan=False)
        self.mailer = CapturingMailer()
        self.controller.mailer = self.mailer
        self.controller.connected = True
        self.enable_email()

    def tearDown(self):
        self.controller.stop()
        self.controller.worker.join(timeout=3)
        self.temp.cleanup()

    def enable_email(self, **overrides):
        config = dict(enabled=True, recipient='me@qq.com', server='smtp.qq.com', port=465,
                      security='ssl', password='授权码', digest_times=[])
        config.update(overrides)
        self.controller.settings({'email': config})
        return config

    def add_assignment(self, uid='u1', hours=20, name='第三章作业'):
        """Store one pending task that crosses the given reminder window."""
        item = Assignment(uid=uid, course='高等数学', course_url='https://www.icourse163.org/learn/X-1?tid=1#/learn/testlist',
                          name=name, kind='作业', deadline=now_beijing() + timedelta(hours=hours),
                          deadline_text='', status='pending', checked_at=now_beijing().isoformat())
        self.controller.store.data['assignments'] = [item.to_dict()]
        return item

    def test_due_reminder_queues_one_email_without_a_popup_duplicate(self):
        item = self.add_assignment(hours=20)
        alerts = [(item, 24)]
        self.assertTrue(self.controller.dispatch_email(alerts, [], self.controller.store.active_items()))
        self.assertEqual(len(self.mailer.queued), 1)
        key, config, mail = self.mailer.queued[0]
        self.assertEqual(key, f'alert|{item.revision}|24')
        self.assertIn('第三章作业', mail.text)
        # The password handed to the queue is the stored token, never clear text.
        self.assertNotIn('授权码', str(config['password']))

    def test_a_sent_alert_is_not_sent_again_but_a_failed_one_is_retried(self):
        item = self.add_assignment()
        self.controller.store.data['email_log'][f'alert|{item.revision}|24'] = 'sent 2026-10-14T08:00:00+08:00'
        self.assertFalse(self.controller.dispatch_email([(item, 24)], [], self.controller.store.active_items()))
        self.assertEqual(self.mailer.queued, [])
        self.controller.store.data['email_log'][f'attempt|alert|{item.revision}|24'] = '3'
        self.assertFalse(self.controller.dispatch_email([(item, 24)], [], self.controller.store.active_items()))
        self.assertEqual(self.mailer.queued, [])

    def test_digest_is_queued_only_when_its_time_is_due(self):
        self.add_assignment(hours=30)
        self.assertFalse(self.controller.dispatch_email([], [], self.controller.store.active_items()))
        self.assertTrue(self.controller.dispatch_email([], ['08:00'], self.controller.store.active_items()))
        self.assertEqual(len(self.mailer.queued), 1)
        self.assertTrue(self.mailer.queued[0][0].startswith('digest|'))

    def test_nothing_is_queued_while_email_reminders_are_off(self):
        item = self.add_assignment()
        self.enable_email(enabled=False)
        self.assertFalse(self.controller.dispatch_email([(item, 24)], ['08:00'], self.controller.store.active_items()))
        self.assertEqual(self.mailer.queued, [])

    def test_delivery_result_is_recorded_and_stays_out_of_the_log(self):
        self.controller.record_email('alert|u1|24', False, '邮箱授权码不正确')
        self.assertIn('failed', self.controller.store.data['email_log']['alert|u1|24'])
        self.assertEqual(self.controller.store.data['email_log']['attempt|alert|u1|24'], '1')
        self.assertIn('授权码', self.controller.snapshot()['email_status'])
        self.controller.record_email('alert|u1|24', True, '')
        self.assertIn('sent', self.controller.store.data['email_log']['alert|u1|24'])
        self.assertNotIn('attempt|alert|u1|24', self.controller.store.data['email_log'])

    def test_invalid_email_settings_are_rejected_without_saving(self):
        before = self.controller.store.data['settings']['email']['recipient']
        with self.assertRaises(ValueError):
            self.controller.settings({'email': dict(enabled=True, recipient='bad-address', server='smtp.qq.com', password='x')})
        with self.assertRaises(ValueError):
            self.controller.settings({'email': dict(enabled=True, recipient='me@qq.com', digest_times=['25:00'])})
        self.assertEqual(self.controller.store.data['settings']['email']['recipient'], before)

    def test_test_email_requires_an_enabled_recipient(self):
        with self.assertRaises(ValueError):
            self.controller.send_test_email(dict(enabled=False, recipient='', password=''))
        self.controller.send_test_email(self.controller.email_config())
        self.assertEqual(len(self.mailer.queued), 1)
        self.assertTrue(self.mailer.queued[0][0].startswith('test|'))


if __name__ == '__main__':
    unittest.main()
