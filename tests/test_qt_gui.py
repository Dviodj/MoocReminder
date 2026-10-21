"""Verify displayed scan updates on the actual desktop widgets."""

import _bootstrap  # noqa: F401  (puts src/ on sys.path)
import gc
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from _bootstrap import scratch_folder

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
from qt_desktop import Window, EmailSettingsForm, EmailLogPage, LockScreen, RegisterDialog
from core import Course, COURSE_URL, parse_assignment, BEIJING
from datetime import datetime
from storage import Store
import secrets_store
import profiles as profiles_module
import usage_ping
import self_update


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
                window = Window(store, tray=False)
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

    def make(self, password='', folder=None, **overrides):
        email = dict(enabled=True, recipient='me@qq.com', server='smtp.qq.com', port=465,
                     security='ssl', password=password, digest_times=['08:00'])
        email.update(overrides)
        # An empty folder unless the test asks for a shipped sender: leaving it
        # unset makes the form read the publisher's real email_sender.json, so
        # the test would measure this machine instead of the rule it states.
        return EmailSettingsForm(email, None, scratch_folder('no-shipped-sender') if folder is None else folder)

    def shipped_folder(self, **values):
        """A folder holding a publisher sender config, for the shipped path."""
        folder = scratch_folder('shipped-sender')
        config = dict(sender='reminder@example.com', server='smtp.example.com',
                      port=465, security='ssl', password='admin-secret')
        config.update(values)
        (folder/'email_sender.json').write_text(json.dumps(config), encoding='utf-8')
        return folder

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
        form = self.make(recipient='', server='', port=465, folder=self.shipped_folder())
        form.recipient.setText('someone@163.com')
        self.assertEqual(form.server.text(), '')
        self.assertFalse(form.advanced.isVisibleTo(form))
        # Naming the address matters: an unrecognised sender looks like spam.
        self.assertIn('reminder@example.com', form.hint.text())

    def test_a_build_without_a_shipped_sender_says_so_instead_of_pretending(self):
        """A source checkout has no email_sender.json; the form must not promise one."""
        form = self.make(recipient='', server='')
        # Nothing can be sent from a shipped address here, so the choice is made
        # for the user rather than offered: their own mailbox is the only way.
        self.assertFalse(form.official.isEnabled())
        self.assertTrue(form.custom.isChecked())
        self.assertTrue(form.advanced.isVisibleTo(form))
        self.assertIn('没有预置', form.official.text())
        self.assertIn('没有内置发件邮箱', form.shipped_note())

    def test_the_shipped_sender_is_the_default_choice(self):
        """Two alternatives, stated as a choice — not an "advanced" switch."""
        form = self.make(recipient='me@qq.com', folder=self.shipped_folder())
        self.assertTrue(form.official.isChecked())
        self.assertFalse(form.custom.isChecked())
        self.assertFalse(form.advanced.isVisibleTo(form))
        self.assertFalse(form.values()['advanced'])
        form.custom.setChecked(True)
        self.assertTrue(form.advanced.isVisibleTo(form))
        self.assertTrue(form.values()['advanced'])

    def test_a_stored_custom_choice_reopens_on_the_custom_option(self):
        form = self.make(recipient='me@qq.com', advanced=True, folder=self.shipped_folder())
        self.assertTrue(form.custom.isChecked())
        self.assertTrue(form.advanced.isVisibleTo(form))

    def test_choosing_a_custom_sender_suggests_a_server_for_the_sender(self):
        form = self.make(recipient='', server='', port=465)
        form.custom.setChecked(True)
        form.sender.setText('someone@163.com')
        self.assertEqual(form.server.text(), 'smtp.163.com')
        self.assertEqual(form.port.value(), 465)
        self.assertTrue(form.values()['advanced'])

    def test_a_manually_typed_server_is_not_overwritten(self):
        form = self.make(recipient='', server='mail.mycompany.com', port=2525)
        form.custom.setChecked(True)
        form.sender.setText('someone@163.com')
        self.assertEqual(form.server.text(), 'mail.mycompany.com')
        self.assertEqual(form.port.value(), 2525)

    def test_unknown_provider_is_reported_and_custom_server_kept(self):
        form = self.make(recipient='', server='', port=465)
        form.custom.setChecked(True)
        form.sender.setText('someone@company.example')
        self.assertIn('手动填写', form.hint.text())
        self.assertEqual(form.server.text(), '')

    def test_digest_times_accept_full_width_commas_and_are_split(self):
        form = self.make()
        form.digest.setText('08:00，20:00 , 12:30')
        self.assertEqual(form.values()['digest_times'], ['08:00', '20:00', '12:30'])
        form.digest.setText('')
        self.assertEqual(form.values()['digest_times'], [])

    def test_a_daily_summary_is_the_default_and_hides_the_extra_controls(self):
        form = self.make()
        self.assertEqual(form.digest_frequency.currentData(), 'daily')
        self.assertFalse(form.digest_weekday.isVisibleTo(form))
        self.assertFalse(form.digest_interval.isVisibleTo(form))
        self.assertIn('每天发一封汇总邮件', form.digest_hint.text())
        self.assertEqual(form.values()['digest_frequency'], 'daily')

    def test_a_stored_cadence_is_shown_back(self):
        form = self.make(digest_frequency='weekly', digest_weekday=2, digest_interval=5)
        self.assertEqual(form.digest_frequency.currentData(), 'weekly')
        self.assertEqual(form.digest_weekday.currentData(), 2)
        self.assertEqual(form.digest_interval.value(), 5)
        self.assertEqual(form.values()['digest_weekday'], 2)

    def test_choosing_a_weekly_summary_asks_for_the_weekday_only(self):
        form = self.make()
        form.digest_frequency.setCurrentIndex(form.digest_frequency.findData('weekly'))
        self.assertTrue(form.digest_weekday.isVisibleTo(form))
        self.assertFalse(form.digest_interval.isVisibleTo(form))
        form.digest_weekday.setCurrentIndex(5)
        self.assertEqual(form.values()['digest_weekday'], 5)
        self.assertIn('每周六', form.digest_hint.text())

    def test_choosing_every_n_days_asks_for_the_number_only(self):
        form = self.make()
        form.digest_frequency.setCurrentIndex(form.digest_frequency.findData('interval'))
        self.assertFalse(form.digest_weekday.isVisibleTo(form))
        self.assertTrue(form.digest_interval.isVisibleTo(form))
        form.digest_interval.setValue(4)
        self.assertEqual(form.values()['digest_interval'], 4)
        self.assertIn('每 4 天', form.digest_hint.text())

    def test_an_every_n_days_summary_reopens_on_its_number(self):
        form = self.make(digest_frequency='interval', digest_interval=7)
        self.assertEqual(form.digest_frequency.currentData(), 'interval')
        self.assertTrue(form.digest_interval.isVisibleTo(form))
        self.assertIn('每 7 天', form.digest_hint.text())

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
            window = Window(Store(folder), tray=False)
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
        self.window = Window(self.profiles.load_store(), profiles=self.profiles, tray=False)

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


