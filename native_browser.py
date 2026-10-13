"""Start the installed browser normally, with an app-owned profile and local CDP."""
from pathlib import Path
import json
import os
import socket
import subprocess
import time
from urllib.request import urlopen


def find_browser():
    candidates = [
        Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
        Path(os.environ.get('LOCALAPPDATA', 'C:/Users/DoubleJ/AppData/Local')) / 'Google/Chrome/Application/chrome.exe',
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError('没有找到 Edge 或 Chrome，请先安装一种浏览器。')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class NativeBrowser:
    def __init__(self, folder, cancelled=lambda: False, progress=lambda text: None):
        self.folder = Path(folder).resolve()
        self.cancelled, self.progress = cancelled, progress
        self.process = None
        self.browser = None
        self.log = None
        self.port = None

    def start(self, playwright=None, headless=False, timeout=15, url='about:blank'):
        executable = find_browser()
        self.folder.mkdir(parents=True, exist_ok=True)
        profile = self.folder / 'profile'
        self.port = free_port()
        args = [str(executable), '--user-data-dir=' + str(profile),
                '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=' + str(self.port),
                '--no-first-run', '--no-default-browser-check']
        # Use the browser's normal Windows startup; background scans minimize it.
        # This keeps the normal sandbox and avoids the failing headless startup.
        args += ['--new-window']
        # Establish the browser connection before navigating a rich website.
        # Startup navigation can replace targets while the connection attaches.
        args.append('data:text/html;charset=utf-8,<title>Course Reminders</title><p>Preparing browser...</p>')
        self.log = open(self.folder / 'browser-launch.log', 'wb')
        self.progress('正在准备课程读取…' if headless else '正在打开系统浏览器…')
        startup = None
        if headless and os.name == 'nt':
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 2
        try:
            self.process = subprocess.Popen(args, stdout=self.log, stderr=self.log,
                                            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0), startupinfo=startup)
            endpoint = f'http://127.0.0.1:{self.port}'
            deadline = time.monotonic() + timeout
            ready = False
            while time.monotonic() < deadline:
                if self.cancelled():
                    raise InterruptedError('已取消浏览器启动')
                try:
                    with urlopen(endpoint + '/json/version', timeout=0.5) as response:
                        metadata = json.load(response)
                        ready = bool(metadata.get('webSocketDebuggerUrl'))
                    if ready:
                        break
                except (OSError, ValueError):
                    pass
                if self.process.poll() not in [None, 0]:
                    raise RuntimeError('系统浏览器启动后退出，请关闭旧版登录窗口后重试。')
                time.sleep(0.15)
            if not ready:
                raise TimeoutError('浏览器未在 15 秒内启动。请关闭之前的登录窗口，再重新连接。')
            self.progress('浏览器已打开，正在连接…')
            if playwright is None:
                raise RuntimeError('浏览器读取组件没有初始化')
            self.browser = playwright.chromium.connect_over_cdp(endpoint, timeout=12000)
            self.progress('浏览器连接成功。')
            return self.browser
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
        if self.process and self.process.poll() is None:
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
        if self.log:
            self.log.close()
            self.log = None
