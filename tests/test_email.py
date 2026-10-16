import _bootstrap  # noqa: F401  (puts src/ on sys.path)

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import emailer
import secrets_store
from browser import parse_cookie_header
from core import Assignment, BEIJING, DEFAULT_SETTINGS, due_digests
from emailer import (EmailError, EmailSettings, Mail, Mailer, SmtpNotifier, compose_alert, compose_digest,
                     password_saved, suggest_server, validate, valid_address, valid_digest_times, get_notifier)
from storage import Store, normalize_email_settings


def make_item(uid='u1', name='第三章作业', kind='作业', course='高等数学', hours=20,
              url='https://www.icourse163.org/learn/X-1?tid=1#/learn/testlist'):
    return Assignment(uid=uid, course=course, course_url=url, name=name, kind=kind,
                      deadline=datetime(2026, 10, 15, 23, 30, tzinfo=BEIJING),
                      deadline_text='截止时间：2026/10/15 23:30', status='pending', checked_at='')


NOW = datetime(2026, 10, 14, 8, 0, tzinfo=BEIJING)


class ShippedSenderTests(unittest.TestCase):
    """An ordinary user gives only their own address; the sender is shipped."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.folder = Path(self.temp.name)
        (self.folder/'email_sender.json').write_text(json.dumps({
            'sender': 'reminder@example.com', 'server': 'smtp.example.com', 'port': 465,
            'security': 'ssl', 'password': 'admin-secret'}), encoding='utf-8')
        self.user = dict(enabled=True, recipient='student@qq.com', sender='', server='',
                         port=465, security='ssl', password='', digest_times=[])

    def tearDown(self):
        emailer.load_sender(self.folder, refresh=True)
        self.temp.cleanup()

    def test_blank_fields_fall_back_to_the_shipped_sender(self):
        merged = emailer.resolve(self.user, self.folder)
        self.assertEqual(merged['sender'], 'reminder@example.com')
        self.assertEqual(merged['server'], 'smtp.example.com')
        self.assertEqual(merged['password'], 'admin-secret')

    def test_only_the_recipient_is_required_to_validate(self):
        settings = emailer.validate(self.user, emailer.resolve_password(self.user, self.folder), self.folder)
        self.assertEqual(settings.recipient, 'student@qq.com')
        self.assertEqual(settings.sender, 'reminder@example.com')
        self.assertEqual(settings.password, 'admin-secret')

    def test_a_user_value_wins_over_the_shipped_one(self):
        custom = dict(self.user, sender='me@qq.com', server='smtp.qq.com', password='mine')
        merged = emailer.resolve(custom, self.folder)
        self.assertEqual(merged['sender'], 'me@qq.com')
        self.assertEqual(merged['server'], 'smtp.qq.com')
        self.assertEqual(merged['password'], 'mine')

    def test_without_a_shipped_sender_the_user_is_told_what_is_missing(self):
        empty = Path(tempfile.mkdtemp())
        try:
            with self.assertRaises(EmailError) as error:
                emailer.validate(self.user, emailer.resolve_password(self.user, empty), empty)
            # The blank server is reported first; either message is actionable.
            self.assertTrue('邮件服务器' in str(error.exception) or '授权码' in str(error.exception))
            # And with a server but no credential, the credential is the blocker.
            with self.assertRaises(EmailError) as second:
                emailer.validate(dict(self.user, server='smtp.qq.com'),
                                 emailer.resolve_password(self.user, empty), empty)
            self.assertIn('授权码', str(second.exception))
        finally:
            for path in sorted(empty.rglob('*'), reverse=True):
                path.unlink() if path.is_file() else path.rmdir()
            empty.rmdir()

    def test_the_repository_file_ships_empty(self):
        # The tracked copy must never carry a real credential.
        shipped = emailer.load_sender(Path(__file__).resolve().parents[1], refresh=True)
        self.assertEqual(shipped, {}, '仓库里的 email_sender.json 必须保持为空模板')

    def test_a_malformed_sender_file_is_ignored(self):
        (self.folder/'email_sender.json').write_text('{ not json', encoding='utf-8')
        self.assertEqual(emailer.load_sender(self.folder, refresh=True), {})


class CookieLoginTests(unittest.TestCase):
    """The cookie fallback must parse real pastes and ignore everything else."""

    def test_a_cookie_header_is_parsed(self):
        pasted = 'NTESSTUDYSI=abc123; STUDY_SESS=def456; EDUWEBDEVICE=xyz; _ga=GA1.2.9'
        parsed = parse_cookie_header(pasted)
        self.assertEqual([name for name, _, _ in parsed], ['NTESSTUDYSI', 'STUDY_SESS', 'EDUWEBDEVICE'])
        self.assertTrue(all(domain == '.icourse163.org' for _, _, domain in parsed))

    def test_unrelated_cookies_are_dropped(self):
        self.assertEqual(parse_cookie_header('_ga=1; sessionid=2; foo=bar'), [])

    def test_multiline_and_quoted_values_are_handled(self):
        parsed = parse_cookie_header('NTESSTUDYSI=abc;\nSTUDY_INFO="quoted-value"\n')
        self.assertEqual(parsed, [('NTESSTUDYSI', 'abc', '.icourse163.org'),
                                  ('STUDY_INFO', 'quoted-value', '.icourse163.org')])

    def test_junk_input_yields_nothing_instead_of_raising(self):
        for junk in ('', '   ', 'not-a-cookie', ';;;', '=;;='):
            self.assertEqual(parse_cookie_header(junk), [])

    def test_login_with_cookies_refuses_an_empty_paste(self):
        from browser import MoocBrowser
        reader = MoocBrowser(tempfile.mkdtemp())
        with self.assertRaises(ValueError) as error:
            reader.login_with_cookies('https://www.icourse163.org/home.htm?userId=1#/home/course', '  ')
        self.assertIn('Cookie', str(error.exception))


class AddressAndTimeTests(unittest.TestCase):
    def test_provider_presets_cover_common_mailboxes(self):
        self.assertEqual(suggest_server('me@qq.com')['server'], 'smtp.qq.com')
        self.assertEqual(suggest_server('me@163.com')['port'], 465)
        self.assertEqual(suggest_server('me@outlook.com')['security'], 'starttls')
        self.assertEqual(suggest_server('me@company.example'), {})
        self.assertEqual(suggest_server('not-an-address'), {})

    def test_address_validation(self):
        self.assertTrue(valid_address('a.b+tag@sub.example.com'))
        self.assertFalse(valid_address('a@b'))
        self.assertFalse(valid_address('a b@c.com'))
        self.assertFalse(valid_address(''))

    def test_digest_times_are_normalised_and_rejected_when_invalid(self):
        self.assertEqual(valid_digest_times(['8:00', '20:00', '8:00']), ['08:00', '20:00'])
        self.assertEqual(valid_digest_times(['9：05']), ['09:05'])
        self.assertIsNone(valid_digest_times(['24:00']))
        self.assertIsNone(valid_digest_times(['8点']))
        self.assertIsNone(valid_digest_times('08:00'))
        self.assertEqual(valid_digest_times([]), [])


class ValidateTests(unittest.TestCase):
    def base(self, **overrides):
        config = dict(enabled=True, recipient='me@qq.com', sender='me@qq.com', server='smtp.qq.com',
                      port=465, security='ssl', password='授权码', digest_times=[])
        config.update(overrides)
        return config

    def test_missing_recipient_is_explained(self):
        with self.assertRaises(EmailError) as error:
            validate(self.base(recipient=''))
        self.assertIn('接收提醒的邮箱', str(error.exception))

    def test_malformed_recipient_is_explained(self):
        with self.assertRaises(EmailError):
            validate(self.base(recipient='nope'))

    def test_disabled_config_needs_no_server_or_password(self):
        settings = validate(dict(enabled=False, recipient='me@qq.com', server='', password=''))
        self.assertFalse(settings.enabled)

    def test_enabled_config_requires_server_password_and_known_security(self):
        with self.assertRaises(EmailError):
            validate(self.base(server=''))
        with self.assertRaises(EmailError):
            validate(self.base(password=''))
        with self.assertRaises(EmailError):
            validate(self.base(security='plain'))
        with self.assertRaises(EmailError):
            validate(self.base(port=70000))

    def test_smtp_is_the_registered_default_channel(self):
        self.assertIsInstance(get_notifier(), SmtpNotifier)
        self.assertEqual(get_notifier().name, 'smtp')
        with self.assertRaises(EmailError):
            get_notifier('telegram')


class ComposeTests(unittest.TestCase):
    def test_alert_groups_items_and_names_the_window(self):
        items = [make_item('u1', '第三章作业'), make_item('u2', '单元测验', kind='测验')]
        mail = compose_alert(items, 24, NOW)
        self.assertIn('2 项', mail.subject)
        self.assertIn('24 小时', mail.subject)
        for item in items:
            self.assertIn(item.name, mail.text)
            self.assertIn(item.course, mail.text)
            self.assertIn('10月15日 23:30', mail.text)
            self.assertIn(item.course_url, mail.text)
        self.assertIn('第三章作业', mail.html)
        self.assertNotIn('{{', mail.text)

    def test_alert_orders_by_deadline_and_labels_unknown_deadline(self):
        soon = make_item('soon', '今天截止')
        soon = Assignment(**{**soon.to_dict(), 'deadline': datetime(2026, 10, 14, 12, 0, tzinfo=BEIJING)})
        unknown = make_item('unknown', '待核对作业')
        unknown = Assignment(**{**unknown.to_dict(), 'deadline': None, 'deadline_text': '待核对'})
        late = make_item('late', '下周截止')
        mail = compose_alert([late, unknown, soon], 72, NOW)
        self.assertLess(mail.text.index('今天截止'), mail.text.index('下周截止'))
        self.assertLess(mail.text.index('下周截止'), mail.text.index('待核对作业'))
        self.assertIn('截止时间待核对', mail.text)

    def test_digest_lists_every_pending_task(self):
        mail = compose_digest([make_item()], NOW)
        self.assertIn('待完成 1 项', mail.subject)
        self.assertIn('高等数学', mail.text)

    def test_html_escapes_untrusted_course_text(self):
        mail = compose_alert([make_item(name='<script>alert(1)</script>')], 24, NOW)
        self.assertNotIn('<script>', mail.html)
        self.assertIn('&lt;script&gt;', mail.html)


class SecretStoreTests(unittest.TestCase):
    def test_roundtrip_hides_the_secret(self):
        token = secrets_store.encrypt('我的授权码 abc123')
        self.assertTrue(secrets_store.protected(token))
        self.assertNotIn('abc123', token)
        self.assertEqual(secrets_store.decrypt(token), '我的授权码 abc123')
        self.assertTrue(password_saved(token))

    def test_empty_and_unreadable_tokens_are_safe(self):
        self.assertEqual(secrets_store.encrypt(''), '')
        self.assertEqual(secrets_store.decrypt(''), '')
        self.assertEqual(secrets_store.decrypt('dpapi:not-base64!!'), '')
        self.assertFalse(password_saved(''))

    def test_plain_fallback_is_marked_and_still_readable(self):
        with patch.object(secrets_store, 'available', return_value=False):
            token = secrets_store.encrypt('fallback')
        self.assertTrue(token.startswith('plain:'))
        self.assertFalse(secrets_store.protected(token))
        self.assertEqual(secrets_store.decrypt(token), 'fallback')


class MailerTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.results = []
        self.logs = []
        self.mailer = Mailer(log=self.logs.append,
                             resolve=lambda config: (validate(config, config.get('password', '明文')),
                                                     _Recorder(self.sent)),
                             on_result=lambda key, ok, detail: self.results.append((key, ok, detail)))

    def tearDown(self):
        self.mailer.stop()

    def wait_for(self, results, timeout=5.0):
        """Bounded wait so a stuck worker fails the test instead of hanging."""
        end = time.monotonic() + timeout
        while time.monotonic() < end and not results:
            time.sleep(0.02)
        return results

    def test_delivery_is_queued_and_reported(self):
        self.mailer.start()
        config = dict(enabled=True, recipient='me@qq.com', server='smtp.qq.com', password='明文')
        self.assertTrue(self.mailer.enqueue('alert|u1|24', config, Mail('主题', '正文')))
        self.assertFalse(self.mailer.enqueue('alert|u1|24', config, Mail('主题', '正文')))
        self.wait_for(self.results)
        self.assertEqual(self.sent[0].subject, '主题')
        self.assertEqual(self.results[0][0], 'alert|u1|24')
        self.assertTrue(self.results[0][1])

    def test_failures_are_reported_without_credentials(self):
        mailer = Mailer(log=self.logs.append, resolve=lambda config: (_ for _ in ()).throw(EmailError('授权码不正确')),
                        on_result=lambda key, ok, detail: self.results.append((key, ok, detail)))
        mailer.start()
        mailer.enqueue('alert|u2|3', dict(enabled=True, recipient='me@qq.com', password='topsecret'), Mail('主题', '正文'))
        self.wait_for(self.results)
        mailer.stop()
        self.assertFalse(self.results[0][1])
        self.assertIn('授权码', self.results[0][2])
        self.assertNotIn('topsecret', ''.join(self.logs))


class _Recorder(SmtpNotifier):
    def __init__(self, sink):
        self.sink = sink

    def send(self, settings, mail):
        self.sink.append(mail)
        return True


class SmtpNotifierTests(unittest.TestCase):
    def setUp(self):
        self.settings = EmailSettings(enabled=True, recipient='me@qq.com', sender='me@qq.com',
                                      server='smtp.qq.com', port=465, security='ssl', password='secret')
        self.mail = Mail('主题', '正文', '<p>正文</p>')

    def test_ssl_login_and_send(self):
        with patch('emailer.smtplib.SMTP_SSL') as factory:
            self.assertTrue(SmtpNotifier().send(self.settings, self.mail))
        factory.assert_called_once()
        client = factory.return_value
        self.assertEqual(client.login.call_args[0], ('me@qq.com', 'secret'))
        message = client.send_message.call_args[0][0]
        self.assertEqual(message['To'], 'me@qq.com')
        self.assertEqual(message['Subject'], '主题')
        self.assertIn('正文', message.get_body(preferencelist=('plain',)).get_content())

    def test_starttls_upgrade_is_used_when_selected(self):
        settings = EmailSettings(enabled=True, recipient='me@qq.com', server='smtp.office365.com',
                                 port=587, security='starttls', password='secret')
        with patch('emailer.smtplib.SMTP') as factory, patch('emailer.ssl.create_default_context'):
            SmtpNotifier().send(settings, self.mail)
        self.assertTrue(factory.return_value.starttls.called)
        self.assertFalse(factory.return_value.send_message.call_args is None)

    def test_authentication_failure_is_phrased_for_the_user(self):
        import smtplib
        with patch('emailer.smtplib.SMTP_SSL') as factory:
            factory.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b'bad')
            with self.assertRaises(EmailError) as error:
                SmtpNotifier().send(self.settings, self.mail)
        self.assertIn('授权码', str(error.exception))
        self.assertNotIn('secret', str(error.exception))

    def test_network_failure_is_phrased_for_the_user(self):
        with patch('emailer.smtplib.SMTP_SSL', side_effect=OSError('unreachable')):
            with self.assertRaises(EmailError) as error:
                SmtpNotifier().send(self.settings, self.mail)
        self.assertIn('无法连接邮件服务器', str(error.exception))


class DigestScheduleTests(unittest.TestCase):
    def test_only_the_matching_minute_fires_once(self):
        state = {}
        self.assertEqual(due_digests(['08:00', '20:00'], state, NOW), ['08:00'])
        self.assertEqual(due_digests(['08:00', '20:00'], state, NOW), [])
        later = NOW.replace(hour=20)
        self.assertEqual(due_digests(['08:00', '20:00'], state, later), ['20:00'])
        self.assertEqual(due_digests(['08:00'], state, NOW.replace(day=15)), ['08:00'])

    def test_malformed_entries_are_ignored(self):
        self.assertEqual(due_digests(['8:0', 'nonsense'], {}, NOW), [])


class EmailSettingsStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def test_password_is_encrypted_at_rest_and_kept_when_omitted(self):
        config = normalize_email_settings({'email': dict(enabled=True, recipient='me@qq.com',
                                                         server='smtp.qq.com', password='授权码')})
        self.assertTrue(secrets_store.protected(config['password']))
        self.assertEqual(secrets_store.decrypt(config['password']), '授权码')
        kept = normalize_email_settings({'email': dict(enabled=True, recipient='me@qq.com')}, config)
        self.assertEqual(kept['password'], config['password'])

    def test_times_and_flags_are_validated(self):
        with self.assertRaises(ValueError):
            normalize_email_settings({'email': dict(enabled=True, recipient='me@qq.com', digest_times=['25:00'])})
        with self.assertRaises(ValueError):
            normalize_email_settings({'email': dict(enabled=True, recipient='me@qq.com', security='plain')})
        with self.assertRaises(ValueError):
            normalize_email_settings({'email': 'not-a-dict'})

    def test_store_persists_settings_and_send_log(self):
        store = Store(self.temp.name)
        store.update_settings({**store.data['settings'], 'email': normalize_email_settings(
            {'email': dict(enabled=True, recipient='me@qq.com', server='smtp.qq.com', password='授权码', digest_times=['08:00'])})})
        store.data['email_log']['alert|u1|24'] = 'sent 2026-10-14T08:00:00+08:00'
        store.save()
        reopened = Store(self.temp.name)
        self.assertEqual(reopened.data['settings']['email']['digest_times'], ['08:00'])
        self.assertTrue(secrets_store.protected(reopened.data['settings']['email']['password']))
        self.assertEqual(reopened.data['email_log']['alert|u1|24'], 'sent 2026-10-14T08:00:00+08:00')

    def test_changing_the_recipient_clears_the_send_log(self):
        store = Store(self.temp.name)
        store.data['email_log']['alert|u1|24'] = 'sent'
        store.update_settings({**store.data['settings'], 'email': normalize_email_settings(
            {'email': dict(enabled=True, recipient='other@qq.com')})})
        self.assertEqual(store.data['email_log'], {})

    def test_email_log_is_bounded_and_pruned_with_tasks(self):
        store = Store(self.temp.name)
        store.data['email_log'] = {f'alert|rev{i}|24': f'sent {i:04d}' for i in range(600)}
        store.prune_email_log({'rev599'})
        self.assertLessEqual(len(store.data['email_log']), 500)
        self.assertIn('alert|rev599|24', store.data['email_log'])


if __name__ == '__main__':
    unittest.main()
