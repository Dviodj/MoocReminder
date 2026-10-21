import _bootstrap  # noqa: F401  (puts src/ on sys.path)

import json
import tempfile
import unittest
from pathlib import Path

import secrets_store
from core import DEFAULT_SETTINGS
from profiles import Profiles, account_label, folder_for_store, merge_settings, profile_key, split_settings
from storage import Store, normalize_email_settings

HOME_A = 'https://www.icourse163.org/home.htm?userId=111111#/home/course'
HOME_B = 'https://www.icourse163.org/home.htm?userId=222222#/home/course'


class ProfileKeyTests(unittest.TestCase):
    def test_mooc_user_id_names_the_profile(self):
        self.assertEqual(profile_key(HOME_A, '主账号'), '111111')
        self.assertEqual(profile_key(HOME_B), '222222')

    def test_placeholder_and_empty_home_fall_back_to_a_slug(self):
        self.assertEqual(profile_key(DEFAULT_SETTINGS['home_url'], '我的账号'), '我的账号')
        self.assertEqual(profile_key('', ''), 'default')
        self.assertTrue(profile_key('', 'a/b\\c:d*e?').find('/') < 0)

    def test_label_prefers_name_then_user_id(self):
        self.assertEqual(account_label(dict(name='主账号', home_url=HOME_A)), '主账号')
        self.assertEqual(account_label(dict(home_url=HOME_A)), '慕课账号 111111')
        self.assertEqual(account_label({}), '默认账号')


class SettingsSplitTests(unittest.TestCase):
    def test_shared_and_account_settings_are_separated(self):
        settings = {**DEFAULT_SETTINGS, 'home_url': HOME_A, 'interval_minutes': 15}
        shared, per_account = split_settings(settings)
        self.assertEqual(shared, dict(interval_minutes=15, reminder_hours=[72, 24, 3],
                                      include_quizzes=True, include_finished=False,
                                      autostart_prompted=False, update_check=True,
                                      usage_status={}))
        self.assertNotIn('home_url', shared)
        # Dismissed announcements belong to one account's view, not to the
        # program: switching accounts must not carry them over.
        self.assertNotIn('announcement_seen', shared)
        # The install ID is one machine, not one account.
        self.assertNotIn('usage_report', shared)
        self.assertEqual(per_account['home_url'], HOME_A)

    def test_merged_settings_let_a_profile_override_shared_defaults(self):
        merged = merge_settings(dict(interval_minutes=15), dict(interval_minutes=45, home_url=HOME_A))
        self.assertEqual(merged['interval_minutes'], 45)
        self.assertEqual(merged['home_url'], HOME_A)
        self.assertEqual(merged['reminder_hours'], DEFAULT_SETTINGS['reminder_hours'])


class ProfileFolderTests(unittest.TestCase):
    def test_store_inside_accounts_resolves_to_the_data_folder(self):
        root = Path(tempfile.gettempdir())/'example-data'
        self.assertEqual(folder_for_store(root/'accounts'/'111111'), root)
        self.assertEqual(folder_for_store(root), root)