class PageSwitchTests(unittest.TestCase):
    """The side bar switches between real pages; it used to redraw one view."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.window = Window(Store(self.temp.name), tray=False)

    def tearDown(self):
        self.window.controller.stop()
        self.window.controller.worker.join(timeout=2)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        gc.collect()
        self.temp.cleanup()

    def test_every_nav_entry_has_a_page_of_its_own(self):
        self.assertEqual(len(self.window.page_index), self.window.pages.count())
        for key in ('overview', 'assignments', 'courses', 'log', 'details'):
            self.assertIn(key, self.window.page_index)
            self.assertIn(key, self.window.nav_buttons)

    def test_the_settings_entry_is_a_dialog_and_not_a_page(self):
        self.assertIn('settings', self.window.nav_buttons)
        self.assertNotIn('settings', self.window.page_index)

    def test_the_workspace_opens_on_the_overview(self):
        self.assertEqual('overview', self.window.current_page())
        self.assertTrue(self.window.nav_buttons['overview'].property('active'))

    def test_clicking_an_entry_changes_the_visible_page(self):
        self.window.nav_buttons['assignments'].click()
        self.assertEqual('assignments', self.window.current_page())
        self.assertEqual(self.window.page_index['assignments'], self.window.pages.currentIndex())

    def test_the_active_entry_follows_the_click(self):
        self.window.nav_buttons['courses'].click()
        self.assertTrue(self.window.nav_buttons['courses'].property('active'))
        self.assertFalse(self.window.nav_buttons['overview'].property('active'))

    def test_going_back_to_the_overview_works_even_when_nothing_changed(self):
        """This was the reported bug: two entries redrew the same view."""
        self.window.nav_buttons['assignments'].click()
        self.window.nav_buttons['overview'].click()
        self.assertEqual('overview', self.window.current_page())

    def test_the_header_describes_the_page_it_shows(self):
        self.window.show_page('log')
        self.assertIn('邮件', self.window.page_title.text())
        self.assertTrue(self.window.page_subtitle.text())

    def test_the_binding_shortcut_still_reaches_its_page(self):
        self.window.show_courses()
        self.assertEqual('courses', self.window.current_page())
        self.window.details()
        self.assertEqual('details', self.window.current_page())

    def test_each_page_can_be_shown_without_an_error(self):
        for key in self.window.page_index:
            self.window.show_page(key)
            self.assertEqual(self.window.page_index[key], self.window.pages.currentIndex())


class EmailLogPageTests(unittest.TestCase):
    """The send history the user asked to be able to see."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def page(self):
        widget = EmailLogPage()
        self.widgets.append(widget)
        return widget

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.widgets = []
        self.app.processEvents()
        gc.collect()

    def test_an_empty_history_says_so_and_shows_no_rows(self):
        page = self.page()
        self.assertEqual(0, page.table.rowCount())
        self.assertTrue(page.empty.isVisibleTo(page))

    def test_rows_are_filled_newest_first(self):
        page = self.page()
        page.set_log({
            'alert|abc|24.0': 'sent 2026-02-14T08:00:00+08:00',
            'digest|2026-02-15|08:00': 'sent 2026-02-15T08:00:00+08:00',
        })
        self.assertEqual(2, page.table.rowCount())
        self.assertEqual('每日汇总', page.table.item(0, 1).text())
        self.assertEqual('02月15日 08:00', page.table.item(0, 0).text())
        self.assertEqual('已发送', page.table.item(0, 2).text())
        self.assertFalse(page.empty.isVisibleTo(page))

    def test_a_failure_shows_its_reason(self):
        page = self.page()
        page.set_log({'alert|abc|24.0': 'failed 2026-02-14T08:00:00+08:00 · 授权码不正确'})
        self.assertEqual('发送失败', page.table.item(0, 2).text())
        self.assertIn('授权码不正确', page.table.item(0, 3).text())
        self.assertIn('提前 24 小时', page.table.item(0, 3).text())

    def test_the_heading_counts_real_messages_and_ignores_test_ones(self):
        page = self.page()
        page.set_log({
            'alert|abc|24.0': 'sent 2026-02-14T08:00:00+08:00',
            'digest|2026-02-15|08:00': 'failed 2026-02-15T08:00:00+08:00',
            'test|9f2c': 'sent 2026-02-15T09:00:00+08:00',
        })
        self.assertIn('共 2 条', page.hint.text())
        self.assertIn('1 条发送失败', page.hint.text())

    def test_the_last_delivery_result_is_shown_below_the_table(self):
        page = self.page()
        page.set_log({}, '邮件已发送：测试')
        self.assertIn('邮件已发送', page.status.text())

    def test_a_missing_log_is_rendered_as_empty(self):
        page = self.page()
        page.set_log()
        self.assertEqual(0, page.table.rowCount())


