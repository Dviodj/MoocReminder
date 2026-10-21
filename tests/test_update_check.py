import _bootstrap  # noqa: F401  (puts src/ on sys.path)

import json
import unittest

import update_check
from update_check import from_release, newer, parse, unseen, version_tuple

RELEASE = {
    'tag_name': 'v0.2.2',
    'html_url': 'https://github.com/Dviodj/MoocReminder/releases/tag/v0.2.2',
    'body': '修好桌面上快捷方式的图标。\n\n更多细节见页面。',
}


class VersionTests(unittest.TestCase):
    def test_versions_are_compared_numerically(self):
        self.assertEqual((0, 2, 1), version_tuple('v0.2.1'))
        self.assertEqual((0, 2, 1), version_tuple(' 0.2.1 '))
        self.assertIsNone(version_tuple('v0.2'))
        self.assertIsNone(version_tuple('latest'))
        self.assertIsNone(version_tuple(21))

    def test_a_higher_version_is_an_update(self):
        self.assertTrue(newer('v0.2.2', 'v0.2.1'))
        self.assertTrue(newer('v0.10.0', 'v0.9.9'))
        self.assertFalse(newer('v0.2.1', 'v0.2.1'))
        # A stale or mistyped tag must not look like an update, or the prompt
        # would never go away.
        self.assertFalse(newer('v0.2.0', 'v0.2.1'))
        self.assertFalse(newer('最新版', 'v0.2.1'))
        self.assertFalse(newer('', 'v0.2.1'))


class ParseTests(unittest.TestCase):
    def test_a_well_formed_document_is_normalised(self):
        got = parse({'latest': 'v0.2.2', 'url': 'https://example.com/a', 'notes': '改了什么',
                     'announcements': [{'id': 'a', 'title': '标题', 'body': '正文', 'level': 'warning'}]})
        self.assertEqual('v0.2.2', got['latest'])
        self.assertEqual('改了什么', got['notes'])
        self.assertEqual([dict(id='a', title='标题', body='正文', link='', level='warning')],
                         got['announcements'])

    def test_junk_in_the_document_is_dropped_not_raised(self):
        self.assertIsNone(parse(b'\xff\xfe not utf8'))
        self.assertIsNone(parse('{oops'))
        self.assertIsNone(parse('[1, 2]'))
        self.assertIsNone(parse(None))
        got = parse({'latest': '版本二', 'url': 'javascript:alert(1)', 'notes': 7,
                     'announcements': 'nope'})
        self.assertEqual(dict(latest='', url='', notes='', announcements=[]), got)

    def test_one_bad_announcement_does_not_silence_the_others(self):
        got = parse({'announcements': [
            {'id': 'ok', 'title': '留下'},
            {'title': '没有 id'},
            {'id': 'no-title'},
            {'id': 'ok', 'title': '重复 id'},
            'not a dict',
            {'id': 'weird', 'title': '等级不认识', 'body': '正文', 'level': 'critical'},
        ]})
        self.assertEqual(['ok', 'weird'], [item['id'] for item in got['announcements']])
        self.assertEqual('info', got['announcements'][1]['level'])

    def test_announcement_links_must_be_https(self):
        got = parse({'announcements': [{'id': 'a', 'title': 't', 'link': 'http://example.com'}]})
        self.assertEqual('', got['announcements'][0]['link'])


class ReleaseTests(unittest.TestCase):
    def test_a_release_becomes_a_document(self):
        got = from_release(json.dumps(RELEASE))
        self.assertEqual('v0.2.2', got['latest'])
        self.assertEqual(RELEASE['html_url'], got['url'])
        self.assertEqual('修好桌面上快捷方式的图标。', got['notes'])
        self.assertEqual([], got['announcements'])
        # Compare against a fixed baseline: this test is about reading a release,
        # not about which version the checkout happens to be on today.
        self.assertTrue(newer(got['latest'], 'v0.2.1'))

    def test_announcements_are_read_from_their_own_block(self):
        body = '\n'.join([
            '## 新增',
            '',
            '```mooc-reminder-announcements',
            '[{"id": "2026-10-20", "title": "标题", "body": "正文"}]',
            '```',
            '',
            '- 其它改动',
        ])
        got = from_release(dict(RELEASE, body=body))
        self.assertEqual(['2026-10-20'], [item['id'] for item in got['announcements']])
        # The prose around the block is for people on the release page; the card
        # takes one line and the heading is more useful than the code fence.
        self.assertEqual('新增', got['notes'])

    def test_markdown_headings_are_not_shown_as_the_notes(self):
        got = from_release(dict(RELEASE, body='# 标题\n\n正文第一行\n第二行'))
        self.assertEqual('标题', got['notes'])

    def test_a_broken_block_still_yields_the_version(self):
        got = from_release(dict(RELEASE, body='```mooc-reminder-announcements\n[{oops]\n```'))
        self.assertEqual('v0.2.2', got['latest'])
        self.assertEqual([], got['announcements'])

    def test_a_non_release_document_is_rejected(self):
        self.assertIsNone(from_release('{"message": "Not Found"}'))
        self.assertIsNone(from_release(b'\xff'))
        self.assertIsNone(from_release('not json'))


class TransportTests(unittest.TestCase):
    def test_the_first_url_that_answers_wins(self):
        asked = []

        def opener(request, timeout):
            asked.append(request.full_url)
            raise OSError('no route')

        self.assertIsNone(update_check.fetch(('https://a', 'https://b'), opener=opener))
        self.assertEqual(['https://a', 'https://b'], asked)

    def test_the_request_identifies_nobody(self):
        seen = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b'{}'

        def opener(request, timeout):
            seen.update(url=request.full_url, headers={key.lower(): value for key, value in request.header_items()})
            return Response()

        self.assertEqual(b'{}', update_check.fetch(('https://a',), opener=opener))
        self.assertEqual('https://a', seen['url'])
        self.assertEqual({'user-agent', 'accept'}, set(seen['headers']))

    def test_a_failing_check_is_none_not_an_exception(self):
        def opener(request, timeout):
            raise TimeoutError('slow')

        self.assertIsNone(update_check.check(('https://a',), opener=opener))

    def test_an_empty_body_is_not_the_latest_release(self):
        class Empty:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b''

        self.assertIsNone(update_check.check(('https://a',), opener=lambda *a, **k: Empty()))

    def test_a_real_network_failure_reaches_the_caller_as_none(self):
        # No mocking: pointing at a host that will not resolve is the offline
        # case users actually hit, and it must not raise.
        self.assertIsNone(update_check.check(('https://no-such-host.invalid/announce.json',), timeout=2))


class UnseenTests(unittest.TestCase):
    ITEMS = [dict(id='old'), dict(id='new')]

    def test_dismissed_ids_are_hidden(self):
        self.assertEqual(['new'], [item['id'] for item in unseen(self.ITEMS, ['old'])])
        self.assertEqual(['old'], [item['id'] for item in unseen(self.ITEMS, ['new'])])
        self.assertEqual([], unseen(self.ITEMS, ['old', 'new']))

    def test_no_record_means_everything_is_new(self):
        self.assertEqual(self.ITEMS, unseen(self.ITEMS, []))
        self.assertEqual(self.ITEMS, unseen(self.ITEMS, None))
        # A single id is still accepted, so a hand-edited file cannot crash it.
        self.assertEqual(['new'], [item['id'] for item in unseen(self.ITEMS, 'old')])


if __name__ == '__main__':
    unittest.main()
