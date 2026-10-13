import json
import tempfile
import unittest
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from threading import Thread

from storage import Store
from portal import Controller, create_server


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


if __name__ == '__main__':
    unittest.main()
