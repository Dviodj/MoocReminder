"""Native macOS-inspired desktop, with a shared embedded MOOC browser."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import threading

from storage import Store
from version import APP_NAME, VERSION
from qt_bootstrap import prepare_qt

_data = Path(__file__).parent / 'data'
prepare_qt(_data)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QDialog, QFormLayout,
    QSpinBox, QPlainTextEdit, QMessageBox, QFrame)
from portal import Controller
from webview2_reader import Manager

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


class Window(QMainWindow):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.manager = Manager(store.folder, self)
        self.controller = Controller(store, context_provider=self.manager.context, profile_dir='webview2-profiles')
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
        sidebar.addSpacing(21); self.connection = QLabel('●  尚未连接慕课'); self.connection.setObjectName('muted'); sidebar.addWidget(self.connection)
        layout.addWidget(side)
        main = QWidget(); body = QVBoxLayout(main); body.setContentsMargins(32, 23, 32, 19); body.setSpacing(18)
        top = QHBoxLayout(); crumb = QLabel('学习空间  /  总览'); crumb.setObjectName('muted'); top.addWidget(crumb); top.addStretch()
        date = QLabel(datetime.now().strftime('%m月%d日')); date.setObjectName('muted'); top.addWidget(date)
        self.pause = QPushButton('暂停'); self.pause.setFixedWidth(72); self.pause.clicked.connect(self.toggle_pause); top.addWidget(self.pause); body.addLayout(top)
        heading = QHBoxLayout(); title_block = QVBoxLayout(); eyebrow = QLabel('A LITTLE MORE ORGANIZED'); eyebrow.setObjectName('muted'); title_block.addWidget(eyebrow)
        title = QLabel('作业概览'); title.setObjectName('title'); title_block.addWidget(title); title_block.addWidget(QLabel('截止时间看清楚，学习节奏更从容。')); heading.addLayout(title_block); heading.addStretch()
        self.login = QPushButton('连接慕课'); self.login.clicked.connect(lambda: self.request('login')); heading.addWidget(self.login)
        self.scan = QPushButton('检查作业'); self.scan.setObjectName('primary'); self.scan.clicked.connect(lambda: self.request('scan')); heading.addWidget(self.scan); body.addLayout(heading)
        self.status = QLabel('在内置窗口中登录慕课，再回来检查作业。'); self.status.setWordWrap(True); self.status.setStyleSheet('background:#edf3ff;border:1px solid #e0e9fb;border-radius:8px;padding:12px;color:#849bc0'); body.addWidget(self.status)
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

    def request(self, command):
        try:
            self.controller.submit(command)
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
        self.status.setText(data['phase'] + (f" · {data['elapsed']} 秒" if data['busy'] else ''))
        self.login.setEnabled(not data['busy']); self.scan.setEnabled(not data['busy']); self.cancel.setVisible(data['busy'])
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

    def settings(self):
        dialog=QDialog(self);dialog.setWindowTitle('提醒设置');dialog.resize(700,480);layout=QVBoxLayout(dialog);form=QFormLayout();settings=self.store.data['settings']
        home=QLineEdit(settings['home_url']);hours=QLineEdit(','.join(str(v) for v in settings['reminder_hours']));interval=QSpinBox();interval.setRange(5,1440);interval.setValue(settings['interval_minutes'])
        manual=QPlainTextEdit('\n'.join(settings['manual_urls']));quizzes=QCheckBox('同时提醒测验');quizzes.setChecked(settings['include_quizzes']);finished=QCheckBox('检查已结束学期');finished.setChecked(settings['include_finished'])
        form.addRow('课程主页',home);form.addRow('提前提醒（小时）',hours);form.addRow('检查间隔（分钟）',interval);form.addRow('补充作业链接',manual);form.addRow(quizzes);form.addRow(finished);layout.addLayout(form)
        save=QPushButton('保存设置');save.setObjectName('primary');layout.addWidget(save)
        def apply():
            try:
                self.controller.settings(dict(home_url=home.text().strip(),reminder_hours=[float(v.strip()) for v in hours.text().replace('，',',').split(',')],interval_minutes=interval.value(),manual_urls=[v.strip() for v in manual.toPlainText().splitlines() if v.strip()],include_quizzes=quizzes.isChecked(),include_finished=finished.isChecked()))
                dialog.accept();self.refresh()
            except Exception as exc: QMessageBox.warning(dialog,'设置未保存',str(exc))
        save.clicked.connect(apply);dialog.exec()

    def closeEvent(self,event):
        self.controller.stop()
        self.hide()
        event.accept()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir');parser.add_argument('--smoke-report');parser.add_argument('--preview');parser.add_argument('--startup-report');args=parser.parse_args()
    app=QApplication(sys.argv);app.setQuitOnLastWindowClosed(False);app.setApplicationName(APP_NAME)
    window=Window(Store(args.data_dir));window.show()
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