class FakeTray:
    """Stands in for the notification-area icon, which no test machine has."""

    def __init__(self):
        self.messages = []
        self.pause_checked = False

    def showMessage(self, title, message, icon=None, timeout=0):
        self.messages.append((title, message))

    def show_window(self):
        self.shown = getattr(self, 'shown', 0) + 1

    def sync(self, snapshot):
        self.last_snapshot = snapshot


class AutostartUiTests(unittest.TestCase):
    """Autostart is offered once, and closing the window must not stop reminders."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.profiles = profiles_module.Profiles(self.temp.name)
        self.window = Window(self.profiles.load_store(), profiles=self.profiles, tray=False)

    def tearDown(self):
        self.window.controller.stop()
        self.window.controller.worker.join(timeout=2)
        self.window.close()
        self.temp.cleanup()

    def test_the_prompt_is_shown_until_it_is_answered(self):
        self.assertTrue(self.window.autostart.isVisibleTo(self.window))
        self.window.decline_autostart()
        self.assertFalse(self.window.autostart.isVisibleTo(self.window))
        self.assertTrue(self.profiles.shared['autostart_prompted'])

    def test_answering_the_prompt_survives_a_restart(self):
        self.window.decline_autostart()
        reopened = Window(self.profiles.load_store(), profiles=self.profiles, tray=False)
        try:
            self.assertFalse(reopened.autostart.isVisibleTo(reopened))
        finally:
            reopened.controller.stop()
            reopened.controller.worker.join(timeout=2)
            reopened.close()

    def test_accepting_installs_the_entry_and_answers_the_prompt(self):
        with patch('qt_desktop.autostart.enable') as enable, \
             patch('qt_desktop.autostart.enabled', return_value=True):
            self.window.accept_autostart()
        self.assertEqual(1, enable.call_count)
        self.assertTrue(enable.call_args.kwargs.get('python'))
        self.assertTrue(self.window.autostart_on)
        self.assertFalse(self.window.autostart.isVisibleTo(self.window))
        self.assertTrue(self.profiles.shared['autostart_prompted'])

    def test_declining_installs_nothing(self):
        with patch('qt_desktop.autostart.enable') as enable:
            self.window.decline_autostart()
        self.assertFalse(enable.called)

    def test_a_failure_to_install_is_reported_rather_than_hidden(self):
        with patch('qt_desktop.autostart.enable', side_effect=RuntimeError('同名开机启动项不属于本程序，未覆盖。')), \
             patch('qt_desktop.autostart.enabled', return_value=False), \
             patch('qt_desktop.QMessageBox.warning') as warn:
            self.window.accept_autostart()
        self.assertFalse(self.window.autostart_on)
        self.assertIn('未覆盖', warn.call_args.args[2])

    def test_the_settings_checkbox_follows_the_startup_folder(self):
        with patch('qt_desktop.autostart.enable') as enable, \
             patch('qt_desktop.autostart.disable') as disable, \
             patch('qt_desktop.autostart.enabled', return_value=True):
            self.assertEqual('', self.window.set_autostart(True))
            self.assertTrue(self.window.autostart_on)
            # The truth lives in the Startup folder, so turning it off is read
            # back from there rather than assumed from the request.
            with patch('qt_desktop.autostart.enabled', return_value=False):
                self.assertEqual('', self.window.set_autostart(False))
            self.assertFalse(self.window.autostart_on)
        self.assertEqual(1, enable.call_count)
        self.assertEqual(1, disable.call_count)

    def test_closing_the_window_keeps_reminders_running(self):
        self.window.tray = FakeTray()
        self.window.close()
        # Closing means "get out of the way", not "stop reminding me".
        self.assertFalse(self.window.controller.stopping)
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.tray.messages)

    def test_quitting_from_the_tray_stops_the_controller(self):
        self.window.tray = FakeTray()
        self.window.show()
        with patch('qt_desktop.Tray.quit_app') as quit_app:
            self.window.close()
        self.assertFalse(quit_app.called)
        self.window.quitting = True
        self.window.close()
        self.assertTrue(self.window.controller.stopping)

    def test_without_a_tray_closing_still_stops_cleanly(self):
        self.assertIsNone(self.window.tray)
        self.window.close()
        self.assertTrue(self.window.controller.stopping)


class NoticeCardTests(unittest.TestCase):
    """The version / announcement card is fed from the network, so it must be
    driven directly here; nothing in setUp talks to GitHub."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.profiles = profiles_module.Profiles(self.temp.name)
        self.window = Window(self.profiles.load_store(), profiles=self.profiles, tray=False)
        self.window.notice_data = None
        self.window.refresh()
        # These tests are about what the card says, not about how this checkout
        # was installed; claiming an installable copy keeps the ones that press
        # the update button independent of where the suite runs from.
        self.window._installable = True

    def tearDown(self):
        self.window.controller.stop()
        self.window.controller.worker.join(timeout=2)
        self.window.close()
        self.temp.cleanup()

    def deliver(self, latest='v0.2.1', announcements=(), url='https://example.com/download'):
        result = dict(latest=latest, url=url, notes='本版改动', announcements=list(announcements))
        self.window.finish_notice_check(result)
        return result

    def test_nothing_to_say_keeps_the_card_hidden(self):
        self.deliver()
        self.assertFalse(self.window.notice.isVisibleTo(self.window))

    def test_a_newer_version_offers_a_download(self):
        self.assertTrue(self.deliver(latest='v0.9.9'))
        self.assertTrue(self.window.notice.isVisibleTo(self.window))
        self.assertIn('v0.9.9', self.window.notice_title.text())
        # One press installs it; the page stays beside that as the manual route.
        self.assertEqual('打开发布页', self.window.notice_action.text())
        self.assertTrue(self.window.notice_press.isVisibleTo(self.window))
        self.assertIn('v0.9.9', self.window.notice_press.text())
        # A version card is about the program, so there is nothing to dismiss.
        self.assertFalse(self.window.notice_dismiss.isVisibleTo(self.window))

    def test_opening_the_download_page_does_not_need_a_browser(self):
        self.deliver(latest='v0.9.9')
        with patch('qt_desktop.QDesktopServices.openUrl', return_value=True) as open_url:
            self.assertTrue(self.window.open_notice())
        self.assertEqual('https://example.com/download', open_url.call_args.args[0].toString())

    def test_a_missing_browser_shows_the_address_instead(self):
        self.deliver(latest='v0.9.9')
        with patch('qt_desktop.QDesktopServices.openUrl', return_value=False):
            self.assertFalse(self.window.open_notice())
        self.assertIn('https://example.com/download', self.window.status.text())

    def test_an_announcement_shows_once_and_stays_dismissed(self):
        self.deliver(announcements=[dict(id='a1', title='临时维护', body='今晚停服', link='', level='info')])
        self.assertTrue(self.window.notice.isVisibleTo(self.window))
        self.assertEqual('临时维护', self.window.notice_title.text())
        self.window.dismiss_notice()
        self.assertFalse(self.window.notice.isVisibleTo(self.window))
        self.assertIn('a1', self.window.store.data['settings']['announcement_seen'])

    def test_a_dismissed_announcement_stays_dismissed_after_a_restart(self):
        self.deliver(announcements=[dict(id='a1', title='临时维护', body='', link='', level='info')])
        self.window.dismiss_notice()
        reopened = Window(self.profiles.load_store(), profiles=self.profiles, tray=False)
        try:
            reopened.finish_notice_check(dict(latest='v0.2.1', url='', notes='',
                                             announcements=[dict(id='a1', title='临时维护', body='',
                                                                 link='', level='info')]))
            self.assertFalse(reopened.notice.isVisibleTo(reopened))
        finally:
            reopened.controller.stop()
            reopened.controller.worker.join(timeout=2)
            reopened.close()

    def test_several_announcements_are_dismissed_one_at_a_time(self):
        self.deliver(announcements=[dict(id='a1', title='第一条', body='', link='', level='info'),
                                    dict(id='a2', title='第二条', body='', link='', level='warning')])
        self.assertEqual('第一条', self.window.notice_title.text())
        self.window.dismiss_notice()
        self.assertEqual('第二条', self.window.notice_title.text())
        self.assertTrue(self.window.notice.isVisibleTo(self.window))
        self.window.dismiss_notice()
        self.assertFalse(self.window.notice.isVisibleTo(self.window))

    def test_a_failed_check_keeps_what_is_already_on_screen(self):
        self.deliver(announcements=[dict(id='a1', title='临时维护', body='', link='', level='info')])
        self.window.finish_notice_check(None)
        self.assertTrue(self.window.notice.isVisibleTo(self.window))
        self.assertEqual('临时维护', self.window.notice_title.text())

    def test_the_last_announcement_dismissal_hides_the_card(self):
        # Closing the card must also undo what the announcement did to it,
        # otherwise the widgets underneath keep a stale state forever.
        self.deliver(announcements=[dict(id='a1', title='临时维护', body='', link='', level='info')])
        self.window.dismiss_notice()
        self.assertFalse(self.window.notice.isVisibleTo(self.window))
        self.assertTrue(self.window.notice_action.isVisibleTo(self.window.notice))
        self.assertTrue(self.window.notice_dismiss.isVisibleTo(self.window.notice))

    def test_an_announcement_body_gets_room_for_every_wrapped_line(self):
        # A one-line minimum in this row layout clipped the announcement at its
        # first line, which read as a truncated notice. The room must come from
        # the text's real height at the card's width, not from a fixed guess.
        body = ('本软件旨在帮助同学们避免忘记完成慕课作业而白白丢分。目前的版本完全依赖本地运行，'
                '没有服务器，所以还请各位每隔几天打开一下软件，会自动补发提醒邮件，或设置为开机自启。'
                '第一次尝试制作这种软件，经验不足。反馈问题 moocreminder@foxmail.com')
        self.window.show()
        QApplication.processEvents()
        try:
            self.deliver(announcements=[dict(id='a1', title='标题', body=body, link='', level='info')])
            QApplication.processEvents()
            label = self.window.notice_body
            needed = label.heightForWidth(label.width())
            self.assertGreater(needed, label.fontMetrics().lineSpacing())
            self.assertGreaterEqual(label.minimumHeight(), needed)
            self.assertGreaterEqual(label.height(), needed)
        finally:
            self.window.hide()

    def test_a_narrow_window_reflows_the_announcement_instead_of_clipping_it(self):
        # The height is recomputed on resize; a fixed one would clip the extra
        # lines the narrower width creates.
        body = ('本软件旨在帮助同学们避免忘记完成慕课作业而白白丢分。目前的版本完全依赖本地运行，'
                '没有服务器，所以还请各位每隔几天打开一下软件，会自动补发提醒邮件，或设置为开机自启。'
                '第一次尝试制作这种软件，经验不足。反馈问题 moocreminder@foxmail.com')
        self.window.resize(1240, 900)
        self.window.show()
        QApplication.processEvents()
        try:
            self.deliver(announcements=[dict(id='a1', title='标题', body=body, link='', level='info')])
            QApplication.processEvents()
            wide = self.window.notice_body.minimumHeight()
            self.window.resize(760, 900)
            QApplication.processEvents()
            label = self.window.notice_body
            self.assertGreater(label.minimumHeight(), wide)
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        finally:
            self.window.hide()

    def test_the_binding_card_leaves_the_last_line_visible(self):
        # The card used to collapse to its minimum height and clip the tail of
        # the wrapped sentence, which read as a broken layout.
        self.window.show()
        QApplication.processEvents()
        try:
            detail = self.window.binding_detail
            self.assertGreaterEqual(detail.height(), detail.sizeHint().height())
        finally:
            self.window.hide()

    def test_the_automatic_check_respects_the_switch(self):
        self.window.controller.settings(dict(update_check=False))
        with patch('qt_desktop.threading.Thread') as thread:
            self.assertFalse(self.window.start_notice_check())
        self.assertFalse(thread.called)

    def test_a_check_in_flight_is_not_started_twice(self):
        self.window.notice_busy = True
        with patch('qt_desktop.threading.Thread') as thread:
            self.assertFalse(self.window.start_notice_check())
        self.assertFalse(thread.called)


