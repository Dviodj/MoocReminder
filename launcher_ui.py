"""Dependency-free startup window. Only the Tk thread touches widgets."""
import argparse
import json
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk


class StartupWorker:
    def __init__(self, workspace, app_args, wait=False, runner=None):
        self.workspace = Path(workspace)
        self.app_args = app_args
        self.wait = wait
        self.runner = runner
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            runner = self.runner
            if runner is None:
                from source_launcher import run_app
                runner = run_app
            def progress(event):
                self.events.put(dict(event, kind=event.get('kind', 'progress')))
            result = runner(self.workspace, self.app_args, wait=self.wait,
                            progress=progress, cancelled=self.cancelled.is_set)
            if self.cancelled.is_set():
                self.events.put({'kind': 'cancelled'})
            elif result:
                self.events.put({'kind': 'error', 'detail': '程序退出，返回代码：%s' % result})
            else:
                self.events.put({'kind': 'success'})
        except InterruptedError:
            self.events.put({'kind': 'cancelled'})
        except Exception as exc:
            self.events.put({'kind': 'error', 'detail': str(exc)})

    def drain(self):
        items = []
        while True:
            try:
                items.append(self.events.get_nowait())
            except queue.Empty:
                return items

    def cancel(self):
        self.cancelled.set()

    def retry(self):
        if self.thread and self.thread.is_alive():
            return
        self.cancelled.clear()
        self.drain()
        self.start()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='MoocReminder startup window')
    parser.add_argument('--workspace', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--smoke-report')
    parser.add_argument('--app-smoke-report')
    parser.add_argument('--wait', action='store_true')
    args, forwarded = parser.parse_known_args(argv)
    if args.app_smoke_report:
        forwarded += ['--smoke-report', args.app_smoke_report]
    return args, forwarded


def preview_runner(workspace, args, wait=False, progress=None, cancelled=None):
    for phase, detail, percent, unknown in [
        ('检查运行环境', '正在确认本机可用的 Python 环境', 12, False),
        ('准备桌面组件', '首次启动时将自动安装所需组件', 42, True),
        ('打开主窗口', '准备课程提醒与本地数据', 88, False)]:
        progress(dict(phase=phase, detail=detail, percent=percent, indeterminate=unknown))
        for _ in range(20):
            if cancelled():
                raise InterruptedError()
            time.sleep(.05)
    return 0


def select_runner(args):
    return preview_runner if args.preview or (args.smoke_report and not args.app_smoke_report) else None


class StartupWindow:
    def __init__(self, root, worker, smoke_report=None):
        self.root, self.worker = root, worker
        self.smoke_report = smoke_report
        self.started = time.monotonic()
        self.ticks = 0
        self.phases = []
        self.closing = False
        self.exit_code = 0
        self.after_ids = set()
        root.bind('<Destroy>', self.on_destroy, add='+')
        self.indeterminate = False
        root.title('MoocReminder · 启动')
        root.configure(bg='#f4f7fb')
        root.resizable(False, False)
        width, height = 540, 360
        root.geometry('%dx%d+%d+%d' % (width, height, (root.winfo_screenwidth()-width)//2, (root.winfo_screenheight()-height)//2))
        icon = worker.workspace / 'assets' / 'MoocReminder.ico'
        if icon.exists():
            try: root.iconbitmap(str(icon))
            except tk.TclError: pass
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('Startup.Horizontal.TProgressbar', background='#397cf2', troughcolor='#e8eef8', borderwidth=0, lightcolor='#397cf2', darkcolor='#397cf2')
        card = tk.Frame(root, bg='white', padx=30, pady=26, highlightthickness=1, highlightbackground='#e5eaf1')
        card.pack(fill='both', expand=True, padx=18, pady=18)
        top = tk.Frame(card, bg='white'); top.pack(fill='x')
        mark = tk.Canvas(top, width=48, height=48, bg='white', highlightthickness=0)
        mark.pack(side='left', padx=(0, 14))
        mark.create_oval(2, 2, 46, 46, fill='#edf4ff', outline='')
        mark.create_text(24, 24, text='M', fill='#397cf2', font=('Segoe UI', 22, 'bold'))
        heading = tk.Frame(top, bg='white'); heading.pack(side='left')
        tk.Label(heading, text='MoocReminder', bg='white', fg='#19283f', font=('Segoe UI', 21, 'bold')).pack(anchor='w')
        try:
            from version import VERSION
        except ImportError:
            VERSION = ''
        tk.Label(heading, text='让每一次截止，都提前一步  ' + VERSION, bg='white', fg='#7b8798', font=('Microsoft YaHei UI', 10)).pack(anchor='w')
        self.phase = tk.StringVar(value='正在启动')
        self.detail = tk.StringVar(value='正在准备启动环境，请稍候…')
        tk.Label(card, textvariable=self.phase, bg='white', fg='#26354c', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', pady=(26, 8))
        self.bar = ttk.Progressbar(card, style='Startup.Horizontal.TProgressbar', maximum=100)
        self.bar.pack(fill='x', ipady=3)
        tk.Label(card, textvariable=self.detail, bg='white', fg='#778498', font=('Microsoft YaHei UI', 10), wraplength=435, justify='left', height=3, anchor='nw').pack(fill='x', pady=(12, 0))
        buttons = tk.Frame(card, bg='white'); buttons.pack(fill='x', side='bottom')
        self.log = tk.Button(buttons, text='查看启动日志', command=self.open_log, relief='flat', bg='white', fg='#397cf2', cursor='hand2')
        self.retry_button = tk.Button(buttons, text='重试', command=self.retry, relief='flat', bg='#edf4ff', fg='#2864c8', padx=20)
        self.cancel_button = tk.Button(buttons, text='取消', command=self.cancel, relief='flat', bg='#f0f3f8', fg='#59677b', padx=20)
        self.cancel_button.pack(side='right')
        root.protocol('WM_DELETE_WINDOW', self.cancel)
        self.schedule(80, self.poll)
        self.schedule(120, self.start_worker)

    def schedule(self, delay, callback):
        timer = None
        def invoke():
            self.after_ids.discard(timer)
            callback()
        timer = self.root.after(delay, invoke)
        self.after_ids.add(timer)

    def on_destroy(self, event):
        if event.widget is self.root:
            self.clear_timers()

    def clear_timers(self):
        for timer in tuple(self.after_ids):
            try:
                self.root.after_cancel(timer)
            except tk.TclError:
                pass
        self.after_ids.clear()

    def start_worker(self):
        if not self.closing:
            self.worker.start()

    def open_log(self):
        path = self.worker.workspace / '.launcher-state' / 'startup.log'
        if path.exists():
            os.startfile(str(path))
        else:
            self.detail.set('启动日志尚未生成。')

    def retry(self):
        self.retry_button.pack_forget(); self.log.pack_forget()
        self.cancel_button.configure(state='normal', text='取消')
        self.phase.set('重新启动'); self.detail.set('正在重新检查运行环境…')
        self.worker.retry()

    def cancel(self):
        self.closing = True
        if self.worker.thread and self.worker.thread.is_alive():
            self.closing = True
            self.worker.cancel()
            self.phase.set('正在取消'); self.detail.set('正在停止本次启动，请稍候…')
            self.cancel_button.configure(state='disabled')
        else:
            self.finish('cancelled')

    def finish(self, result):
        self.exit_code = 1 if result == 'error' else 0
        if self.smoke_report:
            path = Path(self.smoke_report)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(dict(result=result, responsive_ticks=self.ticks,
                phases=self.phases, elapsed_seconds=round(time.monotonic()-self.started, 3),
                window_mapped=bool(self.root.winfo_ismapped())), ensure_ascii=False, indent=2), encoding='utf-8')
        self.clear_timers()
        self.root.destroy()

    def poll(self):
        self.ticks += 1
        for event in self.worker.drain():
            kind = event.get('kind', 'progress')
            if kind in ('success', 'cancelled'):
                self.finish(kind); return
            if kind == 'error':
                if self.smoke_report:
                    self.finish('error'); return
                self.bar.stop(); self.indeterminate = False
                self.phase.set('启动未完成'); self.detail.set(event.get('detail', '请查看日志后重试。'))
                self.retry_button.pack(side='right', padx=8); self.log.pack(side='left')
                self.cancel_button.configure(state='normal', text='关闭')
                if self.closing:
                    self.finish('cancelled'); return
            else:
                self.phase.set(event.get('phase', '正在启动'))
                self.detail.set(event.get('detail', ''))
                self.phases.append(event.get('phase', ''))
                unknown = bool(event.get('indeterminate', False))
                if unknown != self.indeterminate:
                    self.bar.stop(); self.bar.configure(mode='indeterminate' if unknown else 'determinate')
                    if unknown: self.bar.start(15)
                    self.indeterminate = unknown
                if not unknown:
                    self.bar['value'] = max(0, min(100, event.get('percent', 0)))
        self.schedule(80, self.poll)


def main(argv=None):
    args, forwarded = parse_args(argv)
    root = tk.Tk()
    worker = StartupWorker(args.workspace, forwarded, args.wait,
                           runner=select_runner(args))
    window = StartupWindow(root, worker, args.smoke_report)
    root.mainloop()
    return window.exit_code


if __name__ == '__main__':
    raise SystemExit(main())
