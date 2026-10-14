"""Native macOS-inspired desktop, with a shared embedded MOOC browser."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import time

from storage import Store
from version import APP_NAME, VERSION
from qt_bootstrap import prepare_qt

_data = Path(__file__).parent / 'data'
prepare_qt(_data)

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QDialog, QFormLayout,
    QSpinBox, QComboBox, QPlainTextEdit, QMessageBox, QFrame)
from portal import Controller
import emailer
import profiles as profiles_module
from core import account_id, binding_state as core_binding_state
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


class EmailSettingsForm(QWidget):
    """Email reminder fields; testable on its own without the main window.

    The authorization code follows one rule: only a value the user actually
    typed is submitted, so leaving the field blank keeps the stored secret.
    """

    test_requested = Signal(dict)

    def __init__(self, email, parent=None):
        super().__init__(parent)
        email = dict(email or {})
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
        form.addRow('接收提醒的邮箱', self.recipient)
        form.addRow('每日发信时刻', self.digest)
        layout.addLayout(form)
        # Everything below is optional: the program ships a sender, so an
        # ordinary user only needs the address above.
        self.advanced_toggle = QCheckBox('高级选项（自定义发件邮箱与服务器）')
        self.advanced_toggle.setChecked(bool(email.get('advanced')))
        layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced_form = QFormLayout(self.advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.addRow('发件邮箱', self.sender)
        advanced_form.addRow('邮件服务器', self.server)
        advanced_form.addRow('端口', self.port)
        advanced_form.addRow('加密方式', self.security)
        advanced_form.addRow('授权码', self.password)
        layout.addWidget(self.advanced)
        self.advanced.setVisible(self.advanced_toggle.isChecked())
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName('muted')
        layout.addWidget(self.hint)
        state = QHBoxLayout()
        self.test_button = QPushButton('发送测试邮件')
        self.clear_password = QPushButton('清除已保存的授权码')
        self.clear_password.setVisible(self.saved_password)
        state.addWidget(self.test_button)
        state.addWidget(self.clear_password)
        state.addStretch()
        layout.addLayout(state)
        note = QLabel('提醒由程序预置的发件邮箱统一发出；填好上面的邮箱即可，无需了解邮件服务器。')
        note.setObjectName('muted')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.recipient.textChanged.connect(self.autofill)
        self.sender.textChanged.connect(self.autofill)
        self.password.textEdited.connect(self.mark_password)
        self.clear_password.clicked.connect(self.forget)
        self.test_button.clicked.connect(self.request_test)
        self.advanced_toggle.toggled.connect(lambda _: self.autofill(self.recipient.text()))
        self.autofill(self.recipient.text())

    def mark_password(self, *_):
        self.password_touched = True
        self.forget_password = False

    def forget(self):
        self.password.clear()
        self.forget_password = True
        self.saved_password = False
        self.clear_password.setVisible(False)
        self.hint.setText('保存后本机不再保留授权码，需要重新填写才能发信。')

    def autofill(self, text):
        """Offer a provider preset for the sender the user is configuring.

        Only in advanced mode: the server has to match whoever actually sends,
        which by default is the shipped sender, not the address being typed in
        as the recipient.
        """
        self.test_button.setEnabled(bool(str(text).strip()))
        if not self.advanced_toggle.isChecked():
            self.hint.setText('提醒会从程序预置的发件邮箱发出，你只需填写自己的邮箱。' if str(text).strip() else '')
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

    def values(self):
        """Return the settings patch; omit the code unless the user typed one."""
        config = dict(enabled=self.enabled.isChecked(), recipient=self.recipient.text().strip(),
                      advanced=self.advanced_toggle.isChecked(),
                      sender=self.sender.text().strip(), server=self.server.text().strip(),
                      port=self.port.value(), security=self.security.currentData(),
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
        self.setWindowIcon(QIcon(str(Path(__file__).parent/'assets/MoocReminder.ico')))
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


class Window(QMainWindow):
    def __init__(self, store, profiles=None):
        super().__init__()
        self.store = store
        self.profiles = profiles or profiles_module.Profiles(profiles_module.folder_for_store(store.folder))
        self._email_dialog = None
        self.manager = Manager(store.folder, self)
        self.controller = Controller(store, context_provider=self.manager.context, profile_dir='web-profiles')
        self.manager.on_error = lambda message: self.controller.events.put(('error', message))
        self.setWindowTitle(f'{APP_NAME} · 作业提醒 {VERSION}')
        self.setWindowIcon(QIcon(str(Path(__file__).parent/'assets/MoocReminder.ico')))
        self.resize(1260, 850)
        self.setMinimumSize(1020, 670)
        self.setStyleSheet(STYLE)
        self.show_past = False
        self.last_signature = None
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(250)
        self.refresh()

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
        caption = QLabel('学习空间'); caption.setObjectName('muted'); sidebar.addWidget(caption)
        for text, action, active in [('◫   总览', self.refresh, True), ('☷   作业清单', self.refresh, False), ('▤   我的课程', self.show_courses, False)]:
            button = QPushButton(text); button.setObjectName('nav'); button.setProperty('active', active); button.clicked.connect(action); sidebar.addWidget(button)
        sidebar.addSpacing(20); label = QLabel('偏好设置'); label.setObjectName('muted'); sidebar.addWidget(label)
        for text, action in [('♧   提醒设置', self.settings), ('ⓘ   连接详情', self.details)]:
            button = QPushButton(text); button.setObjectName('nav'); button.clicked.connect(action); sidebar.addWidget(button)
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
        top = QHBoxLayout(); crumb = QLabel('学习空间  /  总览'); crumb.setObjectName('muted'); top.addWidget(crumb); top.addStretch()
        date = QLabel(datetime.now().strftime('%m月%d日')); date.setObjectName('muted'); top.addWidget(date)
        self.email_button = QPushButton('邮件提醒'); self.email_button.setFixedWidth(96)
        self.email_button.setToolTip('设置接收提醒的邮箱，无需先登录慕课')
        self.email_button.clicked.connect(self.open_email_settings); top.addWidget(self.email_button)
        self.pause = QPushButton('暂停'); self.pause.setFixedWidth(72); self.pause.clicked.connect(self.toggle_pause); top.addWidget(self.pause); body.addLayout(top)
        heading = QHBoxLayout(); title_block = QVBoxLayout(); eyebrow = QLabel('A LITTLE MORE ORGANIZED'); eyebrow.setObjectName('muted'); title_block.addWidget(eyebrow)
        title = QLabel('作业概览'); title.setObjectName('title'); title_block.addWidget(title); title_block.addWidget(QLabel('截止时间看清楚，学习节奏更从容。')); heading.addLayout(title_block); heading.addStretch()
        self.login = QPushButton('连接慕课'); self.login.clicked.connect(self.open_login); heading.addWidget(self.login)
        self.scan = QPushButton('检查作业'); self.scan.setObjectName('primary'); self.scan.clicked.connect(lambda: self.request('scan')); heading.addWidget(self.scan); body.addLayout(heading)
        self.status = QLabel('在内置窗口中登录慕课，再回来检查作业。'); self.status.setWordWrap(True); self.status.setStyleSheet('background:#edf3ff;border:1px solid #e0e9fb;border-radius:8px;padding:12px;color:#849bc0'); body.addWidget(self.status)
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
        body.addWidget(self.binding)
        metrics = QHBoxLayout(); metrics.setSpacing(17); self.metrics = {}
        for key, name, desc in [('pending', '待完成 / 待核对', '所有尚未截止的任务'), ('soon', '3 天内截止', '优先安排时间完成'), ('courses', '已读取课程', 'MOOC · 校内 SPOC')]:
            card = QFrame(); card.setObjectName('card'); card_layout = QVBoxLayout(card); card_layout.setContentsMargins(21, 15, 21, 16); card_layout.addWidget(QLabel(name))
            value = QLabel('0'); value.setObjectName('metric'); card_layout.addWidget(value); self.metrics[key] = value
            desc_label = QLabel(desc); desc_label.setObjectName('muted'); card_layout.addWidget(desc_label); metrics.addWidget(card)
        body.addLayout(metrics)
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
        test = QPushButton('测试提醒'); test.clicked.connect(lambda: self.controller.add_notification(test=True)); row.addWidget(test); panel.addLayout(row); body.addWidget(card, 1)
        bottom = QLabel('“请核对”表示官网未明确显示提交状态。完成后勾选条目，可停止这项作业的本地提醒。'); bottom.setObjectName('muted'); bottom.setWordWrap(True); body.addWidget(bottom)
        footer = QHBoxLayout(); footer.addWidget(QLabel(f'{APP_NAME}  {VERSION}')); footer.addStretch(); footer.addWidget(QLabel('本机运行 · 北京时间')); body.addLayout(footer)
        layout.addWidget(main, 1); self.setCentralWidget(outer)

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
            if not self.store.is_bound():
                self.cookie_error.setText('请先绑定慕课主页与邮箱，再登录。')
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
            self.manager.shutdown()
            QApplication.instance().quit()

    def refresh(self, *_):
        data = self.controller.snapshot()
        self.refresh_accounts()
        bound = self.binding_state()
        self.binding.setVisible(not all(bound.values()))
        if not all(bound.values()):
            missing = [name for name, ok in (('慕课账号', bound['mooc']), ('接收提醒的邮箱', bound['email'])) if not ok]
            self.binding_detail.setText('尚未绑定：' + '、'.join(missing) + '。绑定后即可检查作业并收到提醒。')
        self.scan.setEnabled(not data['busy'] and all(bound.values()))
        self.login.setEnabled(not data['busy'] and bound['mooc'])
        self.scan.setToolTip('' if all(bound.values()) else '请先绑定慕课账号与邮箱')
        self.status.setText(data['phase'] + (f" · {data['elapsed']} 秒" if data['busy'] else ''))
        self.cancel.setVisible(data['busy'])
        self.connection.setText('●  已连接慕课' if data['connected'] else '●  等待完成登录' if data['job']=='waiting_login' else '●  尚未连接慕课')
        for key, label in self.metrics.items(): label.setText(str(data['summary'][key]))
        self.interval.setText('自动检查已暂停' if data['paused'] else f"每 {data['settings']['interval_minutes']} 分钟检查")
        self.last_update.setText('上次检查：'+(data['last_scan'][11:16] if data['last_scan'] else '尚未检查'))
        query = self.search.text().strip().lower()
        items = [item for item in data['items'] if (self.show_past or not item['expired']) and (not query or query in (item['course']+' '+item['name']).lower())]
        signature = json.dumps([(i['uid'],i['done'],i['name'],i['course'],i['course_url'],i['kind'],
            i['deadline_label'],i['remaining'],i['status_label'],
            i['remaining_hours'] is not None and 0<i['remaining_hours']<=72) for i in items])
        if signature == self.last_signature:
            return
        self.last_signature = signature
        self.current_items = items
        self.table.setVisible(bool(items)); self.empty.setVisible(not items); self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            checkbox = QCheckBox(); checkbox.setChecked(item['done']); checkbox.stateChanged.connect(lambda _, uid=item['uid'], box=checkbox: self.complete(uid,box.isChecked())); self.table.setCellWidget(row,0,checkbox)
            for column, value in [(1,item['name']+'\n'+item['course']), (2,item['kind']), (3,item['deadline_label']), (4,item['remaining']), (5,item['status_label'])]:
                cell = QTableWidgetItem(value); self.table.setItem(row,column,cell)
                if item['remaining_hours'] is not None and 0<item['remaining_hours']<=72: cell.setBackground(QColor('#fff9ef'))
            self.table.setRowHeight(row,67)

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

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def details(self):
        data = self.controller.snapshot()
        QMessageBox.information(self,'连接详情', data['phase']+'\n\n'+('\n'.join(data['errors']) or '暂无错误记录。')+'\n\n数据位置：'+str(self.store.folder))

    def show_courses(self):
        QMessageBox.information(self,'我的课程','\n\n'.join(course['name'] for course in self.store.data['courses']) or '首次检查后，课程会自动显示。')

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
        form = EmailSettingsForm(settings.get('email'), dialog)
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
                self.controller.settings(dict(home_url=home.text().strip(), email=form.values()))
                self.profiles.save_shared(self.store.data['settings'])
                self.last_signature = None
            except Exception as exc:
                QMessageBox.warning(dialog, '绑定未保存', str(exc))
                return
            dialog.accept()
            self.refresh()
            if self.store.is_bound():
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

    def open_email_settings(self):
        """Reachable before signing in to MOOC, so the email can be set up first."""
        patch = self.edit_email_dialog()
        if patch is None:
            return
        try:
            self.controller.settings(dict(email=patch))
            self.refresh()
            self.status.setText('邮件提醒设置已保存。')
        except Exception as exc:
            QMessageBox.warning(self, '邮件设置未保存', str(exc))

    def edit_email_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('邮件提醒设置')
        dialog.resize(560, 520)
        layout = QVBoxLayout(dialog)
        settings = self.store.data['settings']
        form = EmailSettingsForm(settings.get('email'), dialog)
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
        form.addRow('课程主页',home);form.addRow('提前提醒（小时）',hours);form.addRow('检查间隔（分钟）',interval);form.addRow('补充作业链接',manual);form.addRow(quizzes);form.addRow(finished);layout.addLayout(form)
        divider=QLabel('邮件提醒');divider.setObjectName('sectionTitle');layout.addWidget(divider)
        mail=EmailSettingsForm(settings.get('email'),dialog);layout.addWidget(mail)
        self._email_dialog = (dialog, mail)
        mail.test_requested.connect(lambda patch: self.send_test_email(dialog,mail,patch))
        layout.addStretch()
        save=QPushButton('保存设置');save.setObjectName('primary');layout.addWidget(save)
        def apply():
            try:
                self.controller.settings(dict(home_url=home.text().strip(),reminder_hours=[float(v.strip()) for v in hours.text().replace('，',',').split(',')],interval_minutes=interval.value(),manual_urls=[v.strip() for v in manual.toPlainText().splitlines() if v.strip()],include_quizzes=quizzes.isChecked(),include_finished=finished.isChecked(),email=mail.values()))
                self.profiles.save_shared(self.store.data['settings'])
                dialog.accept();self.refresh()
            except Exception as exc: QMessageBox.warning(dialog,'设置未保存',str(exc))
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
        self.controller.stop()
        self.hide()
        event.accept()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir');parser.add_argument('--smoke-report');parser.add_argument('--preview');parser.add_argument('--startup-report');args=parser.parse_args()
    app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False);app.setApplicationName(APP_NAME)
    # Each account keeps its own profile; the program reopens the last one used.
    account = profiles_module.Profiles(args.data_dir)
    if not args.smoke_report:
        # Signing in is the first thing the program asks for.
        gate = LockScreen(account)
        if gate.exec() != QDialog.DialogCode.Accepted:
            return 0
    window=Window(account.load_store(), profiles=account);window.show()
    if args.startup_report:
        def startup_ready():
            target=Path(args.startup_report);temporary=target.with_suffix('.tmp')
            temporary.write_text(json.dumps({'native_window':window.isVisible()}),encoding='utf-8')
            temporary.replace(target)
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
