import _bootstrap  # noqa: F401  (puts src/ on sys.path)

import portal
import tempfile
import unittest
import time
from pathlib import Path
from unittest.mock import patch

import storage
from storage import Store
from portal import Controller
from core import Assignment, now_beijing, BEIJING
from datetime import datetime, timedelta


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.controller = Controller(Store(self.temp.name), auto_scan=False)

    def tearDown(self):
        self.controller.stop()
        self.controller.worker.join(timeout=3)
        self.temp.cleanup()

    def test_invalid_settings_do_not_corrupt_saved_values(self):
        with self.assertRaises(ValueError):
            self.controller.settings({'interval_minutes': 0})
        self.assertEqual(self.controller.store.data['settings']['interval_minutes'], 30)

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

    def add_assignment_at(self, deadline, uid='u1', name='第三章作业'):
        """Store a pending task with an explicit deadline, for a pinned clock."""
        item = Assignment(uid=uid, course='高等数学', course_url='https://www.icourse163.org/learn/X-1?tid=1#/learn/testlist',
                          name=name, kind='作业', deadline=deadline, deadline_text='',
                          status='pending', checked_at=deadline.isoformat())
        self.controller.store.data['assignments'] = [item.to_dict()]
        return item

    def test_the_daily_summary_actually_leaves_the_queue(self):
        """End to end: the due mark must not collide with the send log.

        `due_digests` recorded `digest|<date>|<time>` in the very dict the send
        log uses, so `dispatch_email` saw the key as already sent and dropped
        every daily summary. The user set a time and never received anything.
        """
        moment = datetime(2026, 2, 14, 8, 0, 30, tzinfo=BEIJING)
        self.add_assignment_at(moment + timedelta(hours=80))
        self.enable_email(digest_times=['08:00'])
        with patch.object(portal, 'now_beijing', lambda: moment):
            self.controller.tick()
        self.assertIn('digest|2026-02-14|08:00', [key for key, _, _ in self.mailer.queued])

    def test_a_missed_digest_time_is_delivered_later_that_day(self):
        """Opening the program after the chosen time still sends that day's mail."""
        late = datetime(2026, 2, 14, 9, 30, 0, tzinfo=BEIJING)
        self.add_assignment_at(late + timedelta(hours=80))
        self.enable_email(digest_times=['08:00'])
        with patch.object(portal, 'now_beijing', lambda: late):
            self.controller.tick()
        self.assertIn('digest|2026-02-14|08:00', [key for key, _, _ in self.mailer.queued])

    def test_a_time_later_today_is_not_sent_early(self):
        moment = datetime(2026, 2, 14, 7, 0, 0, tzinfo=BEIJING)
        self.add_assignment_at(moment + timedelta(hours=80))
        self.enable_email(digest_times=['08:00'])
        with patch.object(portal, 'now_beijing', lambda: moment):
            self.controller.tick()
        self.assertEqual(self.mailer.queued, [])

    def test_one_summary_per_time_per_day(self):
        moment = datetime(2026, 2, 14, 8, 0, 30, tzinfo=BEIJING)
        self.add_assignment_at(moment + timedelta(hours=80))
        self.enable_email(digest_times=['08:00'])
        with patch.object(portal, 'now_beijing', lambda: moment):
            self.controller.tick()
            self.controller.tick()
        self.assertEqual([key for key, _, _ in self.mailer.queued], ['digest|2026-02-14|08:00'])

    def test_the_next_day_the_summary_is_due_again(self):
        first = datetime(2026, 2, 14, 8, 0, 30, tzinfo=BEIJING)
        second = datetime(2026, 2, 15, 8, 0, 30, tzinfo=BEIJING)
        self.add_assignment_at(second + timedelta(hours=80))
        self.enable_email(digest_times=['08:00'])
        with patch.object(portal, 'now_beijing', lambda: first):
            self.controller.tick()
        with patch.object(portal, 'now_beijing', lambda: second):
            self.controller.tick()
        self.assertEqual([key for key, _, _ in self.mailer.queued],
                         ['digest|2026-02-14|08:00', 'digest|2026-02-15|08:00'])

    def test_recording_one_reminder_keeps_the_others_in_the_history(self):
        """The send log is a history, so one send must not erase the rest.

        `record_email` used to prune with just its own revision, which deleted
        every other task's entries the moment a new reminder went out.
        """
        first = self.add_assignment(uid='u1', name='第三章作业')
        second = self.add_assignment(uid='u2', name='第四章作业')
        self.controller.record_email(f'alert|{first.revision}|24', True, '')
        self.controller.record_email(f'alert|{second.revision}|24', True, '')
        log = self.controller.store.data['email_log']
        self.assertIn(f'alert|{first.revision}|24', log)
        self.assertIn(f'alert|{second.revision}|24', log)

    def test_entries_for_vanished_tasks_are_still_dropped_by_a_scan(self):
        first = self.add_assignment(uid='u1', name='第三章作业')
        self.controller.record_email(f'alert|{first.revision}|24', True, '')
        # The course page answered but no longer lists this task.
        self.controller.store.merge_scan([], [first.course_url])
        self.assertEqual({}, self.controller.store.data['email_log'])

    def test_the_due_mark_is_kept_out_of_the_send_log(self):
        """The mark is bookkeeping; the user-facing history must not show it."""
        moment = datetime(2026, 2, 14, 8, 0, 30, tzinfo=BEIJING)
        self.add_assignment_at(moment + timedelta(hours=80))
        self.enable_email(digest_times=['08:00'])
        with patch.object(portal, 'now_beijing', lambda: moment):
            self.controller.tick()
        marks = self.controller.store.data['digest_state']
        self.assertEqual({'due|digest|2026-02-14|08:00': '2026-02-14'}, marks)
        self.assertEqual({}, {key: value for key, value in self.controller.store.data['email_log'].items()
                              if key.startswith('due|')})

    def test_yesterdays_due_marks_are_dropped(self):
        self.enable_email(digest_times=['08:00'])
        self.controller.store.data['digest_state'] = {
            'due|digest|2026-02-13|08:00': '2026-02-13',
            'due|digest|2026-02-14|08:00': '2026-02-14',
        }
        # `storage` imports the clock by name, so that is the one to pin.
        with patch.object(storage, 'now_beijing', lambda: datetime(2026, 2, 14, 9, 0, 0, tzinfo=BEIJING)):
            self.controller.store.prune_email_log()
        self.assertEqual({'due|digest|2026-02-14|08:00': '2026-02-14'}, self.controller.store.data['digest_state'])

    def test_a_weekly_summary_is_queued_on_its_day_and_only_once_in_that_cycle(self):
        moment = datetime(2026, 2, 14, 8, 0, 30, tzinfo=BEIJING)   # a Saturday
        self.add_assignment_at(moment + timedelta(hours=80))
        self.enable_email(digest_times=['08:00'], digest_frequency='weekly', digest_weekday=5)
        with patch.object(portal, 'now_beijing', lambda: moment):
            self.controller.tick()
        self.assertEqual(['digest|2026-02-14|08:00'], self.digests())
        # The next day belongs to the same cycle, and the summary has gone out.
        with patch.object(portal, 'now_beijing', lambda: moment.replace(day=15)):
            self.controller.tick()
        self.assertEqual(['digest|2026-02-14|08:00'], self.digests())

    def test_a_weekly_summary_is_not_queued_off_its_day(self):
        moment = datetime(2026, 2, 10, 8, 0, 30, tzinfo=BEIJING)   # the Tuesday after
        self.add_assignment_at(moment + timedelta(hours=80))
        self.enable_email(digest_times=['08:00'], digest_frequency='weekly', digest_weekday=5)
        # Saturday's summary has gone out, so this Tuesday has nothing to send.
        self.controller.store.data['digest_state'] = {'due|digest|2026-02-07|08:00': '2026-02-07'}
        with patch.object(portal, 'now_beijing', lambda: moment):
            self.controller.tick()
        self.assertEqual([], self.digests())

    def test_a_weekly_summary_missed_on_its_day_is_still_delivered(self):
        """The machine was off on Saturday; opening it later still sends it."""
        moment = datetime(2026, 2, 15, 9, 0, 0, tzinfo=BEIJING)   # the Sunday after
        self.add_assignment_at(moment + timedelta(hours=80))
        self.enable_email(digest_times=['08:00'], digest_frequency='weekly', digest_weekday=5)
        with patch.object(portal, 'now_beijing', lambda: moment):
            self.controller.tick()
        self.assertEqual(['digest|2026-02-15|08:00'], self.digests())
        # The mark names the day the summary was for, not the day it went out.
        self.assertEqual({'due|digest|2026-02-14|08:00': '2026-02-14'}, self.controller.store.data['digest_state'])

    def test_a_late_weekly_mark_survives_the_rest_of_its_cycle(self):
        """Pruning must not turn a delivered late summary into a second one."""
        self.enable_email(digest_times=['08:00'], digest_frequency='weekly', digest_weekday=2)
        self.controller.store.data['digest_state'] = {
            'due|digest|2026-02-11|08:00': '2026-02-11',   # this cycle
            'due|digest|2026-02-04|08:00': '2026-02-04',   # the cycle before
        }
        # `storage` imports the clock by name, so that is the one to pin.
        with patch.object(storage, 'now_beijing', lambda: datetime(2026, 2, 14, 9, 0, 0, tzinfo=BEIJING)):
            self.controller.store.prune_email_log()
        self.assertEqual({'due|digest|2026-02-11|08:00': '2026-02-11'}, self.controller.store.data['digest_state'])

    def digests(self):
        """Only the summaries that this tick queued; alerts travel separately."""
        return [key for key, _, _ in self.mailer.queued if key.startswith('digest|')]

    def test_a_new_recipient_gets_todays_summary_again(self):
        moment = datetime(2026, 2, 14, 8, 0, 30, tzinfo=BEIJING)
        self.add_assignment_at(moment + timedelta(hours=80))
        self.enable_email(digest_times=['08:00'])
        with patch.object(portal, 'now_beijing', lambda: moment):
            self.controller.tick()
        self.enable_email(recipient='other@163.com', digest_times=['08:00'])
        self.assertEqual({}, self.controller.store.data['digest_state'])

    def test_a_failed_alert_is_retried_up_to_three_times(self):
        """A recorded failure must not look like a settled send."""
        item = self.add_assignment()
        key = f'alert|{item.revision}|24'
        self.controller.record_email(key, False, '临时网络错误')
        self.assertTrue(self.controller.dispatch_email([(item, 24)], [], self.controller.store.active_items()))
        self.controller.record_email(key, False, '')
        self.controller.record_email(key, False, '')
        self.assertFalse(self.controller.dispatch_email([(item, 24)], [], self.controller.store.active_items()))

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

    def test_a_mail_account_that_is_not_ready_warns_without_discarding_the_patch(self):
        """A half-configured mailbox must not throw away the rest of the patch.

        The binding dialog saves the pasted course link and the mailbox together,
        so refusing the whole patch over a missing authorization code made the
        course link look like it could never be saved.
        """
        warnings = self.controller.settings({'email': dict(enabled=True, recipient='bad-address', server='smtp.qq.com', password='x')})
        self.assertEqual(len(warnings), 1)
        self.assertIn('邮件提醒尚未生效', warnings[0])
        # Kept for correction, but never left switched on while unusable.
        self.assertEqual(self.controller.store.data['settings']['email']['recipient'], 'bad-address')
        self.assertFalse(self.controller.store.data['settings']['email']['enabled'])

    def test_invalid_email_settings_are_rejected_without_saving(self):
        before = self.controller.store.data['settings']['email']['recipient']
        with self.assertRaises(ValueError):
            self.controller.settings({'email': dict(enabled=True, recipient='me@qq.com', digest_times=['25:00'])})
        self.assertEqual(self.controller.store.data['settings']['email']['recipient'], before)

    def test_saving_again_keeps_the_stored_authorization_code(self):
        """The form omits the password when the user did not retype it.

        Reading `current` from the merged patch instead of from storage made the
        second save look like a missing authorization code, so the user had to
        re-enter it after every unrelated change.
        """
        self.enable_email(advanced=True, sender='me@qq.com', password='16-char-code')
        saved = self.controller.store.data['settings']['email']['password']
        self.assertTrue(saved.startswith('dpapi:') or saved.startswith('plain:'))

        # The dialog's own payload shape: no password key at all.
        form = {key: value for key, value in self.controller.email_config().items() if key != 'password'}
        form['digest_times'] = ['08:00']
        warnings = self.controller.settings({'email': form})
        self.assertEqual(warnings, [])
        stored = self.controller.store.data['settings']['email']
        self.assertEqual(stored['password'], saved)
        self.assertTrue(stored['enabled'])
        self.assertEqual(stored['digest_times'], ['08:00'])

    def test_tidying_unrelated_settings_keeps_the_course_link_and_the_mailbox(self):
        """Pasting the course link must save even with a mailbox that needs work."""
        self.controller.settings({'email': dict(enabled=True, recipient='', server='', password='')})
        home = 'https://www.icourse163.org/home.htm?userId=8123456789#/home/course'
        warnings = self.controller.settings({'home_url': home})
        self.assertTrue(warnings or not self.controller.store.is_bound())
        self.assertEqual(self.controller.store.data['settings']['home_url'], home)

    def test_test_email_requires_an_enabled_recipient(self):
        with self.assertRaises(ValueError):
            self.controller.send_test_email(dict(enabled=False, recipient='', password=''))
        self.controller.send_test_email(self.controller.email_config())
        self.assertEqual(len(self.mailer.queued), 1)
        self.assertTrue(self.mailer.queued[0][0].startswith('test|'))


if __name__ == '__main__':
    unittest.main()
