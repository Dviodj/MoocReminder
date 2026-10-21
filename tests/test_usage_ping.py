import _bootstrap  # noqa: F401  (puts src/ on sys.path)

import unittest
from datetime import date

import usage_ping as ping
from usage_ping import count, line_for, lists, new_id, parse_line, valid_id

INSTALL = '0123456789abcdef'
OTHER = 'fedcba9876543210'


class IdentityTests(unittest.TestCase):
    def test_a_new_id_is_random_lower_case_hex(self):
        first, second = new_id(), new_id()
        self.assertTrue(valid_id(first))
        self.assertEqual(16, len(first))
        self.assertNotEqual(first, second)

    def test_nothing_that_looks_like_a_machine_is_accepted_as_an_id(self):
        for value in ('', 'not-hex', 'ABCDEF0123456789', '12345678', None, 12):
            self.assertFalse(valid_id(value))

    def test_a_line_carries_only_the_id_and_the_version(self):
        self.assertEqual(f'`{INSTALL}` {ping.VERSION}', line_for(INSTALL))

    def test_a_bad_id_is_refused_rather_than_written(self):
        with self.assertRaises(ValueError):
            line_for('too short')

    def test_a_line_reads_back_as_the_same_id(self):
        self.assertEqual(INSTALL, parse_line(line_for(INSTALL)))
        self.assertEqual(INSTALL, parse_line(f'  `{INSTALL}`  \n'))

    def test_text_that_is_not_ours_is_not_counted(self):
        for text in ('', None, '随便写点什么', '`nothex` v0.2.2', '**重要**'):
            self.assertEqual('', parse_line(text), text)

    def test_a_row_without_a_version_still_reads_back(self):
        # A hand-edited comment that lost the version must still count once.
        self.assertEqual(INSTALL, parse_line(f'`{INSTALL}`'))


class CommentListTests(unittest.TestCase):
    def entry(self, body, number=1):
        return dict(id=number, body=body)

    def test_one_comment_is_one_install(self):
        entries = [self.entry(line_for(INSTALL), 1)]
        self.assertEqual(1, count(entries))
        self.assertEqual([INSTALL], list(lists(entries)))
        self.assertEqual(1, lists(entries)[INSTALL]['id'])

    def test_prose_comments_are_ignored(self):
        entries = [self.entry(line_for(INSTALL), 1), self.entry('这是一个欢迎语', 2)]
        self.assertEqual(1, count(entries))

    def test_an_edit_is_only_counted_once(self):
        # The same install edited its comment: both copies are on the page.
        entries = [self.entry(f'`{INSTALL}` v0.2.1', 1), self.entry(f'`{INSTALL}` v0.2.2', 2)]
        self.assertEqual(1, count(entries))

    def test_an_empty_page_counts_nobody(self):
        self.assertEqual(0, count([]))
        self.assertEqual(0, count(None))
        self.assertEqual(0, count(['not a dict', None, 7]))

    def test_a_row_without_a_version_still_counts(self):
        self.assertEqual(1, count([self.entry(f'`{INSTALL}`')]))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.saved = (ping.ISSUE_URL, ping.UPLOAD_TOKEN)
        ping.ISSUE_URL = 'https://example.invalid/issues/1'

    def tearDown(self):
        ping.ISSUE_URL, ping.UPLOAD_TOKEN = self.saved

    def test_without_a_token_nothing_is_sent(self):
        ping.UPLOAD_TOKEN = ''
        self.assertFalse(ping.available())
        self.assertTrue(ping.reads_available())
        ok, message = ping.send(INSTALL)
        self.assertFalse(ok)
        self.assertIn('写入权限', message)

    def test_without_an_address_nothing_is_sent(self):
        ping.ISSUE_URL = ''
        ping.UPLOAD_TOKEN = 'token'
        self.assertFalse(ping.available())
        ok, message = ping.send(INSTALL)
        self.assertFalse(ok)
        self.assertIn('统计地址', message)

    def test_an_offline_machine_gets_an_explanation_instead_of_an_exception(self):
        ping.UPLOAD_TOKEN = 'token'

        def boom(request, timeout):
            raise OSError('no route to host')

        ok, message = ping.send(INSTALL, opener=boom)
        self.assertFalse(ok)
        self.assertIn('网络不通', message)

    def test_a_bad_identity_is_refused(self):
        ping.UPLOAD_TOKEN = 'token'
        ok, message = ping.send('nope')
        self.assertFalse(ok)
        self.assertIn('安装标识', message)


