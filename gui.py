"""Chinese desktop interface for deadlines and local reminders."""
from copy import deepcopy
import csv
from datetime import datetime, timedelta
import os
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import webbrowser

from core import BEIJING, now_beijing, due_reminders, remaining_text, valid_home_url, normalize_course_url, account_id
from worker import BrowserWorker


class App:
    def __init__(self, root, store, auto_scan=True):
        self.root, self.store = root, store
        self.events = queue.Queue()
        self.worker = BrowserWorker(store.folder, self.events)
        self.worker.start()
        self.busy, self.running, self.closing = False, True, False
        self.next_scan = time.monotonic() + self.interval()
        self.visible_items = {}
        self.status = tk.StringVar(value='首次使用：点击“登录慕课”，登录后点击“检查作业”。')
        self.summary = tk.StringVar()
        self.details = tk.StringVar(value='双击条目打开官网；“请核对”表示页面未明确显示提交状态。')
        self.search = tk.StringVar()
        self.show_past = tk.BooleanVar(value=False)
        self.only_homework = tk.BooleanVar(value=False)
        self._build()
        self.refresh()
        self.root.after(200, self.poll_events)
        self.root.after(1000, self.tick)
        self.root.protocol('WM_DELETE_WINDOW', self.close_window)
        if store.warning:
            self.status.set(store.warning)
        if auto_scan and (store.folder / 'browser-profile').exists():
            self.root.after(1500, lambda: self.request('scan'))

    def interval(self):
        return max(5, int(self.store.data['settings']['interval_minutes'])) * 60

    def _build(self):
        self.root.title('MoocReminder · 作业截止提醒 v0.1')
        self.root.geometry('1180x740')
        self.root.minsize(980, 600)
        self.root.configure(bg='#f3f6fb')
        self.root.option_add('*Font', ('Microsoft YaHei UI', 10))
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TFrame', background='#f3f6fb')
        style.configure('TLabel', background='#f3f6fb', foreground='#223048')
        style.configure('TButton', padding=(12, 8))
        style.configure('Treeview', rowheight=36, font=('Microsoft YaHei UI', 10), background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Microsoft YaHei UI', 10, 'bold'), padding=8)
        frame = ttk.Frame(self.root, padding=24)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='慕课 · 作业截止提醒', font=('Microsoft YaHei UI', 23, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='MOOC 与 SPOC 课程一起检查 · 所有时间均为北京时间', foreground='#64748b').pack(anchor='w', pady=(7, 18))
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill='x')
        self.login_button = ttk.Button(toolbar, text='登录慕课', command=lambda: self.request('login'))
        self.login_button.pack(side='left', padx=(0, 8))
        self.scan_button = ttk.Button(toolbar, text='检查作业', command=lambda: self.request('scan'))
        self.scan_button.pack(side='left', padx=(0, 8))
        self.pause_button = ttk.Button(toolbar, text='暂停监测', command=self.toggle_running)
        self.pause_button.pack(side='left', padx=(0, 8))
        ttk.Button(toolbar, text='设置', command=self.open_settings).pack(side='left', padx=(0, 8))
        ttk.Button(toolbar, text='检查详情', command=self.show_diagnostics).pack(side='right')
        ttk.Label(frame, textvariable=self.summary, font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', pady=(20, 8))
        filters = ttk.Frame(frame)
        filters.pack(fill='x', pady=(0, 12))
        ttk.Label(filters, text='搜索').pack(side='left', padx=(0, 8))
        search = ttk.Entry(filters, textvariable=self.search, width=24)
        search.pack(side='left')
        self.search.trace_add('write', lambda *_: self.refresh())
        ttk.Checkbutton(filters, text='只看作业', variable=self.only_homework, command=self.refresh).pack(side='left', padx=14)
        ttk.Checkbutton(filters, text='显示已截止条目', variable=self.show_past, command=self.refresh).pack(side='left')
        table = ttk.Frame(frame)
        table.pack(fill='both', expand=True)
        columns = ('course', 'name', 'kind', 'deadline', 'remaining', 'status')
        self.tree = ttk.Treeview(table, columns=columns, show='headings', selectmode='browse')
        headings = [('course', '课程', 260), ('name', '作业 / 测验', 270), ('kind', '类型', 55),
                    ('deadline', '提交截止时间', 155), ('remaining', '剩余时间', 110), ('status', '完成状态', 110)]
        for key, title, width in headings:
            self.tree.heading(key, text=title, command=lambda k=key: self.sort_table(k))
            self.tree.column(key, width=width, minwidth=50, stretch=key in ['course', 'name'])
        scrollbar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scrollbar.pack(side='right', fill='y')
        for tag, color in [('urgent', '#fee2e2'), ('soon', '#fff4d6'), ('complete', '#eef2f6'), ('past', '#f1f5f9')]:
            self.tree.tag_configure(tag, background=color)
        self.tree.bind('<Double-1>', lambda _: self.open_selected())
        self.tree.bind('<<TreeviewSelect>>', lambda _: self.selected_detail())
        ttk.Label(frame, textvariable=self.details, wraplength=1080, foreground='#64748b').pack(anchor='w', pady=(12, 10))
        actions = ttk.Frame(frame)
        actions.pack(fill='x')
        ttk.Button(actions, text='打开所选作业页', command=self.open_selected).pack(side='left', padx=(0, 8))
        ttk.Button(actions, text='标记已完成 / 恢复提醒', command=self.toggle_completed).pack(side='left', padx=(0, 8))
        ttk.Button(actions, text='导出 CSV', command=self.export_csv).pack(side='left')
        ttk.Button(actions, text='测试弹窗', command=lambda: self.show_alert([], test=True)).pack(side='right')
        ttk.Label(frame, textvariable=self.status, wraplength=1080, foreground='#2563eb').pack(anchor='w', pady=(18, 6))
        ttk.Label(frame, text='保持程序运行即可自动检查和弹窗。最小化后仍运行；关闭程序或电脑关机后停止提醒。', foreground='#64748b').pack(anchor='w')

    def request(self, command):
        if self.busy or self.closing:
            return
        self.busy = True
        self.login_button.configure(state='disabled')
        self.scan_button.configure(state='disabled')
        self.status.set('正在打开登录浏览器…' if command == 'login' else '正在检查课程与作业…')
        self.worker.commands.put((command, deepcopy(self.store.data['settings'])))

    def finish_busy(self):
        self.busy = False
        self.login_button.configure(state='normal')
        self.scan_button.configure(state='normal')
        self.next_scan = time.monotonic() + self.interval()

    def poll_events(self):
        if self.closing:
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'progress':
                    self.status.set(value)
                elif kind == 'login_ready':
                    self.finish_busy()
                    self.status.set('请在浏览器中完成登录，再返回这里点击“检查作业”。检查时登录窗口会自动关闭。')
                elif kind == 'result':
                    self.consume_result(value)
                elif kind == 'error':
                    self.finish_busy()
                    self.store.data['errors'] = [value]
                    self.status.set('检查失败：' + value.splitlines()[0])
        except queue.Empty:
            pass
        except Exception as exc:
            self.finish_busy()
            self.status.set('保存检查结果失败：' + str(exc))
        self.root.after(200, self.poll_events)

    def consume_result(self, result):
        if result.account_id and result.account_id != account_id(self.store.data['settings']['home_url']):
            self.finish_busy()
            self.status.set('账号已切换，已忽略旧账号的检查结果。请重新登录并检查作业。')
            return
        self.store.merge_scan(result.items, result.successful_urls)
        existing = {c['url']: c for c in self.store.data['courses']}
        existing.update({c.url: dict(name=c.name, url=c.url) for c in result.courses})
        self.store.data['courses'] = list(existing.values())
        self.store.data['last_scan'] = now_beijing().isoformat()
        self.store.data['errors'] = result.errors
        self.store.data['successful_urls'] = result.successful_urls
        self.store.save()
        self.finish_busy()
        self.refresh()
        stamp = now_beijing().strftime('%H:%M')
        if result.errors:
            self.status.set(f'{stamp}：{len(result.successful_urls)} 门课程检查成功，{len(result.errors)} 项失败；失败课程保留上次数据。点击“检查详情”查看。')
        else:
            self.status.set(f'{stamp}：已检查 {len(result.successful_urls)} 门课程，读取 {len(result.items)} 项作业和测验。')
        self.check_reminders()

    def refresh(self):
        now = now_beijing()
        all_items = self.store.items()
        completed = set(self.store.data['completed'])
        active = [i for i in all_items if i.deadline and i.deadline > now and i.status != 'completed' and i.revision not in completed]
        soon = sum(i.deadline <= now + timedelta(hours=72) for i in active)
        self.summary.set(f'{len(active)} 项待核对 / 待完成    ·    {soon} 项将在 3 天内截止    ·    {len(self.store.data["courses"])} 门已读取课程')
        selection = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        self.visible_items = {}
        query = self.search.get().strip().lower()
        ordered = sorted(all_items, key=lambda i: (i.deadline or datetime.max.replace(tzinfo=BEIJING), i.course, i.name))
        for item in ordered:
            if not self.show_past.get() and item.deadline and item.deadline <= now:
                continue
            if self.only_homework.get() and item.kind != '作业':
                continue
            if query and query not in (item.course + ' ' + item.name).lower():
                continue
            is_complete = item.status == 'completed' or item.revision in completed
            status = '本地已完成' if item.revision in completed else {'unknown': '请核对', 'pending': '未提交', 'completed': '已提交'}[item.status]
            hours = (item.deadline - now).total_seconds() / 3600 if item.deadline else None
            tag = 'complete' if is_complete else 'past' if hours is not None and hours <= 0 else 'urgent' if hours is not None and hours <= 24 else 'soon' if hours is not None and hours <= 72 else ''
            deadline = item.deadline.strftime('%Y-%m-%d %H:%M') if item.deadline else (item.deadline_text or '待核对')
            self.tree.insert('', 'end', iid=item.uid, values=(item.course, item.name, item.kind, deadline, remaining_text(item, now), status), tags=(tag,))
            self.visible_items[item.uid] = item
        if selection and selection[0] in self.visible_items:
            self.tree.selection_set(selection[0])

    def sort_table(self, column):
        rows = [(self.tree.set(key, column), key) for key in self.tree.get_children()]
        for index, (_, key) in enumerate(sorted(rows)):
            self.tree.move(key, '', index)

    def selected(self):
        keys = self.tree.selection()
        return self.visible_items.get(keys[0]) if keys else None

    def selected_detail(self):
        item = self.selected()
        if item:
            checked = datetime.fromisoformat(item.checked_at).strftime('%m-%d %H:%M')
            self.details.set(f'{item.course} · {item.name}    上次成功读取：{checked}。请在官网确认提交状态；完成后可在本地停止提醒。')

    def open_selected(self):
        item = self.selected()
        if item:
            webbrowser.open(item.course_url)

    def toggle_completed(self):
        item = self.selected()
        if item:
            self.store.mark_completed(item, item.revision not in self.store.data['completed'])
            self.refresh()

    def toggle_running(self):
        self.running = not self.running
        self.pause_button.configure(text='恢复监测' if not self.running else '暂停监测')
        self.status.set('自动检查和提醒已暂停。' if not self.running else '自动检查和提醒已恢复。')
        if self.running:
            self.next_scan = time.monotonic()
            self.check_reminders()

    def check_reminders(self):
        if not self.running or self.closing:
            return
        state = deepcopy(self.store.data['reminders'])
        alerts = due_reminders(self.store.active_items(), self.store.data['settings']['reminder_hours'], state, now_beijing())
        if alerts:
            self.show_alert(alerts)
            self.store.data['reminders'] = state
            self.store.save()

    def show_alert(self, alerts, test=False):
        window = tk.Toplevel(self.root)
        window.title('作业截止提醒')
        window.geometry('650x380')
        window.attributes('-topmost', True)
        panel = ttk.Frame(window, padding=24)
        panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='作业即将截止' if not test else '提醒弹窗测试成功', font=('Microsoft YaHei UI', 18, 'bold')).pack(anchor='w', pady=(0, 12))
        box = tk.Text(panel, wrap='word', height=9, relief='flat', bg='#ffffff')
        box.pack(fill='both', expand=True)
        if test:
            box.insert('end', '这是测试通知。实际提醒会显示课程、作业名称、截止时间和剩余时间。\n\n最小化程序后仍可弹窗，关闭程序后停止提醒。')
        else:
            for item, _ in alerts:
                box.insert('end', f'{item.course} · {item.name}\n截止：{item.deadline:%m月%d日 %H:%M}    剩余：{remaining_text(item, now_beijing())}\n\n')
        box.configure(state='disabled')
        if alerts:
            ttk.Button(panel, text='打开第一项作业页', command=lambda: webbrowser.open(alerts[0][0].course_url)).pack(side='left', pady=(12, 0))
        ttk.Button(panel, text='知道了', command=window.destroy).pack(side='right', pady=(12, 0))
        window.bell()
        window.lift()

    def tick(self):
        if self.closing:
            return
        self.refresh()
        self.check_reminders()
        if self.running and not self.busy and time.monotonic() >= self.next_scan:
            self.request('scan')
        self.root.after(60000, self.tick)

    def open_settings(self):
        window = tk.Toplevel(self.root)
        window.title('设置')
        window.geometry('800x530')
        window.transient(self.root)
        panel = ttk.Frame(window, padding=24)
        panel.pack(fill='both', expand=True)
        settings = self.store.data['settings']
        home = tk.StringVar(value=settings['home_url'])
        interval = tk.StringVar(value=str(settings['interval_minutes']))
        hours = tk.StringVar(value=', '.join(str(h) for h in settings['reminder_hours']))
        quizzes = tk.BooleanVar(value=settings['include_quizzes'])
        finished = tk.BooleanVar(value=settings['include_finished'])
        ttk.Label(panel, text='课程主页链接').pack(anchor='w')
        ttk.Entry(panel, textvariable=home).pack(fill='x', pady=(5, 12))
        row = ttk.Frame(panel)
        row.pack(fill='x', pady=8)
        ttk.Label(row, text='检查间隔（分钟，至少 5）').pack(side='left')
        ttk.Entry(row, textvariable=interval, width=8).pack(side='left', padx=10)
        ttk.Label(panel, text='提前多少小时提醒（逗号分隔，例如 72,24,3）').pack(anchor='w', pady=(8, 3))
        ttk.Entry(panel, textvariable=hours).pack(fill='x', pady=(0, 10))
        ttk.Label(panel, text='手动作业页链接（每行一条，可作为自动发现的补充）').pack(anchor='w')
        links = tk.Text(panel, height=6, wrap='word')
        links.pack(fill='x', pady=(5, 10))
        links.insert('1.0', '\n'.join(settings['manual_urls']))
        ttk.Checkbutton(panel, text='对测验也进行截止提醒', variable=quizzes).pack(anchor='w')
        ttk.Checkbutton(panel, text='检查已结束的课程学期', variable=finished).pack(anchor='w')
        ttk.Label(panel, text='设置与登录状态保存在本机；点击“登录慕课”可以切换账号。', foreground='#64748b').pack(anchor='w', pady=10)

        def save():
            try:
                if not valid_home_url(home.get().strip()):
                    raise ValueError('请填写官网个人课程主页链接，包含 userId。')
                minutes = int(interval.get())
                if not 5 <= minutes <= 1440:
                    raise ValueError('检查间隔须为 5 到 1440 分钟。')
                windows = sorted({float(v.strip()) for v in hours.get().replace('，', ',').split(',')}, reverse=True)
                if not windows or any(not 0 < h <= 8760 for h in windows):
                    raise ValueError('提醒时间须为大于 0、不超过 8760 的小时数。')
                urls = [v.strip() for v in links.get('1.0', 'end').splitlines() if v.strip()]
                if any(not normalize_course_url(v) for v in urls):
                    raise ValueError('作业链接须来自慕课官网，包含课程和 tid 学期编号。')
                changed_account = self.store.update_settings(dict(home_url=home.get().strip(), manual_urls=urls,
                    interval_minutes=minutes, reminder_hours=windows, include_quizzes=quizzes.get(), include_finished=finished.get()))
                self.next_scan = time.monotonic() + self.interval()
                window.destroy()
                self.refresh()
                self.status.set('账号已切换，旧作业记录已清除。请重新登录再检查。' if changed_account else '设置已保存。新的检查间隔和提醒时间立即生效。')
            except (ValueError, OSError) as exc:
                messagebox.showerror('设置未保存', str(exc), parent=window)

        ttk.Button(panel, text='保存设置', command=save).pack(side='right')

    def show_diagnostics(self):
        window = tk.Toplevel(self.root)
        window.title('检查详情')
        window.geometry('850x500')
        box = tk.Text(window, wrap='word', padx=16, pady=16)
        box.pack(fill='both', expand=True)
        box.insert('end', '最近检查：' + (self.store.data['last_scan'] or '尚未检查') + '\n\n')
        for course in self.store.data['courses']:
            success = course['url'] in self.store.data['successful_urls']
            box.insert('end', ('本次成功' if success else '上次缓存') + ' · ' + course['name'] + '\n' + course['url'] + '\n\n')
        errors = self.store.data['errors']
        box.insert('end', '错误 / 提示：\n' + ('\n'.join(errors) if errors else '没有错误记录。') + '\n\n本地数据目录：\n' + str(self.store.folder))
        box.configure(state='disabled')

    def export_csv(self):
        path = filedialog.asksaveasfilename(parent=self.root, title='导出作业截止时间', defaultextension='.csv',
            initialfile='作业截止时间.csv', filetypes=[('CSV 文件', '*.csv')])
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['课程', '作业名称', '类型', '截止时间（北京时间）', '状态', '官网链接', '上次读取时间'])
                for item in self.store.items():
                    status = '本地已完成' if item.revision in self.store.data['completed'] else item.status
                    values = [item.course, item.name, item.kind, item.deadline.isoformat() if item.deadline else item.deadline_text, status, item.course_url, item.checked_at]
                    writer.writerow(["'" + v if isinstance(v, str) and v[:1] in '=+-@' else v for v in values])
            self.status.set('已导出：' + path)
        except OSError as exc:
            messagebox.showerror('导出失败', str(exc), parent=self.root)

    def shutdown(self):
        self.closing = True
        self.worker.stop()

    def close_window(self):
        if self.closing:
            return
        self.shutdown()
        self.status.set('正在关闭浏览器，稍候…')
        self.login_button.configure(state='disabled')
        self.scan_button.configure(state='disabled')

        def finish():
            if self.worker.is_alive():
                self.root.after(200, finish)
            else:
                self.root.destroy()

        finish()