class RegistrationTests(unittest.TestCase):
    """Accounts are local and passwordless: a name is the only handle."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.profiles = Profiles(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_name_is_required(self):
        with self.assertRaises(ValueError):
            self.profiles.register('   ')

    def test_registering_takes_over_the_placeholder(self):
        self.assertEqual([e['key'] for e in self.profiles.list_profiles()], ['default'])
        key = self.profiles.register('主账号')
        self.assertEqual(key, '主账号')
        self.assertEqual([e['key'] for e in self.profiles.account_options()], ['主账号'])
        self.assertEqual(self.profiles.active, '主账号')

    def test_a_new_account_starts_unbound(self):
        self.profiles.register('主账号')
        store = self.profiles.load_store()
        self.assertEqual(store.binding_state(), dict(mooc=False, email=False))
        self.assertFalse(store.is_bound())

    def test_binding_both_halves_completes_the_account(self):
        self.profiles.register('主账号')
        store = self.profiles.load_store()
        store.data['settings']['home_url'] = HOME_A
        store.save()
        self.assertEqual(store.binding_state(), dict(mooc=True, email=False))
        store.data['settings']['email']['recipient'] = 'me@qq.com'
        store.save()
        self.assertTrue(store.is_bound())

    def test_the_placeholder_home_url_does_not_count_as_bound(self):
        self.profiles.register('主账号')
        store = self.profiles.load_store()
        self.assertEqual(store.data['settings']['home_url'], DEFAULT_SETTINGS['home_url'])
        self.assertFalse(store.binding_state()['mooc'])

    def test_duplicate_names_are_kept_apart(self):
        first = self.profiles.register('我的账号')
        second = self.profiles.register('我的账号')
        self.assertEqual(first, '我的账号')
        self.assertEqual(second, '我的账号-2')
        labels = sorted(entry['label'] for entry in self.profiles.account_options())
        self.assertEqual(labels, ['我的账号', '我的账号 2'])

    def test_login_switches_between_registered_accounts(self):
        first = self.profiles.register('主账号')
        second = self.profiles.register('选修课')
        self.assertEqual(self.profiles.active, second)
        self.profiles.login(first)
        self.assertEqual(self.profiles.active, first)
        self.assertEqual(Profiles(self.temp.name).active, first)

    def test_a_second_account_inherits_the_email_but_not_the_mooc_binding(self):
        first = self.profiles.register('主账号')
        store = self.profiles.load_store()
        store.data['settings']['home_url'] = HOME_A
        store.data['settings']['email'].update(recipient='me@qq.com', server='smtp.qq.com', password='授权码')
        store.save()
        second = self.profiles.register('选修课')
        fresh = self.profiles.load_store(second)
        # The email binding carries over on purpose, the MOOC account must not.
        self.assertEqual(fresh.binding_state(), dict(mooc=False, email=True))
        self.assertEqual(fresh.data['settings']['email']['recipient'], 'me@qq.com')
        self.assertEqual(self.profiles.load_store(first).data['settings']['home_url'], HOME_A)

    def test_registered_accounts_are_listed_even_before_binding(self):
        self.profiles.register('主账号')
        self.profiles.register('选修课')
        labels = sorted(entry['label'] for entry in self.profiles.account_options())
        self.assertEqual(labels, ['主账号', '选修课'])


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.folder = Path(self.temp.name)
        self.profiles = Profiles(self.folder)

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, key=None, **email):
        store = self.profiles.load_store(key)
        store.data['settings']['email'].update(email)
        store.save()
        self.profiles.save_shared(store.data['settings'])
        return store

    def test_first_real_account_replaces_the_placeholder(self):
        self.assertEqual([entry['key'] for entry in self.profiles.list_profiles()], ['default'])
        key = self.profiles.create_profile(name='主账号', home_url=HOME_A, activate=True)
        self.assertEqual(key, '111111')
        entries = self.profiles.list_profiles()
        self.assertEqual([entry['key'] for entry in entries], ['111111'])
        self.assertEqual(entries[0]['label'], '主账号')

    def test_new_account_inherits_the_email_binding(self):
        self.profiles.create_profile(name='主账号', home_url=HOME_A, activate=True)
        self.configure(recipient='me@qq.com', server='smtp.qq.com', password='授权码')
        key = self.profiles.create_profile(name='小号', home_url=HOME_B, inherit_from='111111', activate=True)
        inherited = self.profiles.load_store(key).data['settings']
        self.assertEqual(inherited['email']['recipient'], 'me@qq.com')
        self.assertEqual(inherited['email']['server'], 'smtp.qq.com')
        self.assertTrue(secrets_store.protected(inherited['email']['password']))
        self.assertEqual(inherited['name'], '小号')
        self.assertEqual(inherited['home_url'], HOME_B)

    def test_each_account_keeps_its_own_tasks_and_marks(self):
        self.profiles.create_profile(name='主账号', home_url=HOME_A, activate=True)
        first = self.profiles.load_store()
        first.data['assignments'] = [dict(uid='u1', course='课程A', course_url=HOME_A, name='作业',
                                          kind='作业', deadline=None, deadline_text='', status='pending', checked_at='')]
        first.data['completed'] = ['u1|']
        first.save()
        second_key = self.profiles.create_profile(name='小号', home_url=HOME_B, activate=True)
        second = self.profiles.load_store(second_key)
        self.assertEqual(second.data['assignments'], [])
        self.assertEqual(second.data['completed'], [])
        restored = self.profiles.load_store('111111')
        self.assertEqual(len(restored.data['assignments']), 1)

    def test_the_active_account_survives_a_restart(self):
        self.profiles.create_profile(name='主账号', home_url=HOME_A, activate=True)
        self.profiles.create_profile(name='小号', home_url=HOME_B, activate=True)
        reopened = Profiles(self.folder)
        self.assertEqual(reopened.active, '222222')
        self.assertEqual(len(reopened.list_profiles()), 2)
        reopened.set_active('111111')
        self.assertEqual(Profiles(self.folder).active, '111111')

    def test_switching_to_an_unknown_account_is_refused(self):
        with self.assertRaises(ValueError):
            self.profiles.set_active('nope')

    def test_inheriting_from_an_unknown_account_is_refused(self):
        self.profiles.create_profile(name='主账号', home_url=HOME_A, activate=True)
        with self.assertRaises(ValueError):
            self.profiles.create_profile(name='x', home_url=HOME_B, inherit_from='nope')

    def test_invalid_home_url_is_refused(self):
        with self.assertRaises(ValueError):
            self.profiles.create_profile(name='x', home_url='https://example.com/')

    def test_shared_settings_apply_to_every_account(self):
        self.profiles.create_profile(name='主账号', home_url=HOME_A, activate=True)
        store = self.profiles.load_store()
        store.data['settings']['interval_minutes'] = 15
        store.save()
        self.profiles.save_shared(store.data['settings'])
        key = self.profiles.create_profile(name='小号', home_url=HOME_B, activate=True)
        self.assertEqual(self.profiles.load_store(key).data['settings']['interval_minutes'], 15)
        self.assertEqual(self.profiles.load_store('111111').data['settings']['interval_minutes'], 15)

    def test_legacy_single_file_setup_is_adopted_without_loss(self):
        legacy_folder = Path(tempfile.mkdtemp(dir=self.folder))
        store = Store(legacy_folder)
        store.data['settings'].update(home_url=HOME_A, name='旧账号')
        store.data['settings']['email'] = normalize_email_settings(
            {'email': dict(enabled=True, recipient='old@qq.com', server='smtp.qq.com', password='pw')})
        store.data['assignments'] = [dict(uid='u1', course='课程A', course_url=HOME_A, name='作业',
                                          kind='作业', deadline=None, deadline_text='', status='pending', checked_at='')]
        store.save()
        root = Path(tempfile.mkdtemp())
        try:
            (root/'data.json').write_bytes((legacy_folder/'data.json').read_bytes())
            adopted = Profiles(root)
            self.assertEqual(adopted.active, '111111')
            restored = adopted.load_store()
            self.assertEqual(restored.data['settings']['name'], '旧账号')
            self.assertEqual(restored.data['settings']['email']['recipient'], 'old@qq.com')
            self.assertEqual(len(restored.data['assignments']), 1)
            self.assertFalse((root/'data.json').exists())
            self.assertTrue((root/'data.migrated.json').is_file())
        finally:
            for path in sorted(root.rglob('*'), reverse=True):
                path.unlink() if path.is_file() else path.rmdir()
            root.rmdir()

    def test_removing_an_account_keeps_a_backup_and_one_profile(self):
        self.profiles.create_profile(name='主账号', home_url=HOME_A, activate=True)
        self.profiles.create_profile(name='小号', home_url=HOME_B, activate=True)
        backup = self.profiles.forget_profile('222222')
        self.assertTrue(backup.is_file())
        self.assertIn('222222', backup.name)
        self.assertEqual([entry['key'] for entry in self.profiles.list_profiles()], ['111111'])
        self.assertEqual(self.profiles.active, '111111')
        with self.assertRaises(ValueError):
            self.profiles.forget_profile('111111')


if __name__ == '__main__':
    unittest.main()