class ScheduleTests(unittest.TestCase):
    """The daily limit lives here, not in the window, so it can be tested."""

    def setUp(self):
        self.saved = (ping.ISSUE_URL, ping.UPLOAD_TOKEN)
        ping.ISSUE_URL = 'https://example.invalid/issues/1'
        ping.UPLOAD_TOKEN = 'token'

    def tearDown(self):
        ping.ISSUE_URL, ping.UPLOAD_TOKEN = self.saved

    def test_nothing_is_sent_without_an_install_id(self):
        self.assertFalse(ping.due({}, '2026-10-21'))
        self.assertFalse(ping.due({'install': 'nope'}, '2026-10-21'))
        self.assertFalse(ping.due(None, '2026-10-21'))

    def test_nothing_is_sent_by_a_build_that_cannot_write(self):
        ping.UPLOAD_TOKEN = ''
        self.assertFalse(ping.due({'install': INSTALL}, '2026-10-21'))

    def test_a_fresh_install_is_sent_once(self):
        sent = []

        def sender(install, today, opener=None):
            sent.append(today)
            return True, 'ok'

        status = ping.report({'install': INSTALL}, '2026-10-21', sender=sender)
        self.assertEqual(['2026-10-21'], sent)
        self.assertEqual('2026-10-21', status['reported'])

    def test_the_same_day_is_not_sent_twice(self):
        sent = []

        def sender(install, today, opener=None):
            sent.append(today)
            return True, 'ok'

        status = {'install': INSTALL, 'reported': '2026-10-21'}
        again = ping.report(status, '2026-10-21', sender=sender)
        self.assertEqual([], sent)
        self.assertEqual(status, again)

    def test_the_next_day_is_sent_again(self):
        sent = []

        def sender(install, today, opener=None):
            sent.append(today)
            return True, 'ok'

        status = {'install': INSTALL, 'reported': '2026-10-21'}
        ping.report(status, '2026-10-22', sender=sender)
        self.assertEqual(['2026-10-22'], sent)

    def test_a_failed_attempt_is_not_retried_all_day(self):
        sent = []

        def sender(install, today, opener=None):
            sent.append(today)
            return False, '网络不通'

        status = ping.report({'install': INSTALL}, '2026-10-21', sender=sender)
        ping.report(status, '2026-10-21', sender=sender)
        self.assertEqual(['2026-10-21'], sent)
        self.assertEqual('2026-10-21', status['failed'])
        self.assertIn('网络不通', status['message'])

    def test_a_sender_that_throws_is_only_a_failed_day(self):
        def boom(install, today, opener=None):
            raise RuntimeError('什么坏了')

        status = ping.report({'install': INSTALL}, '2026-10-21', sender=boom)
        self.assertEqual('2026-10-21', status['failed'])

    def test_the_install_id_goes_out_unchanged(self):
        seen = []

        def sender(install, today, opener=None):
            seen.append(install)
            return True, 'ok'

        ping.report({'install': INSTALL}, '2026-10-21', sender=sender)
        self.assertEqual([INSTALL], seen)

    def test_opting_out_leaves_no_identity_behind(self):
        self.assertEqual({}, ping.forget())

    def test_without_a_token_the_log_is_untouched(self):
        ping.UPLOAD_TOKEN = ''
        status = {'install': INSTALL}
        self.assertEqual(status, ping.report(status, '2026-10-21'))


