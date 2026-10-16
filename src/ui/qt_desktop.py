"""Native macOS-inspired desktop, with a shared embedded MOOC browser."""
import argparse
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import sys
import threading
import time

# This file lives in src/ui/, so the folder holding paths.py is one level up.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paths

from storage import Store
from version import APP_NAME, VERSION
from qt_bootstrap import prepare_qt

_data = paths.data_dir()
prepare_qt(_data)

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QDialog, QFormLayout,
    QSpinBox, QComboBox, QPlainTextEdit, QMessageBox, QFrame, QTextBrowser, QStackedWidget, QMenu,
    QSystemTrayIcon, QRadioButton, QButtonGroup)
from portal import Controller
import autostart
import browser
import emailer
import mail_guide
import mail_log
import profiles as profiles_module
from core import (DEFAULT_DIGEST_INTERVAL, MAX_DIGEST_INTERVAL, MIN_DIGEST_INTERVAL, WEEKDAY_NAMES,
                  account_id, binding_state as core_binding_state, digest_cadence_text, next_digest_day,
                  now_beijing)
from qt_reader import Manager

STYLE = '''
QMainWindow,QDialog{background:#e9edf4;color:#344053;font-family:"Microsoft YaHei UI";font-size:12px}
QWidget#shell{background:#f8f9fc;border-radius:16px;border:1px solid #fff}
QWidget#sidebar{background:#eceff5;border-right:1px solid #dde3ed;border-top-left-radius:16px;border-bottom-left-radius:16px}
QLabel{background:transparent;color:#8a96a8}QLabel#brand{color:#303a4e;font-size:25px;font-weight:600}
QLabel#title{color:#28364c;font-size:29px;font-weight:600}QLabel#sectionTitle{color:#63738a;font-size:15px;font-weight:600}
QLabel#muted{color:#a3adbe;font-size:11px}QLabel#metric{color:#2e3b51;font-size:34px;font-weight:600}
QFrame#card{background:#fff;border:1px solid #e4e9f2;border-radius:12px}
QPushButton{background:#fff;border:1px solid #e0e6f0;border-radius:8px;padding:10px 16px;color:#7d8da4}
QPushButton:hover{background:#f2f6fe}QPushButton:disabled{background:#f2f4f8;color:#bdc6d4}
QPushButton#primary{background:#3b7eed;border-color:#3475e4;color:#fff}QPushButton#primary:hover{background:#3374e4}
QPushButton#nav{background:transparent;border:none;text-align:left;padding:13px 14px;color:#8b96a8}
QPushButton#nav[active="true"]{background:#fff;color:#796bcc;font-weight:600}
QLineEdit,QSpinBox,QPlainTextEdit{background:#fff;border:1px solid #e2e8f2;border-radius:7px;padding:9px;color:#7e8da3}
QTableWidget{background:#fff;border:none;color:#66768e;font-size:12px;gridline-color:#eef1f6;selection-background-color:#f2f5fd;selection-color:#5b6e8d}
QTableWidget::item{padding:8px;border-bottom:1px solid #eef1f6}QHeaderView::section{background:#fbfcfe;border:none;border-bottom:1px solid #e9eef6;padding:13px;color:#9ba8bc;font-size:11px}
QCheckBox{color:#9aa6ba;padding:8px}QScrollBar:vertical{background:#f6f8fc;width:7px}QScrollBar::handle:vertical{background:#dce3ef;border-radius:3px}
'''

# The middle of the window is a stack of pages; the sidebar switches between
# them. Before this existed the first three entries all re-ran `refresh()`, so
# clicking them changed nothing at all.
PAGES = {
    'overview': dict(text='◫   总览', title='作业概览', subtitle='截止时间看清楚，学习节奏更从容。', group='学习空间'),
    'assignments': dict(text='☷   作业清单', title='作业清单', subtitle='搜索、筛选、勾选完成，都在这一页。', group='学习空间'),
    'courses': dict(text='▤   我的课程', title='我的课程', subtitle='每次检查作业后读到的课程会列在这里。', group='学习空间'),
    'log': dict(text='✉   发信记录', title='邮件发送记录', subtitle='每一封提醒是否发出、为什么失败，都在这里。', group='偏好设置'),
    'details': dict(text='ⓘ   连接详情', title='连接详情', subtitle='当前状态、错误记录与本机数据位置。', group='偏好设置'),
}
# 'settings' is not a page: 提醒设置 is a dialog, so it stays an action button.
NAV_GROUPS = (('学习空间', ('overview', 'assignments', 'courses')),
              ('偏好设置', ('log', 'settings', 'details')))