class NoticeDialogTests(unittest.TestCase):
    """The announcement window: opened at every launch, and from the sidebar."""

    HISTORY = [dict(tag='v0.2.2', url='https://example.com/tag', notes='本版改动',
                    detail='完整改动\n· 新增更新检查与公告', latest=True,
                    announcements=[dict(id='a1', title='欢迎使用', body='正文', link='', level='info'),
                                   dict(id='a2', title='重要通知', body='停服', link='', level='warning')]),
               dict(tag='v0.2.1', url='https://example.com/old', notes='更早的版本',
                    detail='', latest=False, announcements=[])]

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.profiles = profiles_module.Profiles(self.temp.name)
        self.window = Window(self.profiles.load_store(), profiles=self.profiles, tray=False)

    def tearDown(self):
        dialog = self.window.notice_dialog
        if dialog is not None:
            dialog.close()
        self.window.controller.stop()
        self.window.controller.worker.join(timeout=2)
        self.window.close()
        self.temp.cleanup()

    def deliver(self, history=None):
        history = self.HISTORY if history is None else history
        self.window.notice_history = list(history)
        self.window.notice_version = history[0]['tag'] if history else ''
        self.window.refresh()

    def test_the_window_opens_itself_once_a_check_comes_back(self):
        # A card on a page is read once and forgotten; the point of the window
        # is that nobody has to go looking for it.
        self.assertIsNone(self.window.notice_dialog)
        self.window.finish_notice_check((dict(latest='v0.2.2', url='', notes='', announcements=[]), []))
        self.assertIsNotNone(self.window.notice_dialog)
        self.assertTrue(self.window.notice_dialog.isVisible())
        self.window.notice_dialog.close()

    def test_a_failed_check_does_not_open_an_empty_window(self):
        self.window.finish_notice_check(None)
        self.assertIsNone(self.window.notice_dialog)

    def test_closing_the_window_marks_nothing_as_read(self):
        self.deliver()
        dialog = self.window.open_notice_dialog()
        self.assertIn('欢迎使用', dialog.view.toPlainText())
        dialog.close()
        # Everything stays available: the card keeps showing it and the window
        # reopens with the same content.
        self.assertEqual([], self.window.store.data['settings']['announcement_seen'])
        self.assertIn('欢迎使用', self.window.open_notice_dialog().view.toPlainText())

    def test_the_window_lists_every_version_newest_first(self):
        self.deliver()
        text = self.window.open_notice_dialog().view.toPlainText()
        self.assertLess(text.index('v0.2.2'), text.index('v0.2.1'))
        self.assertIn('完整改动', text)
        self.assertIn('新增更新检查与公告', text)
        self.assertNotIn('<', text)

    def test_a_warning_announcement_is_coloured_in_the_window(self):
        self.deliver()
        html = self.window.notice_document()
        self.assertIn('#b26a00', html)

    def test_the_window_shows_the_new_version_when_there_is_one(self):
        self.window.notice_version = 'v9.9.9'
        self.deliver()
        self.assertIn('新版本', self.window.open_notice_dialog().summary.text())

    def test_a_dismissed_announcement_is_still_listed_but_marked(self):
        self.window.controller.settings(dict(announcement_seen=['a1']))
        self.deliver()
        dialog = self.window.open_notice_dialog()
        self.assertIn('欢迎使用', dialog.view.toPlainText())
        self.assertIn('已读', dialog.view.toPlainText())
        # The card, which is the place that owns "seen", no longer offers it.
        self.assertFalse(self.window.visible_announcements() and
                         self.window.visible_announcements()[0]['id'] == 'a1')

    def test_the_window_is_as_tall_as_its_text_and_no_taller(self):
        # A text browser reports a small fixed hint; without measuring the
        # document the window opened full of empty white.
        self.deliver()
        dialog = self.window.open_notice_dialog()
        QApplication.processEvents()
        needed = dialog.view.document().size().height()
        self.assertGreater(dialog.view.height(), 0)
        self.assertLessEqual(dialog.view.height(), needed + 60)

    def test_a_long_history_is_capped_and_scrolls(self):
        long_history = [dict(tag=f'v0.1.{index}', url='', notes='说明', detail='', latest=False,
                             announcements=[]) for index in range(40)] + self.HISTORY
        self.deliver(long_history)
        dialog = self.window.open_notice_dialog()
        QApplication.processEvents()
        screen = dialog.screen() or QApplication.primaryScreen()
        self.assertLessEqual(dialog.height(), screen.availableGeometry().height())

    def test_the_archive_page_lists_the_same_text(self):
        self.deliver()
        self.window.show_page('notices')
        QApplication.processEvents()
        self.assertIn('欢迎使用', self.window.notices_view.toPlainText())
        self.assertIn('v0.2.1', self.window.notices_view.toPlainText())

    def test_a_check_from_the_window_updates_it(self):
        self.deliver()
        dialog = self.window.open_notice_dialog()
        with patch('qt_desktop.threading.Thread'):
            dialog.check_button.click()
        self.assertFalse(dialog.check_button.isEnabled())
        dialog.close()


