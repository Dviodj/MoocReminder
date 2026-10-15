"""The send log shown to users.

`portal.record_email` writes internal keys into `data['email_log']`. This module
checks that they are turned into readable rows, that the retry counters never
leak into the list, and that a log written by an older version still renders.
"""
import unittest

from mail_log import entries, summary


ALERT = 'alert|abc123|24.0'
DIGEST = 'digest|2026-02-14|08:00'
TEST = 'test|9f2c'


class EntryTests(unittest.TestCase):
    def test_an_alert_shows_its_window_and_time(self):
        rows = entries({ALERT: 'sent 2026-02-14T08:00:00+08:00'})
        self.assertEqual(1, len(rows))
        self.assertEqual('作业提醒', rows[0]['kind'])
        self.assertEqual('已发送', rows[0]['result'])
        self.assertTrue(rows[0]['ok'])
        self.assertFalse(rows[0]['failed'])
        self.assertEqual('提前 24 小时', rows[0]['note'])
        self.assertEqual('02月14日 08:00', rows[0]['when'])

    def test_a_digest_shows_the_time_it_was_planned_for(self):
        rows = entries({DIGEST: 'sent 2026-02-14T09:03:00+08:00'})
        self.assertEqual('每日汇总', rows[0]['kind'])
        self.assertEqual('计划 08:00', rows[0]['note'])

    def test_a_test_message_is_labelled(self):
        rows = entries({TEST: 'sent 2026-02-14T08:00:00+08:00'})
        self.assertEqual('测试邮件', rows[0]['kind'])

    def test_a_failure_keeps_the_reason_it_was_recorded_with(self):
        rows = entries({ALERT: 'failed 2026-02-14T08:00:00+08:00 · 授权码不正确'})
        self.assertEqual('发送失败', rows[0]['result'])
        self.assertTrue(rows[0]['failed'])
        self.assertEqual('提前 24 小时 · 授权码不正确', rows[0]['note'])

    def test_retry_counters_are_not_listed_as_messages(self):
        rows = entries({'attempt|' + ALERT: '2', ALERT: 'failed 2026-02-14T08:00:00+08:00'})
        self.assertEqual([ALERT], [row['key'] for row in rows])

    def test_summary_marks_written_by_an_older_version_are_not_listed(self):
        """`due|digest|...` is bookkeeping, and used to share the log dict."""
        rows = entries({'due|digest|2026-02-14|08:00': '2026-02-14',
                        ALERT: 'sent 2026-02-14T08:00:00+08:00'})
        self.assertEqual([ALERT], [row['key'] for row in rows])
        self.assertEqual(dict(total=1, failed=0, sent=1), summary({'due|digest|2026-02-14|08:00': '2026-02-14',
                                                                   ALERT: 'sent 2026-02-14T08:00:00+08:00'}))

    def test_newest_first_without_a_readable_time_last(self):
        log = {
            ALERT: 'sent 2026-02-14T08:00:00+08:00',
            DIGEST: 'sent 2026-02-15T08:00:00+08:00',
            'weird|key': 'sent',
        }
        self.assertEqual([DIGEST, ALERT, 'weird|key'], [row['key'] for row in entries(log)])
        self.assertEqual('—', entries(log)[-1]['when'])

    def test_an_unreadable_time_is_shown_as_written(self):
        rows = entries({ALERT: 'sent not-a-time'})
        self.assertEqual('not-a-time', rows[0]['when'])
        self.assertEqual(0.0, rows[0]['sort'])

    def test_results_written_by_an_older_version_still_render(self):
        rows = entries({ALERT: 'sent', DIGEST: f'sent {0:04d}'})
        self.assertEqual({'已发送'}, {row['result'] for row in rows})

    def test_limit_keeps_the_newest_entries(self):
        log = {f'alert|{index}|24.0': f'sent 2026-02-{index:02d}T08:00:00+08:00' for index in range(1, 11)}
        rows = entries(log, limit=3)
        self.assertEqual(['alert|10|24.0', 'alert|9|24.0', 'alert|8|24.0'], [row['key'] for row in rows])

    def test_an_empty_or_missing_log_is_not_an_error(self):
        self.assertEqual([], entries({}))
        self.assertEqual([], entries(None))

    def test_a_value_that_is_not_a_string_does_not_raise(self):
        rows = entries({ALERT: None})
        self.assertEqual('已记录', rows[0]['result'])
        self.assertEqual('—', rows[0]['when'])


class SummaryTests(unittest.TestCase):
    def test_it_counts_sent_and_failed(self):
        log = {
            ALERT: 'sent 2026-02-14T08:00:00+08:00',
            DIGEST: 'failed 2026-02-14T08:00:00+08:00',
        }
        self.assertEqual(dict(total=2, failed=1, sent=1), summary(log))

    def test_test_messages_are_left_out_of_the_counts(self):
        log = {ALERT: 'sent 2026-02-14T08:00:00+08:00', TEST: 'failed 2026-02-14T08:00:00+08:00'}
        self.assertEqual(dict(total=1, failed=0, sent=1), summary(log))

    def test_retry_counters_are_not_counted(self):
        log = {'attempt|' + ALERT: '1', ALERT: 'sent 2026-02-14T08:00:00+08:00'}
        self.assertEqual(dict(total=1, failed=0, sent=1), summary(log))

    def test_summary_marks_written_by_an_older_version_are_not_counted(self):
        log = {'due|digest|2026-02-14|08:00': '2026-02-14',
               DIGEST: 'sent 2026-02-14T08:00:00+08:00'}
        self.assertEqual(dict(total=1, failed=0, sent=1), summary(log))

    def test_an_empty_log_counts_zero(self):
        self.assertEqual(dict(total=0, failed=0, sent=0), summary({}))


if __name__ == '__main__':
    unittest.main()