def _whole_number(value, fallback):
    """Read a stored number without letting a damaged one reach the widgets."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


class EmailSettingsForm(QWidget):
    """Email reminder fields; testable on its own without the main window.

    The authorization code follows one rule: only a value the user actually
    typed is submitted, so leaving the field blank keeps the stored secret.
    """

    test_requested = Signal(dict)

    def __init__(self, email, parent=None, folder=None):
        super().__init__(parent)
        email = dict(email or {})
        # The shipped sender decides whether this build can send at all, so the
        # wording below has to branch on it rather than promise a sender that
        # may not be configured.
        self.folder = folder
        self.shipped = emailer.shipped_sender(folder)
        self.saved_password = bool(emailer.password_saved(email.get('password', '')))
        self.forget_password = False
        self.password_touched = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(8)
        self.enabled = QCheckBox('开启邮件提醒')
        self.enabled.setChecked(bool(email.get('enabled')))
        layout.addWidget(self.enabled)
        form = QFormLayout()
        self.recipient = QLineEdit(str(email.get('recipient', '')))
        self.recipient.setPlaceholderText('例如 you@qq.com')
        self.server = QLineEdit(str(email.get('server', '')))
        self.server.setPlaceholderText('留空则使用程序预置的邮件服务器')
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(email.get('port', emailer.DEFAULT_PORT)))
        self.security = QComboBox()
        self.security.addItem('SSL（常用 465）', 'ssl')
        self.security.addItem('STARTTLS（常用 587）', 'starttls')
        index = self.security.findData(str(email.get('security', 'ssl')))
        self.security.setCurrentIndex(max(0, index))
        self.sender = QLineEdit(str(email.get('sender', '')))
        self.sender.setPlaceholderText('留空则使用程序预置的发件邮箱')
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText('已保存，留空则不修改' if self.saved_password else '留空则使用程序预置的授权码')
        self.digest = QLineEdit(','.join(email.get('digest_times', []) or []))
        self.digest.setPlaceholderText('例如 08:00,20:00，留空则只按截止时间提醒')
        # How often the summary goes out. "Every day" is what this program always
        # did; a weekly or every-N-days cadence is what a quiet course actually
        # needs, so the clock time above stays one setting and the rhythm becomes
        # another instead of the two being crammed into one field.
        self.digest_frequency = QComboBox()
        self.digest_frequency.addItem('每天', 'daily')
        self.digest_frequency.addItem('每周一次', 'weekly')
        self.digest_frequency.addItem('每 N 天一次', 'interval')
        index = self.digest_frequency.findData(str(email.get('digest_frequency', 'daily')))
        self.digest_frequency.setCurrentIndex(max(0, index))
        self.digest_weekday = QComboBox()
        for value, name in enumerate(WEEKDAY_NAMES):
            self.digest_weekday.addItem(name, value)
        self.digest_weekday.setCurrentIndex(max(0, _whole_number(email.get('digest_weekday'), 0)) % 7)
        self.digest_interval = QSpinBox()
        self.digest_interval.setRange(MIN_DIGEST_INTERVAL, MAX_DIGEST_INTERVAL)
        self.digest_interval.setSuffix(' 天')
        self.digest_interval.setValue(max(MIN_DIGEST_INTERVAL,
                                          min(MAX_DIGEST_INTERVAL, _whole_number(email.get('digest_interval'),
                                                                                 DEFAULT_DIGEST_INTERVAL))))
        cadence_row = QWidget()
        cadence = QHBoxLayout(cadence_row)
        cadence.setContentsMargins(0, 0, 0, 0)
        cadence.setSpacing(6)
        cadence.addWidget(self.digest_frequency)
        cadence.addWidget(self.digest_weekday)
        cadence.addWidget(self.digest_interval)
        cadence.addStretch()
        form.addRow('接收提醒的邮箱', self.recipient)
        form.addRow('汇总发信时刻', self.digest)
        form.addRow('汇总频率', cadence_row)
        layout.addLayout(form)
        self.digest_hint = QLabel()
        self.digest_hint.setObjectName('muted'); self.digest_hint.setWordWrap(True)
        layout.addWidget(self.digest_hint)
        self.digest_frequency.currentIndexChanged.connect(self.refresh_digest_hint)
        self.digest_weekday.currentIndexChanged.connect(self.refresh_digest_hint)
        self.digest_interval.valueChanged.connect(self.refresh_digest_hint)
        self.refresh_digest_hint()
        # Who the mail comes from is a choice, made explicitly: the shipped
        # address by default, or the user's own mailbox. It used to hide behind
        # an "advanced options" checkbox, which read as a power-user switch
        # rather than as the two alternatives it actually is.
        self.official = QRadioButton('使用程序内置的发件邮箱（推荐）')
        self.custom = QRadioButton('使用我自己的发件邮箱')
        if self.shipped:
            self.official.setToolTip(f'提醒将从 {self.shipped} 发出，你不需要填写任何服务器信息。')
        self.sender_choice = QButtonGroup(self)
        self.sender_choice.addButton(self.official)
        self.sender_choice.addButton(self.custom)
        layout.addWidget(self.official)
        layout.addWidget(self.custom)
        self.advanced = QWidget()
        advanced_form = QFormLayout(self.advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow('发件邮箱', self.sender)
        advanced_form.addRow('邮件服务器', self.server)
        advanced_form.addRow('端口', self.port)
        advanced_form.addRow('加密方式', self.security)
        advanced_form.addRow('授权码', self.password)
        layout.addWidget(self.advanced)
        # No shipped sender means the custom fields are the only way to send at
        # all, so the choice is made for the user instead of offered.
        use_custom = bool(email.get('advanced')) or not self.shipped
        self.official.setChecked(not use_custom)
        self.custom.setChecked(use_custom)
        if not self.shipped:
            self.official.setEnabled(False)
            self.official.setText('使用程序内置的发件邮箱（这个版本没有预置）')
        self.advanced.setVisible(use_custom)
        self.custom.toggled.connect(self.advanced.setVisible)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName('muted')
        layout.addWidget(self.hint)
        state = QHBoxLayout()
        self.test_button = QPushButton('发送测试邮件')
        self.guide_button = QPushButton('如何获取授权码？')
        self.guide_button.setToolTip('以 QQ 邮箱为例，图文说明授权码与服务器怎么填')
        self.clear_password = QPushButton('清除已保存的授权码')
        self.clear_password.setVisible(self.saved_password)
        state.addWidget(self.test_button)
        state.addWidget(self.guide_button)
        state.addWidget(self.clear_password)
        state.addStretch()
        layout.addLayout(state)
        note = QLabel(self.shipped_note())
        note.setObjectName('muted')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.recipient.textChanged.connect(self.autofill)
        self.sender.textChanged.connect(self.autofill)
        self.password.textEdited.connect(self.mark_password)
        self.clear_password.clicked.connect(self.forget)
        self.test_button.clicked.connect(self.request_test)
        self.guide_button.clicked.connect(self.show_guide)
        self.custom.toggled.connect(lambda _: self.autofill(self.recipient.text()))
        self.autofill(self.recipient.text())

    def show_guide(self):
        """Walk through getting a credential, for whoever sets the sender up."""
        dialog = QDialog(self)
        dialog.setWindowTitle('如何获取发件邮箱授权码')
        # Tall enough that the troubleshooting section is visible without
        # scrolling, but never taller than the screen it opens on.
        screen = self.screen().availableGeometry() if self.screen() else None
        height = min(900, int(screen.height() * 0.9)) if screen else 820
        dialog.resize(680, height)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(mail_guide.guide_html(self.sender.text()))
        layout.addWidget(browser)
        buttons = QHBoxLayout()
        provider = mail_guide.supports(self.sender.text())
        if provider:
            note = QLabel(f'已按 {provider["name"]} 显示步骤')
        else:
            note = QLabel('默认以 QQ 邮箱为例；填入其它邮箱后重开会显示对应步骤')
        note.setObjectName('muted')
        buttons.addWidget(note); buttons.addStretch()
        close = QPushButton('知道了')
        close.clicked.connect(dialog.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        dialog.exec()

    def mark_password(self, *_):
        self.password_touched = True
        self.forget_password = False

    def forget(self):
        self.password.clear()
        self.forget_password = True
        self.saved_password = False
        self.clear_password.setVisible(False)
        self.hint.setText('保存后本机不再保留授权码，需要重新填写才能发信。')

    def shipped_note(self):
        """What to say about the sender this build will actually use.

        Naming the address matters: a reminder arriving from an unrecognised
        mailbox looks like spam, and users were left guessing who sent it.
        """
        if self.shipped:
            return f'提醒将由程序内置的发件邮箱 {self.shipped} 统一发出；你只需填写上面的收件邮箱。'
        return '这个版本没有内置发件邮箱，请选择「使用我自己的发件邮箱」，填写发件邮箱、邮件服务器与授权码。'

    def autofill(self, text):
        """Offer a provider preset for the sender the user is configuring.

        Only when the user sends from their own mailbox: the server has to
        match whoever actually sends, and by default that is the shipped
        sender, not the address typed in as the recipient.
        """
        self.test_button.setEnabled(bool(str(text).strip()))
        if not self.custom.isChecked():
            if not str(text).strip():
                self.hint.setText('')
            elif self.shipped:
                self.hint.setText(f'提醒将从 {self.shipped} 发出，你只需填写自己的收件邮箱。')
            else:
                self.hint.setText('这个版本没有内置发件邮箱，请选择「使用我自己的发件邮箱」并填写下面的信息。')
            return
        preset = emailer.suggest_server(self.sender.text() or text)
        if not preset:
            self.hint.setText('未识别该邮箱服务商，请手动填写邮件服务器与端口。')
            return
        if not self.server.text().strip() or self.server.text().strip() in self.suggested_servers():
            self.server.setText(preset['server'])
            self.port.setValue(preset['port'])
            index = self.security.findData(preset['security'])
            if index >= 0:
                self.security.setCurrentIndex(index)
            self.hint.setText(f'已按 {preset["server"]} 自动填写，可按服务商说明调整。')

    @staticmethod
    def suggested_servers():
        return {preset['server'] for preset in emailer.PRESETS.values()}

    def cadence_values(self):
        """The chosen summary cadence as (frequency, weekday, interval)."""
        frequency = str(self.digest_frequency.currentData() or 'daily')
        weekday = _whole_number(self.digest_weekday.currentData(), 0) % 7
        return frequency, weekday, int(self.digest_interval.value())

    def refresh_digest_hint(self):
        """Say when the next summary arrives, and hide the parts that do not apply."""
        frequency, weekday, interval = self.cadence_values()
        self.digest_weekday.setVisible(frequency == 'weekly')
        self.digest_interval.setVisible(frequency == 'interval')
        when = next_digest_day(now_beijing(), frequency, weekday, interval)
        late = '' if frequency == 'daily' else '那天没开机的话，下次启动时会补发一次。'
        self.digest_hint.setText(
            f'{digest_cadence_text(frequency, weekday, interval)}发一封汇总邮件，列出所有未截止作业'
            f'（下次 {when.strftime("%m月%d日")}）；与“提前提醒”互不影响，两种邮件都会发。{late}')

    def values(self):
        """Return the settings patch; omit the code unless the user typed one."""
        frequency, weekday, interval = self.cadence_values()
        config = dict(enabled=self.enabled.isChecked(), recipient=self.recipient.text().strip(),
                      advanced=self.custom.isChecked(),
                      sender=self.sender.text().strip(), server=self.server.text().strip(),
                      port=self.port.value(), security=self.security.currentData(),
                      digest_frequency=frequency, digest_weekday=weekday, digest_interval=interval,
                      digest_times=[part.strip() for part in self.digest.text().replace('，', ',').split(',') if part.strip()])
        if self.password.text() or self.forget_password:
            config['password'] = self.password.text()
        return config

    def request_test(self):
        if not self.recipient.text().strip():
            self.hint.setText('请先填写接收提醒的邮箱。')
            return
        self.hint.setText('正在发送测试邮件…')
        self.test_requested.emit(self.values())


class EmailLogPage(QWidget):
    """What the program has sent, when, and why anything failed.

    Until this existed the send log was only reachable through 连接详情, which
    showed a single message, so a user could not tell whether a reminder had
    gone out at all.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
        card = QFrame(); card.setObjectName('card'); panel = QVBoxLayout(card)
        panel.setContentsMargins(20, 18, 20, 14); panel.setSpacing(12)
        self.hint = QLabel(''); self.hint.setObjectName('muted'); self.hint.setWordWrap(True)
        panel.addWidget(self.hint)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['时间', '类型', '结果', '说明'])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for column, width in [(0, 132), (1, 96), (2, 84)]:
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().hide(); self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        panel.addWidget(self.table, 1)
        self.empty = QLabel('还没有发送记录。\n\n设置好接收提醒的邮箱后，每封邮件的结果都会记在这里。')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter); self.empty.setMinimumHeight(220)
        self.empty.setStyleSheet('color:#a4afc1;font-size:14px'); panel.addWidget(self.empty, 1)
        layout.addWidget(card, 1)
        self.status = QLabel(''); self.status.setObjectName('muted'); self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.set_log({})

    def set_log(self, log=None, status=''):
        """Fill the table from `store.data['email_log']`."""
        rows = mail_log.entries(log)
        counts = mail_log.summary(log)
        if counts['total']:
            head = f'共 {counts["total"]} 条提醒记录，其中 {counts["failed"]} 条发送失败。'
        else:
            head = '还没有提醒记录。'
        self.hint.setText(head + '作业提醒、每日汇总与测试邮件都会记在这里。')
        self.status.setText(str(status or ''))
        self.table.setVisible(bool(rows)); self.empty.setVisible(not rows)
        self.table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            for column, value in enumerate([entry['when'], entry['kind'], entry['result'], entry['note']]):
                cell = QTableWidgetItem(value)
                if entry['failed']:
                    cell.setForeground(QColor('#c0392b'))
                elif entry['ok']:
                    cell.setForeground(QColor('#2e7d5b'))
                self.table.setItem(row, column, cell)
            self.table.setRowHeight(row, 38)