class UpdateUiTests(unittest.TestCase):
    """The one press: what the buttons say, and what happens when it fails.

    The download and the switch are both replaced here. What is under test is
    the wiring -- which button is offered, what the note promises, and whether a
    failure leaves the program usable -- not the transport, which is exercised
    in test_self_update.py.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.profiles = profiles_module.Profiles(self.temp.name)
        self.window = Window(self.profiles.load_store(), profiles=self.profiles, tray=False)
        self.window.notice_data = None
        self.window._installable = True
        self.window.refresh()

    def tearDown(self):
        dialog = self.window.notice_dialog
        if dialog is not None:
            dialog.close()
        self.window.controller.stop()
        self.window.controller.worker.join(timeout=2)
        self.window.close()
        self.temp.cleanup()

    def deliver(self, latest='v0.9.9', url='https://example.com/download'):
        result = dict(latest=latest, url=url, notes='本版改动', announcements=[])
        self.window.notice_history = []
        self.window.finish_notice_check((result, []))
        if self.window.notice_dialog is not None:
            self.window.notice_dialog.close()
        return result

    def test_a_new_version_offers_the_one_press(self):
        self.deliver()
        self.assertTrue(self.window.notice_press.isVisibleTo(self.window))
        self.assertIn('v0.9.9', self.window.notice_press.text())
        self.assertIn('data', self.window.notice_body.text())

    def test_without_a_runtime_only_the_page_is_offered(self):
        # A source checkout with nothing but a .venv cannot be switched in
        # place; the card must not pretend otherwise.
        self.window._installable = False
        self.deliver()
        self.assertFalse(self.window.notice_press.isVisibleTo(self.window))
        self.assertEqual('下载新版本', self.window.notice_action.text())
        self.assertIn('手动', self.window.update_note())

    def test_the_card_button_starts_the_update(self):
        self.deliver()
        with patch.object(self.window, 'start_update') as start:
            self.window.notice_press.click()
        start.assert_called_once_with()
        self.assertIn('立即更新', self.window.notice_press.text())

    def test_the_window_and_the_archive_offer_the_same_press(self):
        self.deliver()
        dialog = self.window.open_notice_dialog()
        self.assertTrue(dialog.update_button.isVisibleTo(dialog))
        self.assertIn('v0.9.9', dialog.update_button.text())
        dialog.close()
        # The archive page has no other way to reach the update on a wide screen.
        # The page is only on screen once someone opens it, so it is looked up
        # from the window with that page shown.
        self.window.show_page('notices')
        self.assertIn('v0.9.9', self.window.notices_update.text())
        self.assertTrue(self.window.notices_update.isVisibleTo(self.window))

    def test_a_failed_download_leaves_the_old_version_alone(self):
        self.deliver()
        self.window.update_pending = 'v0.9.9'
        self.window.finish_update(self_update.UpdateError('下载不完整，请稍后重试。'))
        self.assertIsNone(self.window.update_pending)
        self.assertIn('下载不完整', self.window.status.text())
        # The card goes back to offering the press rather than spinning.
        self.assertIn('立即更新', self.window.notice_press.text())
        self.assertTrue(self.window.notice_press.isEnabled())

    def test_an_update_with_no_version_to_install_does_nothing(self):
        self.window.notice_data = dict(latest='', url='', notes='', announcements=[])
        self.window.refresh()
        with patch('qt_desktop.threading.Thread') as thread:
            self.assertFalse(self.window.start_update())
        self.assertFalse(thread.called)
        self.assertIn('没有可更新的版本', self.window.status.text())

    def test_a_prepared_package_hands_over_to_the_switch_script(self):
        self.deliver()
        # Written to the program's own .update folder, never to a temp one, so
        # the workspace is pointed at a scratch install for this test.
        install = scratch_folder('update-ui-install')
        self.window.workspace = install
        plan = self_update.work_folder(install) / 'plan.json'
        made = dict(workspace=str(install), staged=self.temp.name, swap=[], copy=[],
                    ready=self.temp.name)
        try:
            with patch('qt_desktop.subprocess.Popen') as popen:
                self.window.finish_update(made)
            self.assertTrue(popen.called)
            command = popen.call_args.args[0]
            # A separate interpreter runs the switch: this process holds the
            # folders open, so it can only quit and let someone else move them.
            self.assertIn('-c', command)
            self.assertTrue(plan.is_file())
            # The window quits so the switch is not fighting a running program.
            self.assertTrue(self.window.quitting)
        finally:
            self_update.clean(install)


    """The counter is opt-in; nothing may leave the machine before the tick."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.profiles = profiles_module.Profiles(self.temp.name)
        self.window = Window(self.profiles.load_store(), profiles=self.profiles, tray=False)
        # A real build ships the credential beside the program; the test build
        # has none, so the feature would be off and nothing could be observed.
        self.saved_token = usage_ping.UPLOAD_TOKEN
        usage_ping.UPLOAD_TOKEN = 'token'

    def tearDown(self):
        usage_ping.UPLOAD_TOKEN = getattr(self, 'saved_token', '')
        self.window.controller.stop()
        self.window.controller.worker.join(timeout=2)
        self.window.close()
        self.temp.cleanup()

    def test_a_window_without_opt_in_sends_nothing(self):
        with patch('qt_desktop.threading.Thread') as thread:
            self.assertFalse(self.window.start_usage_ping())
        self.assertFalse(thread.called)

    def test_ticking_the_box_creates_an_id_and_unticking_removes_it(self):
        self.assertEqual({}, self.window.usage_status_from(False))
        given = self.window.usage_status_from(True)
        self.assertTrue(usage_ping.valid_id(given['install']))
        self.assertEqual({}, self.window.usage_status_from(False))

    def test_ticking_again_keeps_the_same_identity(self):
        first = self.window.usage_status_from(True)
        self.window.controller.settings(dict(usage_status=first))
        again = self.window.usage_status_from(True)
        self.assertEqual(first['install'], again['install'])

    def test_an_opted_in_window_starts_one_request(self):
        self.window.controller.settings(dict(usage_status=dict(install=usage_ping.new_id())))
        with patch('qt_desktop.threading.Thread') as thread:
            self.assertTrue(self.window.start_usage_ping())
        self.assertTrue(thread.called)

    def test_the_day_of_the_attempt_is_remembered_so_it_is_not_repeated(self):
        install = usage_ping.new_id()
        self.window.controller.settings(dict(usage_status=dict(install=install)))
        with patch('qt_desktop.usage_ping.report',
                   return_value=dict(install=install, reported='2026-10-21', message='已记录')):
            # The worker thread and the queued timer both run here: drain events
            # until the answer has landed rather than waiting a fixed time.
            self.assertTrue(self.window.start_usage_ping())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                self.app.processEvents()
                if 'reported' in (self.window.store.data['settings'].get('usage_status') or {}):
                    break
                time.sleep(0.01)
        saved = self.window.store.data['settings']['usage_status']
        self.assertEqual('2026-10-21', saved.get('reported'))
        self.assertFalse(usage_ping.due(saved))
        with patch('qt_desktop.threading.Thread') as thread:
            self.assertFalse(self.window.start_usage_ping())
        self.assertFalse(thread.called)

    def test_a_failed_attempt_is_still_recorded_and_shown_nowhere(self):
        install = usage_ping.new_id()
        self.window.controller.settings(dict(usage_status=dict(install=install)))
        self.window.finish_usage_ping(dict(install=install, failed='2026-10-21', message='网络不通'))
        saved = self.window.store.data['settings']['usage_status']
        self.assertEqual('2026-10-21', saved['failed'])
        self.assertNotIn('网络不通', self.window.status.text())