class WriteTests(unittest.TestCase):
    """The write path, driven through a fake HTTP opener instead of the network.

    One installed comment must be edited, a new install must create one, and a
    closed issue must stop everything.  All three are the difference between
    counting correctly and quietly double counting.
    """

    def setUp(self):
        self.saved = (ping.ISSUE_URL, ping.UPLOAD_TOKEN)
        ping.ISSUE_URL = 'https://example.invalid/issues/1'
        ping.UPLOAD_TOKEN = ''

    def tearDown(self):
        ping.ISSUE_URL, ping.UPLOAD_TOKEN = self.saved

    def opener(self, issue=None, entries=None, capture=None, closed=False, auth=False):
        issue = issue or {'state': 'closed' if closed else 'open', 'number': 1}
        entries = list(entries or [])

        def open_(request, timeout=None):
            if auth:
                capture.append((request.get_method(), request.full_url,
                                request.get_header('Authorization')))
            else:
                capture.append((request.get_method(), request.full_url))
            url = request.full_url
            if url.endswith('/comments') and request.get_method() == 'GET':
                return Response(entries)
            if url.endswith('/comments'):
                return Response(dict(id=99, body=''))
            if '/comments/' in url:
                return Response(dict(id=99, body=''))
            return Response(issue)

        return open_

    def test_a_new_install_creates_its_own_comment(self):
        seen = []
        ok, message = ping.send(INSTALL, '2026-10-21', opener=self.opener(capture=seen),
                                token='token')
        self.assertTrue(ok, message)
        methods = [method for method, url in seen]
        self.assertIn('POST', methods)
        self.assertTrue(any(url.endswith('/comments') for _, url in seen))

    def test_an_existing_install_edits_instead_of_posting_again(self):
        seen = []
        entries = [dict(id=42, body=f'`{INSTALL}` v0.0.1')]
        ok, message = ping.send(INSTALL, '2026-10-21',
                                opener=self.opener(entries=entries, capture=seen),
                                token='token')
        self.assertTrue(ok, message)
        methods = [method for method, _ in seen]
        self.assertIn('PATCH', methods)
        self.assertNotIn('POST', methods)
        self.assertTrue(any(url.endswith('/comments/42') for _, url in seen))

    def test_the_same_version_is_not_written_again(self):
        seen = []
        entries = [dict(id=42, body=line_for(INSTALL))]
        ok, message = ping.send(INSTALL, '2026-10-21',
                                opener=self.opener(entries=entries, capture=seen),
                                token='token')
        self.assertTrue(ok)
        methods = [method for method, _ in seen]
        self.assertNotIn('POST', methods)
        self.assertNotIn('PATCH', methods)
        self.assertIn('已经记过', message)

    def test_a_closed_issue_stops_everything(self):
        seen = []
        ok, message = ping.send(INSTALL, '2026-10-21',
                                opener=self.opener(closed=True, capture=seen), token='token')
        self.assertFalse(ok)
        self.assertIn('统计已停止', message)
        # Not one write, on the chance a stale page still says "open".
        self.assertNotIn('POST', [method for method, _ in seen])

    def test_the_count_is_read_from_public_comments(self):
        entries = [dict(id=1, body=line_for(INSTALL)), dict(id=2, body=line_for(OTHER)),
                   dict(id=3, body='一段说明')]
        self.assertEqual(2, ping.count(entries))

    def test_the_comment_list_is_read_with_the_writing_credential(self):
        seen = []
        ping.send(INSTALL, '2026-10-21',
                  opener=self.opener(capture=seen, auth=True), token='secret')
        reads = [auth for method, url, auth in seen
                 if method == 'GET' and url.endswith('/comments')]
        self.assertEqual(['Bearer secret'], reads)

    def test_the_page_that_is_being_edited_is_the_latest_one(self):
        # GitHub returns the page oldest first; posting twice must not count twice.
        entries = [dict(id=1, body=line_for(OTHER)), dict(id=2, body=f'`{INSTALL}` v0.0.1'),
                   dict(id=3, body=f'`{INSTALL}` v0.2.2')]
        self.assertEqual(2, ping.count(entries))
        newest = ping.lists(entries)[INSTALL]
        self.assertEqual(3, newest['id'])


class Response:
    def __init__(self, payload, status=200):
        import json
        self.status = status
        self._raw = json.dumps(payload).encode('utf-8')

    def read(self):
        return self._raw


if __name__ == '__main__':
    unittest.main()