class RegisterDialog(QDialog):
    """Create an account from a name alone; MOOC and email are bound after entering."""

    def __init__(self, profiles, parent=None):
        super().__init__(parent)
        self.profiles = profiles
        self.key = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle('注册账号')
        self.resize(520, 280)
        layout = QVBoxLayout(self)
        title = QLabel('新建一个账号'); title.setObjectName('sectionTitle'); layout.addWidget(title)
        intro = QLabel('先用账号名称建好，进入后再绑定慕课账号与邮箱。每个账号的作业、完成标记与邮箱设置相互独立。')
        intro.setWordWrap(True); intro.setObjectName('muted'); layout.addWidget(intro)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText('例如：主账号、选修课账号')
        form.addRow('账号名称', self.name)
        layout.addLayout(form)
        self.error = QLabel(''); self.error.setObjectName('muted'); self.error.setWordWrap(True)
        layout.addWidget(self.error)
        layout.addStretch()
        buttons = QHBoxLayout()
        self.submit = QPushButton('注册并进入'); self.submit.setObjectName('primary')
        cancel = QPushButton('取消')
        buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(self.submit)
        layout.addLayout(buttons)

        def accept():
            try:
                self.key = self.profiles.register(self.name.text())
            except Exception as exc:
                self.error.setText(str(exc))
                return
            self.accept()

        self.submit.clicked.connect(accept)
        cancel.clicked.connect(self.reject)
        self.name.setFocus()

    def values(self):
        return dict(name=self.name.text().strip()) if self.key else None