class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_name_is_stable_per_data_folder_and_differs_between_them(self):
        import qt_desktop
        first = qt_desktop.SingleInstance.name_for(Path('C:/one'))
        self.assertEqual(first, qt_desktop.SingleInstance.name_for(Path('C:/one')))
        self.assertNotEqual(first, qt_desktop.SingleInstance.name_for(Path('C:/two')))
        self.assertTrue(first.startswith('MoocReminder-'))
        self.assertNotIn('\\', first)
        self.assertNotIn('/', first)

    def test_the_second_copy_hands_over_instead_of_running(self):
        import qt_desktop
        name = qt_desktop.SingleInstance.name_for(Path(tempfile.gettempdir())/'single-instance-probe')
        first = qt_desktop.SingleInstance(name)
        second = qt_desktop.SingleInstance(name)
        try:
            self.assertTrue(first.claim())
            asked = []
            first.on_activate(lambda: asked.append(True))
            self.assertFalse(second.claim())
            deadline = time.monotonic() + 3
            while not asked and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
            self.assertTrue(asked, '第二次启动应当把已有窗口叫到前台')
        finally:
            for instance in (first, second):
                if instance.server is not None:
                    instance.server.close()
                    instance.server.removeServer(name)

    def test_a_stale_socket_from_a_crash_does_not_block_startup(self):
        import qt_desktop
        name = qt_desktop.SingleInstance.name_for(Path(tempfile.gettempdir())/'stale-probe')
        crashed = qt_desktop.SingleInstance(name)
        self.assertTrue(crashed.claim())
        crashed.server.close()  # the process died without cleaning up
        fresh = qt_desktop.SingleInstance(name)
        try:
            self.assertTrue(fresh.claim())
        finally:
            if fresh.server is not None:
                fresh.server.close()
                fresh.server.removeServer(name)


if __name__ == '__main__':
    unittest.main()
