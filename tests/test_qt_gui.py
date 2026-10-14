"""Verify displayed scan updates on the actual desktop widgets."""
import gc
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from qt_bootstrap import prepare_qt

repo = Path(__file__).resolve().parents[1]
# prepare_qt keeps the Qt DLLs together by pointing TEMP/TMP at the repository,
# which is right for the program but wrong for a test run: every other module
# would then create its temporary directories inside the checkout. Only the DLL
# setup is wanted here, so the environment is restored right away.
_saved_temp = {name: os.environ.get(name) for name in ('TEMP', 'TMP')}
prepare_qt(repo / 'qt-test-data')
for _name, _value in _saved_temp.items():
    if _value is None:
        os.environ.pop(_name, None)
    else:
        os.environ[_name] = _value

from PySide6.QtWidgets import QApplication
from qt_desktop import Window, EmailSettingsForm, LockScreen, RegisterDialog
from core import Course, COURSE_URL, parse_assignment, BEIJING
from datetime import datetime
from storage import Store
import secrets_store
import profiles as profiles_module


class QtGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_changed_deadline_and_status_replace_visible_old_values(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = Store(folder)
            course = Course('概率论', COURSE_URL)
            first = parse_assignment(dict(name='第2章作业', deadline='2026-10-18 23:30', status=''), course)
            second = parse_assignment(dict(name='第2章作业', deadline='2026-10-18 23:45', status='未提交'), course)
            store.merge_scan([first], [COURSE_URL])
            with patch('portal.now_beijing', return_value=datetime(2026,10,13,12,tzinfo=BEIJING)):
                window = Window(store)
                try:
                    self.assertEqual(window.table.item(0,3).text(), '10月18日 23:30')
                    self.assertEqual(window.table.item(0,5).text(), '请核对')
                    store.merge_scan([second], [COURSE_URL])
                    window.refresh()
                    self.assertEqual(window.table.item(0,3).text(), '10月18日 23:45')
                    self.assertEqual(window.table.item(0,5).text(), '未提交')
                finally:
                    window.controller.stop()
                    window.controller.worker.join(timeout=2)
                    window.close()


class EmailSettingsFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self, password='', **overrides):
        email = dict(enabled=True, recipient='me@qq.com', server='smtp.qq.com', port=465,
                     security='ssl', password=password, digest_times=['08:00'])
        email.update(overrides)
        return EmailSettingsForm(email)

    def test_stored_values_are_shown_back(self):
        form = self.make(digest_times=['08:00', '20:00'])
        self.assertTrue(form.enabled.isChecked())
        self.assertEqual(form.recipient.text(), 'me@qq.com')
        self.assertEqual(form.port.value(), 465)
        self.assertEqual(form.security.currentData(), 'ssl')
        self.assertEqual(form.digest.text(), '08:00,20:00')

    def test_saved_code_is_never_resubmitted_unless_the_user_typed_one(self):
        token = secrets_store.encrypt('授权码')
        form = self.make(password=token)
        self.assertNotIn('password', form.values())
        self.assertIn('已保存', form.password.placeholderText())
        form.password.setText('新授权码')
        form.mark_password()
        self.assertEqual(form.values()['password'], '新授权码')

    def test_clearing_the_saved_code_submits_an_explicit_empty_value(self):
        form = self.make(password=secrets_store.encrypt('授权码'))
        self.assertTrue(form.clear_password.isVisibleTo(form))
        form.forget()
        self.assertEqual(form.values()['password'], '')
        self.assertFalse(form.saved_password)
        self.assertIn('重新填写', form.hint.text())

    def test_a_recipient_alone_is_enough_and_does_not_touch_the_server(self):
        """Ordinary users only give their own address; the sender is shipped."""
        form = self.make(recipient='', server='', port=465)
        form.recipient.setText('someone@163.com')
        self.assertEqual(form.server.text(), '')
        self.assertIn('预置的发件邮箱', form.hint.text())
        self.assertFalse(form.advanced.isVisibleTo(form))

    def test_advanced_mode_suggests_a_server_for_the_sender(self):
        form = self.make(recipient='', server='', port=465)
        form.advanced_toggle.setChecked(True)
        form.sender.setText('someone@163.com')
        self.assertEqual(form.server.text(), 'smtp.163.com')
        self.assertEqual(form.port.value(), 465)
        self.assertTrue(form.values()['advanced'])

    def test_a_manually_typed_server_is_not_overwritten(self):
        form = self.make(recipient='', server='mail.mycompany.com', port=2525)
        form.advanced_toggle.setChecked(True)
        form.sender.setText('someone@163.com')
        self.assertEqual(form.server.text(), 'mail.mycompany.com')
        self.assertEqual(form.port.value(), 2525)

    def test_unknown_provider_is_reported_and_custom_server_kept(self):
        form = self.make(recipient='', server='', port=465)
        form.advanced_toggle.setChecked(True)
        form.sender.setText('someone@company.example')
        self.assertIn('手动填写', form.hint.text())
        self.assertEqual(form.server.text(), '')

    def test_digest_times_accept_full_width_commas_and_are_split(self):
        form = self.make()
        form.digest.setText('08:00，20:00 , 12:30')
        self.assertEqual(form.values()['digest_times'], ['08:00', '20:00', '12:30'])
        form.digest.setText('')
        self.assertEqual(form.values()['digest_times'], [])

    def test_test_request_emits_the_form_values_and_needs_a_recipient(self):
        form = self.make()
        received = []
        form.test_requested.connect(received.append)
        form.request_test()
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]['recipient'], 'me@qq.com')
        self.assertNotIn('password', received[0])
        form.recipient.setText('')
        form.request_test()
        self.assertEqual(len(received), 1)
        self.assertIn('请先填写', form.hint.text())

    def test_window_reports_the_delivery_result_in_the_dialog(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            window = Window(Store(folder))
            try:
                form = self.make(password=secrets_store.encrypt('授权码'))
                with patch.object(window.controller, 'send_test_email', return_value=True):
                    window.controller.email_message = '邮件已发送：测试'
                    self.assertTrue(window.mailer_idle())
                    self.assertTrue(window.send_test_email(None, form, form.values()))
                self.assertIn('邮件已发送', form.hint.text())
                broken = self.make()
                with patch.object(window.controller, 'send_test_email', side_effect=ValueError('邮箱授权码不正确')):
                    self.assertFalse(window.send_test_email(None, broken, broken.values()))
                self.assertIn('授权码不正确', broken.hint.text())
            finally:
                window.controller.stop()
                window.controller.worker.join(timeout=2)
                window.close()


class EntryGateTests(unittest.TestCase):
    """The program asks which account to work on before showing the workspace."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.profiles = profiles_module.Profiles(self.temp.name)
        self.widgets = []

    def tearDown(self):
        # Qt owns these widgets; destroy them explicitly so nothing outlives the
        # interpreter, which crashes the process on exit otherwise.
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.widgets = []
        self.app.processEvents()
        gc.collect()
        self.temp.cleanup()

    def gate(self):
        widget = LockScreen(self.profiles)
        self.widgets.append(widget)
        return widget

    def register_dialog(self):
        widget = RegisterDialog(self.profiles)
        self.widgets.append(widget)
        return widget

    def test_a_fresh_install_offers_registration_and_no_entry(self):
        gate = self.gate()
        self.assertEqual(gate.accounts.count(), 0)
        self.assertFalse(gate.enter.isEnabled())
        self.assertFalse(gate.accounts.isEnabled())
        self.assertIn('还没有账号', gate.summary.text())
        self.assertEqual(gate.register_button.text(), '注册账号')

    def test_registering_from_the_gate_creates_and_enters_the_account(self):
        key = self.profiles.register('主账号')
        gate = self.gate()
        self.assertEqual([gate.accounts.itemText(i) for i in range(gate.accounts.count())], ['主账号'])
        self.assertTrue(gate.enter.isEnabled())
        gate.accounts.setCurrentIndex(0)
        gate.enter_selected()
        self.assertEqual(gate.key, key)
        self.assertEqual(self.profiles.active, key)
        self.assertEqual(gate.result(), 1)

    def test_the_gate_hides_the_untouched_placeholder(self):
        self.profiles.register('主账号')
        gate = self.gate()
        labels = [gate.accounts.itemText(i) for i in range(gate.accounts.count())]
        self.assertEqual(labels, ['主账号'])

    def test_register_dialog_refuses_a_blank_name(self):
        dialog = self.register_dialog()
        dialog.name.setText('   ')
        dialog.submit.click()
        self.assertIsNone(dialog.key)
        self.assertIn('账号名称', dialog.error.text())
        self.assertEqual(self.profiles.account_options(), [])

    def test_register_dialog_creates_an_account_from_the_name(self):
        dialog = self.register_dialog()
        dialog.name.setText('选修课账号')
        dialog.submit.click()
        self.assertEqual(dialog.key, '选修课账号')
        self.assertEqual(dialog.result(), 1)
        self.assertEqual([e['label'] for e in self.profiles.account_options()], ['选修课账号'])


class AccountUiTests(unittest.TestCase):
    """The email entry point and account switching must work before signing in."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.profiles = profiles_module.Profiles(self.temp.name)
        self.window = Window(self.profiles.load_store(), profiles=self.profiles)

    def tearDown(self):
        self.window.controller.stop()
        self.window.controller.worker.join(timeout=2)
        self.window.close()
        self.temp.cleanup()

    def test_email_settings_are_reachable_without_signing_in(self):
        self.assertTrue(self.window.email_button.isEnabled())
        self.assertIn('无需先登录', self.window.email_button.toolTip())
        # The dialog is built from stored settings and needs no MOOC connection.
        email = self.window.store.data['settings']['email']
        self.assertFalse(email['enabled'])
        self.assertFalse(self.window.controller.connected)

    def test_saving_an_email_before_login_changes_only_the_email(self):
        before = self.window.store.data['settings']['home_url']
        self.window.controller.settings({'email': dict(enabled=True, recipient='me@qq.com',
                                                       server='smtp.qq.com', port=465, security='ssl',
                                                       password='授权码', digest_times=['08:00'])})
        after = self.window.store.data['settings']
        self.assertTrue(after['email']['enabled'])
        self.assertEqual(after['email']['digest_times'], ['08:00'])
        self.assertEqual(after['home_url'], before)

    def test_account_combo_lists_every_account_plus_add(self):
        self.profiles.create_profile(name='主账号',
                                     home_url='https://www.icourse163.org/home.htm?userId=111111#/home/course',
                                     activate=True)
        self.window.refresh_accounts()
        labels = [self.window.account_combo.itemText(i) for i in range(self.window.account_combo.count())]
        self.assertIn('主账号', labels)
        self.assertEqual(labels[-1], '添加账号…')
        self.assertEqual(self.window.account_combo.currentData(), self.profiles.active)

    def test_switching_accounts_swaps_the_store_and_remembers_each_one(self):
        self.profiles.create_profile(name='主账号',
                                     home_url='https://www.icourse163.org/home.htm?userId=111111#/home/course',
                                     activate=True)
        self.window.store = self.profiles.load_store()
        self.window.controller.replace_store(self.window.store)
        self.window.controller.settings({'email': dict(enabled=True, recipient='me@qq.com',
                                                       server='smtp.qq.com', password='授权码')})
        self.assertEqual(self.window.current_account_key(), '111111')
        key = self.profiles.create_profile(name='小号',
                                           home_url='https://www.icourse163.org/home.htm?userId=222222#/home/course',
                                           inherit_from='111111', activate=True)
        self.window.switch_account(key)
        self.assertEqual(self.window.store.data['settings']['name'], '小号')
        self.assertEqual(self.window.store.data['settings']['email']['recipient'], 'me@qq.com')
        self.assertEqual(self.window.current_account_key(), '222222')
        self.assertIs(self.window.controller.store, self.window.store)
        self.window.switch_account('111111')
        self.assertEqual(self.window.store.data['settings']['name'], '主账号')
        self.assertEqual(self.window.store.data['settings']['email']['recipient'], 'me@qq.com')
        self.assertEqual(self.profiles.active, '111111')

    def test_switching_to_the_current_account_is_a_no_op(self):
        worker = self.window.controller.worker
        current = self.window.current_account_key()
        self.window.switch_account(current)
        self.assertIs(self.window.controller.worker, worker)


if __name__ == '__main__':
    unittest.main()