class LockScreen(QDialog):
    """Entry gate: pick the account to work on, or register a new one.

    Accounts are local to this computer and carry no password: the account name
    is the only handle, and the data never leaves the machine.
    """

    def __init__(self, profiles, parent=None):
        super().__init__(parent)
        self.profiles = profiles
        self.key = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle(f'{APP_NAME} · 登录')
        self.setWindowIcon(QIcon(str(paths.icon_path())))
        self.setStyleSheet(STYLE)
        self.setFixedWidth(470)
        outer = QVBoxLayout(self)
        card = QFrame(); card.setObjectName('card'); outer.addWidget(card)
        body = QVBoxLayout(card); body.setContentsMargins(28, 26, 28, 24); body.setSpacing(12)
        brand = QLabel(APP_NAME); brand.setObjectName('brand'); body.addWidget(brand)
        subtitle = QLabel('选择账号后进入；没有账号就先注册一个。')
        subtitle.setObjectName('muted'); subtitle.setWordWrap(True); body.addWidget(subtitle)
        form = QFormLayout()
        self.accounts = QComboBox()
        form.addRow('账号', self.accounts)
        body.addLayout(form)
        self.summary = QLabel(''); self.summary.setObjectName('muted'); self.summary.setWordWrap(True)
        body.addWidget(self.summary)
        self.error = QLabel(''); self.error.setObjectName('muted'); self.error.setWordWrap(True)
        body.addWidget(self.error)
        buttons = QHBoxLayout()
        self.register_button = QPushButton('注册新账号')
        self.enter = QPushButton('进入'); self.enter.setObjectName('primary')
        buttons.addWidget(self.register_button); buttons.addStretch(); buttons.addWidget(self.enter)
        body.addLayout(buttons)
        self.register_button.clicked.connect(self.register)
        self.enter.clicked.connect(self.enter_selected)
        self.accounts.currentIndexChanged.connect(lambda _: self.show_summary())
        self.reload()

    def reload(self, select=None):
        options = self.profiles.account_options()
        self.accounts.blockSignals(True)
        self.accounts.clear()
        for entry in options:
            self.accounts.addItem(entry['label'], entry['key'])
        self.accounts.blockSignals(False)
        self.empty = not options
        self.register_button.setText('注册账号' if self.empty else '注册新账号')
        self.register_button.setObjectName('primary' if self.empty else '')
        self.enter.setEnabled(not self.empty)
        self.enter.setToolTip('' if not self.empty else '还没有账号，请先注册')
        self.accounts.setEnabled(not self.empty)
        if select:
            index = self.accounts.findData(select)
            if index >= 0:
                self.accounts.setCurrentIndex(index)
        self.show_summary()

    def show_summary(self):
        if self.empty:
            self.summary.setText('这台电脑上还没有账号，先注册一个。')
            return
        key = self.accounts.currentData()
        for entry in self.profiles.account_options():
            if entry['key'] == key:
                bound = self.profiles.load_store(key).is_bound()
                self.summary.setText('上次进入的账号' if entry.get('active') else '该账号已绑定慕课与邮箱' if bound
                                     else '该账号还未绑定慕课与邮箱，进入后按提示完成绑定。')
                return

    def register(self):
        dialog = RegisterDialog(self.profiles, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.key:
            return
        self.key = dialog.key
        self.accept()

    def enter_selected(self):
        key = self.accounts.currentData()
        if not key:
            self.error.setText('请先注册一个账号。')
            return
        try:
            self.profiles.login(key)
        except Exception as exc:
            self.error.setText(str(exc))
            return
        self.key = key
        self.accept()

    def select_account(self, key):
        """Used by tests and by callers that already know the account."""
        self.profiles.login(key)
        self.key = key
        self.accept()


class Tray(QSystemTrayIcon):
    """Keeps the program reachable once its window is closed.

    Closing the window only hides it, so without a tray icon there is a
    background process the user has no way back into -- and, with autostart on,
    no way to stop it either.  The menu is the answer to both.
    """

    def __init__(self, window):
        icon = paths.icon_path()
        super().__init__(QIcon(str(icon)) if icon.exists() else QIcon(), window)
        self.window = window
        menu = QMenu()
        self.open_action = menu.addAction('打开主窗口')
        self.pause_action = menu.addAction('暂停提醒')
        self.pause_action.setCheckable(True)
        self.pause_action.setChecked(bool(window.controller.paused))
        menu.addSeparator()
        self.exit_action = menu.addAction('退出 MoocReminder')
        self.open_action.triggered.connect(self.show_window)
        self.pause_action.triggered.connect(lambda: window.toggle_pause())
        self.exit_action.triggered.connect(self.quit_app)
        self.setContextMenu(menu)
        self.activated.connect(self.activated_reason)
        self.setToolTip(f'{APP_NAME} · 作业提醒 {VERSION}')

    def activated_reason(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_window(self):
        window = self.window
        window.show()
        window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
        window.raise_()
        window.activateWindow()

    def quit_app(self):
        """Leave for real, the way closing the window no longer does."""
        self.window.quitting = True
        self.hide()
        QApplication.instance().quit()

    def sync(self, snapshot):
        """Follow the controller: the checkmark and the tooltip are its state."""
        paused = bool(snapshot.get('paused'))
        if self.pause_action.isChecked() != paused:
            self.pause_action.setChecked(paused)
        pending = snapshot.get('summary', {}).get('pending', 0)
        self.setToolTip(f'{APP_NAME} · {pending} 项待完成' if pending else f'{APP_NAME} · 暂无待完成作业')


class Window(QMainWindow):
    def __init__(self, store, profiles=None, tray=True):
        super().__init__()
        self.store = store
        self.profiles = profiles or profiles_module.Profiles(profiles_module.folder_for_store(store.folder))
        self.workspace = paths.PROGRAM_ROOT
        self._email_dialog = None
        self.tray = None
        self.quitting = False
        # Read once and cached: answering this means a PowerShell round trip, and
        # refresh() runs four times a second.
        self.autostart_on = autostart.enabled(self.workspace)
        self.manager = Manager(store.folder, self)
        self.controller = Controller(store, context_provider=self.manager.context, profile_dir='web-profiles')
        self.manager.on_error = lambda message: self.controller.events.put(('error', message))
        self.setWindowTitle(f'{APP_NAME} · 作业提醒 {VERSION}')
        self.setWindowIcon(QIcon(str(paths.icon_path())))
        self.resize(1260, 850)
        self.setMinimumSize(1020, 670)
        self.setStyleSheet(STYLE)
        self.show_past = False
        self.last_signature = None
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(250)
        if tray:
            self.create_tray()
        self.refresh()

    def create_tray(self):
        """Add the tray icon when the desktop actually provides one.

        Headless machines, remote sessions and ``--smoke-report`` have no tray,
        and asking for one there would leave an icon that can never be clicked.
        """
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        self.tray = Tray(self)
        self.tray.show()
        return self.tray

    def notify(self, title, message):
        """Report something the user cannot see if the window is hidden."""
        if self.tray is not None:
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 8000)
        else:
            QMessageBox.information(self, title, message)

    def _build(self):
        outer = QWidget()
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(18, 18, 18, 18)
        shell = QWidget(); shell.setObjectName('shell')
        outer_layout.addWidget(shell)
        layout = QHBoxLayout(shell); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        side = QWidget(); side.setObjectName('sidebar'); side.setFixedWidth(214)
        sidebar = QVBoxLayout(side); sidebar.setContentsMargins(22, 23, 18, 20); sidebar.setSpacing(8)
        dots = QHBoxLayout(); dots.setSpacing(7)
        for color, action in [('#ff635d', self.close), ('#ffbf39', self.showMinimized), ('#36c98a', self.toggle_maximized)]:
            dot = QPushButton(); dot.setFixedSize(13, 13); dot.setStyleSheet(f'background:{color};border:1px solid #00000008;border-radius:6px;padding:0'); dot.clicked.connect(action); dots.addWidget(dot)
        dots.addStretch(); sidebar.addLayout(dots); sidebar.addSpacing(25)
        brand = QLabel(APP_NAME); brand.setObjectName('brand'); brand.setStyleSheet('font-size:21px'); sidebar.addWidget(brand)
        subtitle = QLabel('COURSE REMINDERS'); subtitle.setObjectName('muted'); sidebar.addWidget(subtitle); sidebar.addSpacing(23)
        # Every entry above 偏好设置 switches the page shown in the middle.
        self.nav_buttons = {}
        for index, (caption_text, keys) in enumerate(NAV_GROUPS):
            if index:
                sidebar.addSpacing(20)
            caption = QLabel(caption_text); caption.setObjectName('muted'); sidebar.addWidget(caption)
            for key in keys:
                button = QPushButton(PAGES[key]['text'] if key in PAGES else '♧   提醒设置')
                button.setObjectName('nav')
                button.setProperty('active', key == 'overview')
                if key in PAGES:
                    button.clicked.connect(lambda _, page=key: self.show_page(page))
                else:
                    # 提醒设置 stays a dialog, so it is tracked but has no page.
                    button.clicked.connect(self.settings)
                self.nav_buttons[key] = button
                sidebar.addWidget(button)
        sidebar.addStretch()
        note = QLabel('把重要的日期，\n留在视线里。'); note.setStyleSheet('font-size:13px;line-height:24px;color:#9ca7b9'); sidebar.addWidget(note)
        sidebar.addSpacing(21)
        self.account_label = QLabel('当前账号'); self.account_label.setObjectName('muted'); sidebar.addWidget(self.account_label)
        self.account_summary = QLabel(''); self.account_summary.setWordWrap(True); sidebar.addWidget(self.account_summary)
        self.account_combo = QComboBox(); self.account_combo.setToolTip('切换账号；每个账号的邮箱与作业记录各自独立')
        self.account_combo.activated.connect(self.account_selected); sidebar.addWidget(self.account_combo)
        sidebar.addSpacing(8)
        self.connection = QLabel('●  尚未连接慕课'); self.connection.setObjectName('muted'); sidebar.addWidget(self.connection)
        layout.addWidget(side)
        main = QWidget(); body = QVBoxLayout(main); body.setContentsMargins(32, 23, 32, 19); body.setSpacing(18)
        top = QHBoxLayout(); self.crumb = QLabel('学习空间  /  总览'); self.crumb.setObjectName('muted'); top.addWidget(self.crumb); top.addStretch()
        date = QLabel(datetime.now().strftime('%m月%d日')); date.setObjectName('muted'); top.addWidget(date)
        self.email_button = QPushButton('邮件提醒'); self.email_button.setFixedWidth(96)
        self.email_button.setToolTip('设置接收提醒的邮箱，无需先登录慕课')
        self.email_button.clicked.connect(self.open_email_settings); top.addWidget(self.email_button)
        self.pause = QPushButton('暂停'); self.pause.setFixedWidth(72); self.pause.clicked.connect(self.toggle_pause); top.addWidget(self.pause); body.addLayout(top)
        heading = QHBoxLayout(); title_block = QVBoxLayout(); eyebrow = QLabel('A LITTLE MORE ORGANIZED'); eyebrow.setObjectName('muted'); title_block.addWidget(eyebrow)
        self.page_title = QLabel('作业概览'); self.page_title.setObjectName('title'); title_block.addWidget(self.page_title)
        self.page_subtitle = QLabel('截止时间看清楚，学习节奏更从容。'); title_block.addWidget(self.page_subtitle)
        heading.addLayout(title_block); heading.addStretch()
        self.login = QPushButton('连接慕课'); self.login.clicked.connect(self.open_login); heading.addWidget(self.login)
        self.scan = QPushButton('检查作业'); self.scan.setObjectName('primary'); self.scan.clicked.connect(lambda: self.request('scan')); heading.addWidget(self.scan); body.addLayout(heading)
        self.status = QLabel('在内置窗口中登录慕课，再回来检查作业。'); self.status.setWordWrap(True); self.status.setStyleSheet('background:#edf3ff;border:1px solid #e0e9fb;border-radius:8px;padding:12px;color:#849bc0'); body.addWidget(self.status)
        self.pages = QStackedWidget(); self.page_index = {}; self.page_titles = {}
        body.addWidget(self.pages, 1)

        # ---- 总览：绑定引导、统计数字、最近要交的作业 ----
        overview = QWidget(); overview_page = QVBoxLayout(overview)
        overview_page.setContentsMargins(0, 0, 0, 0); overview_page.setSpacing(18)
        # Shown until this account has bound both its MOOC page and its email.
        self.binding = QFrame(); self.binding.setObjectName('card')
        binding_row = QHBoxLayout(self.binding); binding_row.setContentsMargins(20, 16, 20, 16)
        binding_copy = QVBoxLayout()
        self.binding_title = QLabel('还差两步就可以开始检查作业'); self.binding_title.setObjectName('sectionTitle')
        self.binding_detail = QLabel(); self.binding_detail.setObjectName('muted'); self.binding_detail.setWordWrap(True)
        binding_copy.addWidget(self.binding_title); binding_copy.addWidget(self.binding_detail)
        binding_row.addLayout(binding_copy); binding_row.addStretch()
        self.binding_button = QPushButton('绑定慕课与邮箱'); self.binding_button.setObjectName('primary')
        self.binding_button.clicked.connect(self.open_binding); binding_row.addWidget(self.binding_button)
        overview_page.addWidget(self.binding)
        # Asked exactly once, here rather than in the middle of startup: turning
        # autostart on is the user's decision, and it should not stand between
        # them and the window they just asked for.
        self.autostart = QFrame(); self.autostart.setObjectName('card')
        prompt_row = QHBoxLayout(self.autostart); prompt_row.setContentsMargins(20, 16, 20, 16)
        prompt_copy = QVBoxLayout()
        prompt_title = QLabel('要不要开机自动启动？'); prompt_title.setObjectName('sectionTitle')
        prompt_detail = QLabel('开启后，登录 Windows 就会在托盘里自动运行，不用每次自己打开；'
                               '随时可以在“提醒设置”里关掉。'); prompt_detail.setObjectName('muted'); prompt_detail.setWordWrap(True)
        prompt_copy.addWidget(prompt_title); prompt_copy.addWidget(prompt_detail)
        prompt_row.addLayout(prompt_copy); prompt_row.addStretch()
        self.autostart_yes = QPushButton('开启开机自启'); self.autostart_yes.setObjectName('primary')
        self.autostart_yes.clicked.connect(self.accept_autostart); prompt_row.addWidget(self.autostart_yes)
        self.autostart_no = QPushButton('不用了'); self.autostart_no.clicked.connect(self.decline_autostart)
        prompt_row.addWidget(self.autostart_no)
        overview_page.addWidget(self.autostart)
        metrics = QHBoxLayout(); metrics.setSpacing(17); self.metrics = {}
        for key, name, desc in [('pending', '待完成 / 待核对', '所有尚未截止的任务'), ('soon', '3 天内截止', '优先安排时间完成'), ('courses', '已读取课程', 'MOOC · 校内 SPOC')]:
            card = QFrame(); card.setObjectName('card'); card_layout = QVBoxLayout(card); card_layout.setContentsMargins(21, 15, 21, 16); card_layout.addWidget(QLabel(name))
            value = QLabel('0'); value.setObjectName('metric'); card_layout.addWidget(value); self.metrics[key] = value
            desc_label = QLabel(desc); desc_label.setObjectName('muted'); card_layout.addWidget(desc_label); metrics.addWidget(card)
        overview_page.addLayout(metrics)
        soon_card = QFrame(); soon_card.setObjectName('card'); soon_panel = QVBoxLayout(soon_card)
        soon_panel.setContentsMargins(20, 18, 20, 14); soon_panel.setSpacing(12)
        soon_title = QLabel('最近要交的作业'); soon_title.setObjectName('sectionTitle'); soon_panel.addWidget(soon_title)
        self.overview_list = QLabel(''); self.overview_list.setTextFormat(Qt.TextFormat.RichText)
        self.overview_list.setWordWrap(True); soon_panel.addWidget(self.overview_list)
        soon_panel.addStretch(); overview_page.addWidget(soon_card, 1)
        self.overview_more = QPushButton('查看全部作业'); self.overview_more.clicked.connect(lambda: self.show_page('assignments'))
        overview_page.addWidget(self.overview_more, 0, Qt.AlignmentFlag.AlignLeft)
        self.add_page('overview', overview)

        # ---- 作业清单：搜索、筛掉已截止、逐项勾选完成 ----
        assignments = QWidget(); assignment_page = QVBoxLayout(assignments)
        assignment_page.setContentsMargins(0, 0, 0, 0); assignment_page.setSpacing(12)
        card = QFrame(); card.setObjectName('card'); panel = QVBoxLayout(card); panel.setContentsMargins(20, 18, 20, 14); panel.setSpacing(12)
        row = QHBoxLayout(); title = QLabel('接下来的作业'); title.setObjectName('sectionTitle'); row.addWidget(title); row.addStretch(); self.interval = QLabel('每 30 分钟检查'); self.interval.setObjectName('muted'); row.addWidget(self.interval); panel.addLayout(row)
        tools = QHBoxLayout(); self.search = QLineEdit(); self.search.setPlaceholderText('搜索课程或作业'); self.search.textChanged.connect(self.refresh); tools.addWidget(self.search)
        past = QCheckBox('显示已截止'); past.toggled.connect(lambda checked: (setattr(self, 'show_past', checked), self.refresh())); tools.addWidget(past)
        self.cancel = QPushButton('取消操作'); self.cancel.clicked.connect(self.controller.worker.cancel); self.cancel.hide(); tools.addWidget(self.cancel); panel.addLayout(tools)
        self.table = QTableWidget(0, 6); self.table.setHorizontalHeaderLabels(['完成', '作业与课程', '类型', '提交截止', '剩余时间', '状态'])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in [(0,54),(2,58),(3,146),(4,112),(5,82)]: self.table.setColumnWidth(column,width)
        self.table.verticalHeader().hide(); self.table.setShowGrid(False); self.table.setAlternatingRowColors(False); self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers); self.table.cellDoubleClicked.connect(self.open_course); panel.addWidget(self.table, 1)
        self.empty = QLabel('▣\n\n先连接慕课，作业就有了归处\n\n在官网完成登录，再回来检查一次作业。'); self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter); self.empty.setMinimumHeight(240); self.empty.setStyleSheet('color:#a4afc1;font-size:14px'); panel.addWidget(self.empty, 1)
        row = QHBoxLayout(); self.last_update = QLabel('等待首次连接'); self.last_update.setObjectName('muted'); row.addWidget(self.last_update); row.addStretch()
        test = QPushButton('测试提醒'); test.clicked.connect(lambda: self.controller.add_notification(test=True)); row.addWidget(test); panel.addLayout(row)
        assignment_page.addWidget(card, 1)
        self.add_page('assignments', assignments)

        # ---- 我的课程 ----
        courses = QWidget(); course_page = QVBoxLayout(courses)
        course_page.setContentsMargins(0, 0, 0, 0); course_page.setSpacing(12)
        course_card = QFrame(); course_card.setObjectName('card'); course_panel = QVBoxLayout(course_card)
        course_panel.setContentsMargins(20, 18, 20, 14); course_panel.setSpacing(12)
        course_panel.addWidget(QLabel('读到的课程'))
        self.course_table = QTableWidget(0, 2); self.course_table.setHorizontalHeaderLabels(['课程', '操作'])
        self.course_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.course_table.setColumnWidth(1, 150); self.course_table.verticalHeader().hide()
        self.course_table.setShowGrid(False); self.course_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.course_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.course_table.cellDoubleClicked.connect(self.open_course_page); course_panel.addWidget(self.course_table, 1)
        self.course_empty = QLabel('还没有读到课程。\n\n绑定慕课账号并点“检查作业”后，这里会列出所有课程。')
        self.course_empty.setAlignment(Qt.AlignmentFlag.AlignCenter); self.course_empty.setMinimumHeight(220)
        self.course_empty.setStyleSheet('color:#a4afc1;font-size:14px'); course_panel.addWidget(self.course_empty, 1)
        course_page.addWidget(course_card, 1)
        self.add_page('courses', courses)

        # ---- 发信记录 ----
        self.log_page = EmailLogPage()
        self.add_page('log', self.log_page)

        # ---- 连接详情 ----
        details = QWidget(); detail_page = QVBoxLayout(details)
        detail_page.setContentsMargins(0, 0, 0, 0); detail_page.setSpacing(12)
        detail_card = QFrame(); detail_card.setObjectName('card'); detail_panel = QVBoxLayout(detail_card)
        detail_panel.setContentsMargins(20, 18, 20, 14); detail_panel.setSpacing(12)
        detail_panel.addWidget(QLabel('当前状态'))
        self.details_text = QPlainTextEdit(); self.details_text.setReadOnly(True)
        self.details_text.setStyleSheet('background:#fbfcfe;border:1px solid #eef1f6')
        detail_panel.addWidget(self.details_text, 1); detail_page.addWidget(detail_card, 1)
        self.add_page('details', details)

        bottom = QLabel('“请核对”表示官网未明确显示提交状态。完成后勾选条目，可停止这项作业的本地提醒。'); bottom.setObjectName('muted'); bottom.setWordWrap(True); body.addWidget(bottom)
        footer = QHBoxLayout(); footer.addWidget(QLabel(f'{APP_NAME}  {VERSION}')); footer.addStretch(); footer.addWidget(QLabel('本机运行 · 北京时间')); body.addLayout(footer)
        layout.addWidget(main, 1); self.setCentralWidget(outer)
        self.show_page('overview', refresh=False)

    def add_page(self, key, widget):
        """Register one page of the middle stack, titled from PAGES."""
        self.page_index[key] = self.pages.addWidget(widget)
        self.page_titles[key] = PAGES[key]

    def show_page(self, key, refresh=True):
        """Switch the visible page and mark its sidebar entry as current."""
        if key not in self.page_index:
            return
        self.pages.setCurrentIndex(self.page_index[key])
        page = self.page_titles[key]
        self.page_title.setText(page['title']); self.page_subtitle.setText(page['subtitle'])
        self.crumb.setText(f"{page['group']}  /  {page['title']}")
        for name, button in self.nav_buttons.items():
            button.setProperty('active', name == key)
            # Qt only re-reads a dynamic property when the widget is repolished.
            button.style().unpolish(button); button.style().polish(button)
        if refresh:
            self.refresh()
        return key

    def current_page(self):
        """The key of the page on screen, for tests and for callers."""
        index = self.pages.currentIndex()
        for key, position in self.page_index.items():
            if position == index:
                return key
        return None

    def open_login(self):
        """Offer both ways in: the built-in window, or cookies from a browser."""
        dialog = QDialog(self)
        dialog.setWindowTitle('连接慕课')
        dialog.resize(640, 520)
        layout = QVBoxLayout(dialog)
        intro = QLabel('选择一种登录方式。两种方式都只把登录状态保存在本机。')
        intro.setObjectName('muted'); intro.setWordWrap(True); layout.addWidget(intro)

        builtin_title = QLabel('方式一：在内置窗口中登录'); builtin_title.setObjectName('sectionTitle')
        layout.addWidget(builtin_title)
        builtin_note = QLabel('打开一个内置浏览器窗口，在里面完成登录即可。适合大多数情况。')
        builtin_note.setObjectName('muted'); builtin_note.setWordWrap(True); layout.addWidget(builtin_note)
        builtin_row = QHBoxLayout()
        builtin = QPushButton('打开内置登录窗口'); builtin.setObjectName('primary')
        builtin_row.addWidget(builtin); builtin_row.addStretch(); layout.addLayout(builtin_row)

        cookie_title = QLabel('方式二：粘贴浏览器里的 Cookie'); cookie_title.setObjectName('sectionTitle')
        layout.addWidget(cookie_title)
        cookie_note = QLabel(
            '内置窗口打不开或登录不上时用这个。在已登录慕课的浏览器中按 F12 → '
            '网络（Network）→ 刷新页面 → 点第一个请求 → 找到“Cookie”请求头，'
            '把它的内容整段复制到这里。')
        cookie_note.setObjectName('muted'); cookie_note.setWordWrap(True); layout.addWidget(cookie_note)
        self.cookie_box = QPlainTextEdit()
        self.cookie_box.setPlaceholderText('NTESSTUDYSI=…; STUDY_SESS=…; EDUWEBDEVICE=…')
        layout.addWidget(self.cookie_box, 1)
        cookie_row = QHBoxLayout()
        cookie_hint = QLabel('只会读取慕课相关的 Cookie，其余内容会被忽略。')
        cookie_hint.setObjectName('muted'); cookie_hint.setWordWrap(True)
        cookie_row.addWidget(cookie_hint); cookie_row.addStretch()
        submit_cookies = QPushButton('用这些 Cookie 登录')
        cookie_row.addWidget(submit_cookies); layout.addLayout(cookie_row)
        self.cookie_error = QLabel(''); self.cookie_error.setObjectName('muted'); self.cookie_error.setWordWrap(True)
        layout.addWidget(self.cookie_error)
        close = QPushButton('关闭'); layout.addWidget(close)

        def use_builtin():
            dialog.accept()
            self.request('login')

        def use_cookies():
            pasted = self.cookie_box.toPlainText().strip()
            if not pasted:
                self.cookie_error.setText('请先粘贴 Cookie。')
                return
            # Pasting cookies needs the MOOC page (to check which account they
            # belong to), but never the mailbox, so it must not be gated on the
            # whole binding.
            if not self.binding_state()['mooc']:
                self.cookie_error.setText('请先在“绑定慕课与邮箱”里填好慕课课程主页，再粘贴 Cookie。')
                return
            found = browser.cookie_names_found(pasted)
            if not found:
                self.cookie_error.setText(
                    '没有读到慕课登录 Cookie。请确认复制的是“Cookie”请求头的全部内容，'
                    '里面应包含 NTESSTUDYSI、STUDY_SESS 等字段。')
                return
            dialog.accept()
            self.request('cookie_login', cookies=pasted)

        builtin.clicked.connect(use_builtin)
        submit_cookies.clicked.connect(use_cookies)
        close.clicked.connect(dialog.reject)
        dialog.exec()

    def request(self, command, **extra):
        try:
            self.controller.submit(command, extra or None)
            self.refresh()
        except Exception as exc:
            self.status.setText(str(exc))

    def tick(self):
        self.controller.tick()
        self.refresh()
        while self.controller.pending_popups:
            notice = self.controller.pending_popups.pop(0)
            text = f'这是一条测试提醒。最小化 {APP_NAME} 后，仍会提醒。' if notice['test'] else '\n\n'.join(f"{item['course']} · {item['name']}\n截止：{item['deadline']} · 剩余：{item['remaining']}" for item in notice['items'])
            box = QMessageBox(self); box.setWindowTitle(f'{APP_NAME} · 作业提醒'); box.setText('提醒已准备好' if notice['test'] else '作业即将截止'); box.setInformativeText(text); box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint); box.show()
        if self.controller.stopping and not self.controller.worker.is_alive():
            try:
                self.manager.shutdown()
            except Exception:
                # Never let a browser cleanup failure keep the window open.
                pass
            QApplication.instance().quit()

    def refresh(self, *_):
        data = self.controller.snapshot()
        self.refresh_accounts()
        bound = self.binding_state()
        self.binding.setVisible(not all(bound.values()))
        self.autostart.setVisible(not data['settings'].get('autostart_prompted') and not self.autostart_on)
        if not all(bound.values()):
            missing = [name for name, ok in (('慕课账号', bound['mooc']), ('接收提醒的邮箱', bound['email'])) if not ok]
            self.binding_detail.setText('尚未绑定：' + '、'.join(missing) + '。绑定后即可检查作业并收到提醒。')
        self.scan.setEnabled(not data['busy'] and all(bound.values()))
        self.login.setEnabled(not data['busy'] and bound['mooc'])
        self.scan.setToolTip('' if all(bound.values()) else '请先绑定慕课账号与邮箱')
        self.status.setText(data['phase'] + (f" · {data['elapsed']} 秒" if data['busy'] else ''))
        self.cancel.setVisible(data['busy'])
        self.connection.setText('●  已连接慕课' if data['connected'] else '●  等待完成登录' if data['job']=='waiting_login' else '●  尚未连接慕课')
        if self.tray is not None:
            self.tray.sync(data)
        for key, label in self.metrics.items(): label.setText(str(data['summary'][key]))
        self.interval.setText('自动检查已暂停' if data['paused'] else f"每 {data['settings']['interval_minutes']} 分钟检查")
        self.last_update.setText('上次检查：'+(data['last_scan'][11:16] if data['last_scan'] else '尚未检查'))
        query = self.search.text().strip().lower()
        items = [item for item in data['items'] if (self.show_past or not item['expired']) and (not query or query in (item['course']+' '+item['name']).lower())]
        signature = json.dumps([(i['uid'],i['done'],i['name'],i['course'],i['course_url'],i['kind'],
            i['deadline_label'],i['remaining'],i['status_label'],
            i['remaining_hours'] is not None and 0<i['remaining_hours']<=72) for i in items])
        # The table is only rebuilt when its contents actually change, so typing
        # in the search box stays responsive on a long list.
        if signature != self.last_signature:
            self.last_signature = signature
            self.current_items = items
            self.table.setRowCount(len(items))
            for row, item in enumerate(items):
                checkbox = QCheckBox(); checkbox.setChecked(item['done']); checkbox.stateChanged.connect(lambda _, uid=item['uid'], box=checkbox: self.complete(uid,box.isChecked())); self.table.setCellWidget(row,0,checkbox)
                for column, value in [(1,item['name']+'\n'+item['course']), (2,item['kind']), (3,item['deadline_label']), (4,item['remaining']), (5,item['status_label'])]:
                    cell = QTableWidgetItem(value); self.table.setItem(row,column,cell)
                    if item['remaining_hours'] is not None and 0<item['remaining_hours']<=72: cell.setBackground(QColor('#fff9ef'))
                self.table.setRowHeight(row,67)
        self.table.setVisible(bool(items)); self.empty.setVisible(not items)
        self.fill_overview(data['items'])
        self.fill_courses(data['courses'])
        self.log_page.set_log(data['email_log'], data['email_status'])
        self.fill_details(data)

    def fill_overview(self, items):
        """The overview page lists the few tasks that need attention first."""
        upcoming = [item for item in items if not item['done'] and not item['expired']][:5]
        self.overview_more.setVisible(bool(upcoming))
        if not upcoming:
            self.overview_list.setText('暂时没有待完成的作业。<br>绑定慕课账号与邮箱后，点“检查作业”就能读到它们。')
            return
        blocks = []
        for item in upcoming:
            blocks.append(
                f'<div style="margin-bottom:11px">'
                f'<b style="color:#3b4a63">{html.escape(str(item["name"]))}</b>'
                f'<span style="color:#a3adbe"> · {html.escape(str(item["course"]))}</span><br>'
                f'<span style="color:#849bc0">截止 {html.escape(str(item["deadline_label"]))} · '
                f'剩余 {html.escape(str(item["remaining"]))}</span></div>')
        self.overview_list.setText(''.join(blocks))

    def fill_courses(self, courses):
        self.course_rows = list(courses)
        self.course_table.setVisible(bool(courses)); self.course_empty.setVisible(not courses)
        self.course_table.setRowCount(len(courses))
        for row, course in enumerate(courses):
            name = QTableWidgetItem(str(course.get('name', '')))
            link = QTableWidgetItem('双击打开课程页')
            link.setData(Qt.ItemDataRole.UserRole, str(course.get('url', '')))
            self.course_table.setItem(row, 0, name); self.course_table.setItem(row, 1, link)
            self.course_table.setRowHeight(row, 40)

    def fill_details(self, data):
        lines = [f"当前状态：{data['phase']}",
                 f"是否已连接慕课：{'是' if data['connected'] else '否'}",
                 f"下次自动检查：{data['next_check']} 秒后",
                 f"上次检查：{data['last_scan'] or '尚未检查'}",
                 f"数据位置：{data['data_folder']}",
                 '', '错误记录：']
        lines.extend(data['errors'] or ['暂无错误记录。'])
        lines.extend(['', '最近一次邮件结果：', data['email_status'] or '暂无'])
        self.details_text.setPlainText('\n'.join(lines))

    def open_course_page(self, row, column):
        if row < len(getattr(self, 'course_rows', [])):
            url = str(self.course_rows[row].get('url', ''))
            if url:
                import webbrowser
                webbrowser.open(url)

    def complete(self, uid, checked):
        item = next((item for item in self.store.items() if item.uid==uid),None)
        if item: self.store.mark_completed(item,checked); self.refresh()

    def open_course(self, row, column):
        if row < len(self.current_items):
            import webbrowser
            webbrowser.open(self.current_items[row]['course_url'])

    def toggle_pause(self):
        self.controller.paused = not self.controller.paused
        self.pause.setText('恢复' if self.controller.paused else '暂停')
        if self.tray is not None:
            self.tray.pause_action.setChecked(self.controller.paused)
        self.status.setText('已暂停提醒，作业不会再检查。' if self.controller.paused else '已恢复提醒。')

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def details(self):
        """Kept as an entry point; the content lives on its own page now."""
        return self.show_page('details')

    def show_courses(self):
        return self.show_page('courses')

    def open_binding(self):
        """Bind the MOOC course page and the reminder email for this account."""
        state = self.binding_state()
        dialog = QDialog(self)
        dialog.setWindowTitle('绑定慕课与邮箱')
        dialog.resize(600, 620)
        layout = QVBoxLayout(dialog)
        intro = QLabel('每个账号各自绑定：慕课主页决定读取谁的作业，邮箱决定提醒发到哪里。')
        intro.setWordWrap(True); intro.setObjectName('muted'); layout.addWidget(intro)
        settings = self.store.data['settings']
        mooc_title = QLabel('一、慕课账号'); mooc_title.setObjectName('sectionTitle'); layout.addWidget(mooc_title)
        mooc_form = QFormLayout()
        home = QLineEdit('' if account_id(settings['home_url']) in ('', '0') else settings['home_url'])
        home.setPlaceholderText('https://www.icourse163.org/home.htm?userId=你的数字账号#/home/course')
        mooc_form.addRow('课程主页', home)
        layout.addLayout(mooc_form)
        mooc_hint = QLabel('登录慕课后打开个人主页，地址栏里的 userId 就是你的账号。')
        mooc_hint.setObjectName('muted'); mooc_hint.setWordWrap(True); layout.addWidget(mooc_hint)
        email_title = QLabel('二、接收提醒的邮箱'); email_title.setObjectName('sectionTitle'); layout.addWidget(email_title)
        form = EmailSettingsForm(settings.get('email'), dialog, self.workspace)
        form.enabled.setChecked(True)
        layout.addWidget(form)
        self._email_dialog = (dialog, form)
        form.test_requested.connect(lambda patch: self.send_test_email(dialog, form, patch))
        layout.addStretch()
        buttons = QHBoxLayout()
        save = QPushButton('保存绑定'); save.setObjectName('primary')
        cancel = QPushButton('稍后再说')
        buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(save)
        layout.addLayout(buttons)

        def apply():
            try:
                warnings = self.controller.settings(dict(home_url=home.text().strip(), email=form.values()))
                self.profiles.save_shared(self.store.data['settings'])
                self.last_signature = None
            except Exception as exc:
                QMessageBox.warning(dialog, '绑定未保存', str(exc))
                return
            dialog.accept()
            self.refresh()
            if warnings:
                # The course link and the mailbox are saved together; a mailbox
                # that is not ready must not hide the fact that the link stuck.
                self.status.setText('　'.join(warnings))
            elif self.store.is_bound():
                self.status.setText('绑定完成，正在检查作业…')
                self.request('scan')
            else:
                missing = self.binding_state()
                self.status.setText('还差' + ('慕课主页' if not missing['mooc'] else '邮箱') + '，补齐后即可检查作业。')

        save.clicked.connect(apply)
        cancel.clicked.connect(dialog.reject)
        dialog.exec()

    def binding_state(self):
        return core_binding_state(self.store.data['settings'])

    def set_autostart(self, wanted):
        """Turn the sign-in entry on or off.  Returns a warning, or ''.

        The truth lives in the Startup folder, not in the settings file, so the
        state is read back rather than assumed -- and re-read into the cached
        flag the checkbox and the prompt card follow.
        """
        wanted = bool(wanted)
        try:
            if wanted:
                autostart.enable(self.workspace, python=sys.executable)
            else:
                autostart.disable(self.workspace)
            self.autostart_on = autostart.enabled(self.workspace)
            return ''
        except Exception as exc:
            self.autostart_on = autostart.enabled(self.workspace)
            return '开机自动启动' + ('未开启：' if wanted else '未关闭：') + str(exc)

    def accept_autostart(self):
        warning = self.set_autostart(True)
        self.finish_autostart_prompt()
        if warning:
            QMessageBox.warning(self, '未能开启', warning)

    def decline_autostart(self):
        self.finish_autostart_prompt()

    def finish_autostart_prompt(self):
        """Remember the answer so the card never comes back."""
        try:
            self.controller.settings(dict(autostart_prompted=True))
            self.profiles.save_shared(self.store.data['settings'])
        except Exception:
            pass
        self.last_signature = None
        self.refresh()

    def open_email_settings(self):
        """Reachable before signing in to MOOC, so the email can be set up first."""
        patch = self.edit_email_dialog()
        if patch is None:
            return
        try:
            warnings = self.controller.settings(dict(email=patch))
        except Exception as exc:
            QMessageBox.warning(self, '邮件设置未保存', str(exc))
            return
        self.last_signature = None
        self.refresh()
        self.status.setText('邮件提醒设置已保存。' + ('　' + '　'.join(warnings) if warnings else ''))

    def edit_email_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('邮件提醒设置')
        dialog.resize(560, 520)
        layout = QVBoxLayout(dialog)
        settings = self.store.data['settings']
        form = EmailSettingsForm(settings.get('email'), dialog, self.workspace)
        layout.addWidget(form)
        self._email_dialog = (dialog, form)
        form.test_requested.connect(lambda patch: self.send_test_email(dialog, form, patch))
        layout.addStretch()
        buttons = QHBoxLayout()
        save = QPushButton('保存'); save.setObjectName('primary')
        cancel = QPushButton('取消')
        buttons.addStretch(); buttons.addWidget(cancel); buttons.addWidget(save)
        layout.addLayout(buttons)
        result = {}

        def accept():
            result['patch'] = form.values()
            dialog.accept()

        save.clicked.connect(accept)
        cancel.clicked.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return result.get('patch')

    def current_account_key(self):
        """Which profile the window is showing right now."""
        home = self.store.data['settings'].get('home_url', '')
        for entry in self.profiles.list_profiles():
            if entry['home_url'] == home:
                return entry['key']
        return ''

    def switch_account(self, key):
        if key == self.current_account_key():
            return
        try:
            self.profiles.set_active(key)
            store = self.profiles.load_store()
        except Exception as exc:
            QMessageBox.warning(self, '切换账号失败', str(exc))
            self.refresh()
            return
        self.store = store
        self.last_signature = None
        resuming = self.controller.replace_store(store)
        self.account_combo.blockSignals(True)
        self.account_combo.setCurrentIndex(max(0, self.account_combo.findData(key)))
        self.account_combo.blockSignals(False)
        name = store.data['settings'].get('name') or '另一个账号'
        self.status.setText(('正在恢复' + name + '上次的结果…') if resuming else
                            (name + '尚未连接慕课，请点击“连接慕课”。'))
        self.refresh()

    def add_account(self):
        dialog = RegisterDialog(self.profiles, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.key:
            self.refresh()
            return
        self.store = self.profiles.load_store()
        self.last_signature = None
        self.controller.replace_store(self.store)
        self.refresh_accounts()
        self.refresh()
        self.status.setText('已添加账号，请点击“绑定慕课与邮箱”完成绑定。')

    def refresh_accounts(self):
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        for entry in self.profiles.list_profiles():
            self.account_combo.addItem(entry['label'], entry['key'])
        self.account_combo.addItem('添加账号…', '__add__')
        current = self.current_account_key()
        index = self.account_combo.findData(current)
        self.account_combo.setCurrentIndex(max(0, index))
        self.account_combo.blockSignals(False)
        label = current
        for entry in self.profiles.list_profiles():
            if entry['key'] == current:
                label = entry['label']
        self.account_label.setText('当前账号')
        self.account_summary.setText(label)

    def account_selected(self, index):
        key = self.account_combo.itemData(index)
        if key == '__add__':
            self.add_account()
        elif key:
            self.switch_account(key)

    def settings(self):
        dialog=QDialog(self);dialog.setWindowTitle('提醒设置');dialog.resize(700,760);layout=QVBoxLayout(dialog);form=QFormLayout();settings=self.store.data['settings']
        home=QLineEdit(settings['home_url']);hours=QLineEdit(','.join(str(v) for v in settings['reminder_hours']));interval=QSpinBox();interval.setRange(5,1440);interval.setValue(settings['interval_minutes'])
        manual=QPlainTextEdit('\n'.join(settings['manual_urls']));quizzes=QCheckBox('同时提醒测验');quizzes.setChecked(settings['include_quizzes']);finished=QCheckBox('检查已结束学期');finished.setChecked(settings['include_finished'])
        form.addRow('课程主页',home);form.addRow('提前提醒（小时）',hours);form.addRow('检查间隔（分钟）',interval);form.addRow('补充作业链接',manual);form.addRow(quizzes);form.addRow(finished)
        boot = QCheckBox('开机时自动启动（在系统托盘里运行）'); boot.setChecked(self.autostart_on)
        form.addRow(boot)
        layout.addLayout(form)
        # The two kinds of mail are independent, which is not obvious from the
        # field labels alone: users could not tell which time actually sends.
        reminder_note=QLabel('“提前提醒”：作业还剩上面这些小时数时，立刻发一封提醒邮件，可填多个（用逗号分开）。')
        reminder_note.setObjectName('muted');reminder_note.setWordWrap(True);layout.addWidget(reminder_note)
        divider=QLabel('邮件提醒');divider.setObjectName('sectionTitle');layout.addWidget(divider)
        mail=EmailSettingsForm(settings.get('email'),dialog,self.workspace);layout.addWidget(mail)
        self._email_dialog = (dialog, mail)
        mail.test_requested.connect(lambda patch: self.send_test_email(dialog,mail,patch))
        layout.addStretch()
        save=QPushButton('保存设置');save.setObjectName('primary');layout.addWidget(save)
        def apply():
            try:
                warnings=self.controller.settings(dict(home_url=home.text().strip(),reminder_hours=[float(v.strip()) for v in hours.text().replace('，',',').split(',')],interval_minutes=interval.value(),manual_urls=[v.strip() for v in manual.toPlainText().splitlines() if v.strip()],include_quizzes=quizzes.isChecked(),include_finished=finished.isChecked(),email=mail.values()))
                self.profiles.save_shared(self.store.data['settings'])
            except Exception as exc:
                QMessageBox.warning(dialog,'设置未保存',str(exc));return
            if boot.isChecked() != self.autostart_on:
                trouble = self.set_autostart(boot.isChecked())
                if trouble:
                    QMessageBox.warning(dialog,'开机自启动未生效',trouble);return
            self.last_signature=None
            dialog.accept();self.refresh()
            self.status.setText('设置已保存。'+('　'+'　'.join(warnings) if warnings else ''))
        save.clicked.connect(apply);dialog.exec()

    def mailer_idle(self):
        return not self.controller.mailer.inflight

    def send_test_email(self, dialog, form, patch):
        """Send one message and report the outcome inside the settings dialog."""
        try:
            merged = {**self.controller.email_config(), **patch}
            self.controller.send_test_email(merged)
        except Exception as exc:
            form.hint.setText('测试邮件未发送：'+str(exc))
            return False
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            QApplication.processEvents()
            if self.mailer_idle():
                status = self.controller.snapshot()['email_status']
                form.hint.setText(status or '测试邮件已发送，请查收。')
                return True
            time.sleep(0.05)
        form.hint.setText('测试邮件仍在发送中，稍后可在“连接详情”查看结果。')
        return False

    def closeEvent(self,event):
        if self.quitting or self.tray is None:
            self.controller.stop()
            self.hide()
            event.accept()
            return
        # With a tray icon in place, closing means "get out of my way", not
        # "quit": the reminders are the whole point of the program, and stopping
        # the controller here is what previously left it running but unreachable.
        self.hide()
        self.tray.showMessage(APP_NAME, '已最小化到托盘，提醒仍在后台运行。',
            QSystemTrayIcon.MessageIcon.Information, 4000)
        event.ignore()


class SingleInstance:
    """One running copy per data folder.

    Autostart turns "the user opens the program twice" from a harmless mistake
    into a routine event, and two copies would check the same account and mail
    every reminder twice.  The second copy hands the message over and leaves.

    A local socket is used rather than a lock file because it also carries the
    "come to the front" request; a lock alone would make the second click look
    like nothing happened.
    """

    def __init__(self, name):
        self.name = name
        self.server = None

    @staticmethod
    def name_for(folder):
        digest = hashlib.sha1(str(Path(folder).resolve()).encode('utf-8')).hexdigest()[:16]
        return 'MoocReminder-' + digest

    def claim(self):
        """True when this process should run the program, False if it should not."""
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        if probe.waitForConnected(400):
            probe.write(b'show')
            # This process is about to exit, so the request has to leave the
            # buffer first.  A request still sitting here would make the second
            # launch look like nothing happened.
            deadline = time.monotonic() + 2
            while probe.bytesToWrite() and time.monotonic() < deadline:
                probe.flush()
                probe.waitForBytesWritten(100)
                QApplication.processEvents()
            probe.disconnectFromServer()
            if probe.state() == QLocalSocket.LocalSocketState.ConnectedState:
                probe.waitForDisconnected(400)
            return False
        # A copy that crashed can leave the socket behind.  Nothing answered a
        # moment ago, so clearing it is safe and beats refusing to start.
        QLocalServer.removeServer(self.name)
        self.server = QLocalServer()
        if not self.server.listen(self.name):
            # Cannot arbitrate: running is better than silently doing nothing.
            self.server = None
        return True

    def on_activate(self, callback):
        """Call `callback` when another copy asks for the window."""
        if self.server is not None:
            self.server.newConnection.connect(lambda: self._receive(callback))

    def _receive(self, callback):
        connection = self.server.nextPendingConnection()
        if connection is None:
            return
        state = {'buffer': bytearray(), 'done': False}

        def settle(signal):
            # The bytes can be sitting in the buffer before this runs, arrive
            # in a readyRead, or only show up as the sender goes away, so both
            # signals feed one buffer and whichever sees the whole message acts.
            if state['done']:
                # Already handled: the other signal arrives after the socket is
                # gone, and touching it then would raise.
                return
            state['buffer'].extend(bytes(connection.readAll()))
            if bytes(state['buffer']).startswith(b'show'):
                state['done'] = True
                callback()
            elif signal != 'gone':
                return
            else:
                state['done'] = True
            connection.readyRead.disconnect()
            connection.disconnected.disconnect()
            connection.deleteLater()

        connection.readyRead.connect(lambda: settle('ready'))
        connection.disconnected.connect(lambda: settle('gone'))
        settle('ready')


def write_startup_report(path, **fields):
    """Tell the launcher the program is up, and in what shape."""
    if not path:
        return
    target = Path(path)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(fields), encoding='utf-8')
    temporary.replace(target)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir');parser.add_argument('--smoke-report');parser.add_argument('--preview');parser.add_argument('--startup-report')
    parser.add_argument('--background',action='store_true',help='start hidden in the tray, as at sign-in')
    args=parser.parse_args()
    app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False);app.setApplicationName(APP_NAME)
    # Each account keeps its own profile; the program reopens the last one used.
    account = profiles_module.Profiles(args.data_dir)
    if args.background and not account.account_options():
        # Signing in before the program has ever been set up: stay quiet rather
        # than park an unusable icon in the tray.
        return 0
    single = None
    if not args.smoke_report:
        single = SingleInstance(SingleInstance.name_for(account.folder))
        if not single.claim():
            # Another copy already runs this account; it now has the window.
            write_startup_report(args.startup_report, ready=True, already_running=True,
                background=args.background)
            return 0
    if not args.smoke_report and not args.background:
        # Signing in is the first thing the program asks for.  At sign-in there
        # is nobody to answer, so the locked screen would just block the tray.
        gate = LockScreen(account)
        if gate.exec() != QDialog.DialogCode.Accepted:
            return 0
    window=Window(account.load_store(), profiles=account, tray=not args.smoke_report)
    if single is not None:
        single.on_activate(window.tray.show_window if window.tray else window.show)
    if not args.background:
        window.show()
    if args.startup_report:
        def startup_ready():
            write_startup_report(args.startup_report, ready=True, native_window=window.isVisible(),
                background=args.background)
        QTimer.singleShot(150,startup_ready)
    if args.smoke_report:
        def report():
            if args.preview: window.grab().save(args.preview)
            Path(args.smoke_report).write_text(json.dumps({'native_window':window.isVisible(),'controls_ready':window.login.isEnabled(),'name':APP_NAME,'version':VERSION}),encoding='utf-8')
            window.close()
        QTimer.singleShot(1600,report)
    result=app.exec();window.controller.worker.join(timeout=2);return result



if __name__=='__main__':
    sys.exit(main())
