"""App-owned web pages, accessed asynchronously on Qt's GUI thread."""
from dataclasses import dataclass, field
import fnmatch
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
import threading
import time
from urllib.parse import urljoin

from PySide6.QtCore import QObject, Signal, Slot, QUrl, qVersion
from PySide6.QtWidgets import QMainWindow, QLineEdit, QVBoxLayout, QWidget, QLabel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineLoadingInfo
from PySide6.QtWebEngineWidgets import QWebEngineView


def windows_job_info():
    if os.name != 'nt':
        return {}
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    result = wintypes.BOOL()
    ok = kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(result))
    return dict(in_windows_job=bool(result.value), job_query_ok=bool(ok))


@dataclass
class Request:
    fn: object
    cancelled: object = lambda: False
    event: threading.Event = field(default_factory=threading.Event)
    abandoned: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: Exception | None = None

    def finish(self, value=None, error=None):
        self.result, self.error = value, error
        self.event.set()


class Bridge(QObject):
    requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.requested.connect(self.execute)

    @Slot(object)
    def execute(self, request):
        if request.abandoned.is_set() or request.cancelled():
            return request.finish(error=InterruptedError('当前操作已取消'))
        try:
            request.fn(request)
        except Exception as exc:
            request.finish(error=exc)

    def call(self, fn, timeout=15, cancelled=lambda: False):
        if cancelled():
            raise InterruptedError('当前操作已取消')
        request = Request(fn, cancelled=cancelled)
        self.requested.emit(request)
        end = time.monotonic() + timeout
        while not request.event.wait(0.1):
            if cancelled():
                request.abandoned.set()
                raise InterruptedError('当前操作已取消')
            if time.monotonic() >= end:
                request.abandoned.set()
                raise TimeoutError('网页读取超时，请重试；界面可以继续使用。')
        if cancelled():
            request.abandoned.set()
            raise InterruptedError('当前操作已取消')
        if request.error:
            raise request.error
        return request.result


class LoginWindow(QMainWindow):
    def __init__(self, page, parent=None):
        super().__init__(parent)
        self.setWindowTitle('登录中国大学 MOOC · MoocReminder')
        self.resize(1100, 800)
        self.setStyleSheet('QMainWindow{background:#f5f7fb}QLineEdit{background:#fff;border:1px solid #e1e6ef;border-radius:7px;padding:10px;color:#63728a}')
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        self.address.setPlaceholderText('正在打开慕课官网…')
        self.view = QWebEngineView()
        self.view.setPage(page)
        self.status = QLabel('正在打开慕课官网…')
        self.status.setWordWrap(True)
        self.set_status('正在打开慕课官网…')
        layout.addWidget(self.address)
        layout.addWidget(self.status)
        layout.addWidget(self.view)
        self.setCentralWidget(panel)
        page.urlChanged.connect(lambda url: self.address.setText(url.toString()))

    def set_status(self, message, failed=False):
        self.status.setText(message)
        self.status.setStyleSheet('padding:12px;border-radius:7px;background:'+('#fff1e9;color:#9c5739' if failed else '#edf3ff;color:#627ca5'))


class Manager(QObject):
    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.folder = Path(folder)
        self.bridge = Bridge(self)
        self.contexts = {}
        self.on_error = lambda message: None
        self.session = uuid.uuid4().hex
        self.record('manager_started', qt=qVersion(), **windows_job_info())

    def record(self, event, **data):
        path = Path(__file__).parent / '.launcher-state' / 'connection.log'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(time=datetime.now().isoformat(), session=self.session,
                    event=event, **data), ensure_ascii=False)+'\n')
        except OSError:
            pass

    def context(self, user_id, visible, cancelled):
        def create(request):
            if user_id not in self.contexts:
                profile = QWebEngineProfile('course-' + user_id, self)
                path = self.folder / 'web-profiles' / user_id
                path.mkdir(parents=True, exist_ok=True)
                profile.setPersistentStoragePath(str(path / 'storage'))
                profile.setCachePath(str(path / 'cache'))
                profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
                self.contexts[user_id] = Context(self, profile, cancelled)
            context = self.contexts[user_id]
            context.cancelled = cancelled
            context.visible = visible
            request.finish(context)
        return self.bridge.call(create, cancelled=cancelled)


class Context:
    def __init__(self, manager, profile, cancelled):
        self.manager, self.profile, self.cancelled = manager, profile, cancelled
        self.visible = False
        self.owned = []
        self.windows = []

    def set_default_timeout(self, value):
        pass

    @property
    def pages(self):
        return list(self.owned)

    def new_page(self):
        def create(request):
            page = QWebEnginePage(self.profile, self.manager)
            proxy = Page(self, page)
            self.owned.append(proxy)
            if self.visible:
                window = LoginWindow(page)
                self.windows.append(window)
                proxy.window = window
                window.show()
                window.raise_()
                window.activateWindow()
                self.manager.record('login_window_shown', visible=window.isVisible())
            request.finish(proxy)
        return self.manager.bridge.call(create, cancelled=self.cancelled)

    def close(self):
        # Login pages share the same profile with background reader pages.
        # The manager owns the profile until the app exits.
        pass


class Page:
    def __init__(self, context, page):
        self.context, self.page = context, page
        self.window = None
        self.closed = False
        self.failure = ''
        self.load_done = threading.Event()
        self.load_ok = True
        page.loadStarted.connect(self._started)
        page.loadFinished.connect(self._loaded)
        page.loadingChanged.connect(self._loading_changed)
        page.renderProcessTerminated.connect(self._terminated)

    def _started(self):
        self.failure = ''
        self.load_done.clear()
        if self.window:
            self.window.set_status('正在打开慕课官网…')

    def _loading_changed(self, info):
        # Record public navigation/error metadata; never record query strings,
        # fragments, response headers, cookies or credential forms.
        url = info.url()
        reason = re.search(r'(?:net::)?ERR_[A-Z_]+', info.errorString())
        reason = reason.group(0) if reason else ''
        self.context.manager.record('navigation', host=url.host(), path=url.path(),
            status=info.status().name, domain=info.errorDomain().name, code=info.errorCode(), reason=reason)
        if info.status() == QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus:
            if not self.failure:
                self.failure = f'官网加载失败（{info.errorDomain().name}，{info.errorCode()}，{reason}）。'
            if self.window:
                self.window.set_status(self.failure+' 具体原因已记录在“连接详情”。', True)

    def _loaded(self, ok):
        self.load_ok = ok
        self.load_done.set()
        self.context.manager.record('load_finished', ok=bool(ok))
        if self.window and ok:
            self.window.set_status('官网页面已加载。完成登录后，返回主窗口检查作业。')
        if not ok and self.window and not self.context.cancelled():
            message = self.failure or '登录网页没有完成加载，请关闭窗口后重新连接。'
            self.window.set_status(message+' 具体原因已记录在“连接详情”。', True)
            self.context.manager.on_error(message)

    def _terminated(self, status, code):
        self.failure = f'内置网页渲染进程退出（代码 {code}）。请关闭后重新连接，并在连接详情中查看诊断。'
        self.load_ok = False
        self.load_done.set()
        self.context.manager.record('renderer_terminated', status=str(status), code=code)
        if self.window:
            self.window.set_status(self.failure, True)
        if not self.context.cancelled():
            self.context.manager.on_error(self.failure)

    def _call(self, fn, timeout=15):
        return self.context.manager.bridge.call(fn, timeout, self.context.cancelled)

    @property
    def url(self):
        return self._call(lambda request: request.finish(self.page.url().toString()))

    def evaluate_expression(self, expression):
        def run(request):
            if self.failure:
                return request.finish(error=RuntimeError(self.failure))
            def completed(value):
                if not isinstance(value, str):
                    return request.finish(error=RuntimeError(self.failure or '网页尚未准备好，请稍后重试。'))
                try:
                    request.finish(json.loads(value))
                except ValueError as exc:
                    request.finish(error=exc)
            self.page.runJavaScript('JSON.stringify((()=>{' + 'return (' + expression + ');})())',
                completed)
        return self._call(run)

    def evaluate(self, script):
        return self.evaluate_expression('(' + script + ')()')

    def goto(self, url, wait_until='domcontentloaded', timeout=30000):
        # A fresh document prevents stale SPA category cards being accepted.
        current = self.url
        if current and current != 'about:blank' and url != 'about:blank':
            self.goto('about:blank', timeout=5000)
        self.load_done.clear()
        self._call(lambda request: (self.page.setUrl(QUrl(url)), request.finish(True)))
        # setUrl only queues navigation. Qt has no separate commit signal;
        # require a successful load before telling the user the page is ready.
        self.wait_for_load_state(wait_until, timeout)

    def wait_for_load_state(self, state='networkidle', timeout=10000):
        end = time.monotonic() + timeout / 1000
        while not self.load_done.wait(0.1):
            if self.context.cancelled():
                raise InterruptedError('当前操作已取消')
            if time.monotonic() >= end:
                raise TimeoutError('网页加载超时，已保留上次数据。')
        if not self.load_ok:
            raise RuntimeError(self.failure or '内置网页未能加载，请关闭登录窗口后重新连接。')
        if state == 'networkidle':
            self.wait_for_timeout(1000)

    def wait_for_timeout(self, milliseconds):
        end = time.monotonic() + milliseconds / 1000
        while time.monotonic() < end:
            if self.context.cancelled():
                raise InterruptedError('当前操作已取消')
            time.sleep(min(0.1, max(0, end - time.monotonic())))

    def wait_for_url(self, pattern, timeout=20000):
        end = time.monotonic() + timeout / 1000
        while time.monotonic() < end:
            url = self.url
            if (hasattr(pattern, 'search') and pattern.search(url)) or (isinstance(pattern, str) and fnmatch.fnmatch(url, pattern)):
                return
            self.wait_for_timeout(150)
        raise TimeoutError('未能读取课程学习链接。')

    def locator(self, selector):
        return Locator(self, 'Array.from(document.querySelectorAll(' + json.dumps(selector) + '))')

    def get_by_text(self, text, exact=True):
        return Locator(self, 'Array.from(document.querySelectorAll("a,button,[role=button]")).filter(e=>e.innerText.trim()===' + json.dumps(text) + ')')

    def expect_popup(self, timeout=15000):
        return Popup(self, timeout)

    def bring_to_front(self):
        if self.window:
            self._call(lambda request: (self.window.show(), self.window.raise_(), self.window.activateWindow(), request.finish(True)))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.context.owned = [page for page in self.context.owned if page is not self]
        try:
            self.context.manager.bridge.call(lambda request: (self.page.triggerAction(QWebEnginePage.WebAction.Stop), self.page.deleteLater(), request.finish(True)), timeout=3)
        except Exception:
            pass


class Popup:
    def __init__(self, page, timeout):
        self.page, self.timeout = page, timeout
        self.value = None

    def __enter__(self):
        self.page.evaluate_expression('(()=>{window.__courseOpenBackup=window.open;window.__courseOpenUrl="";window.open=function(url){window.__courseOpenUrl=String(url);return null;};return true;})()')
        return self

    def __exit__(self, exc_type, value, tb):
        try:
            if exc_type:
                return False
            end = time.monotonic() + self.timeout / 1000
            while time.monotonic() < end:
                url = self.page.evaluate_expression('window.__courseOpenUrl || ""')
                if url:
                    self.value = self.page.context.new_page()
                    self.value.goto(urljoin(self.page.url, url))
                    return False
                self.page.wait_for_timeout(150)
            raise TimeoutError('课程入口没有提供学习链接，请补充该课程的作业页链接。')
        finally:
            try:
                self.page.evaluate_expression('(()=>{window.open=window.__courseOpenBackup;delete window.__courseOpenBackup;delete window.__courseOpenUrl;return true;})()')
            except Exception:
                pass


class Locator:
    def __init__(self, page, expression):
        self.page, self.expression = page, expression
    @property
    def first(self):
        return self.nth(0)
    def nth(self, n):
        return Locator(self.page, '(' + self.expression + ').slice(' + str(n) + ',' + str(n + 1) + ')')
    def locator(self, selector):
        return Locator(self.page, '(' + self.expression + ').flatMap(e=>Array.from(e.querySelectorAll(' + json.dumps(selector) + ')))')
    def count(self):
        return self.page.evaluate_expression('(' + self.expression + ').length') or 0
    def inner_text(self):
        return self.page.evaluate_expression('(' + self.expression + ')[0]?.innerText || ""') or ''
    def get_attribute(self, name):
        return self.page.evaluate_expression('(' + self.expression + ')[0]?.getAttribute(' + json.dumps(name) + ') ?? null')
    def is_visible(self):
        return bool(self.page.evaluate_expression('!!(' + self.expression + ')[0]?.getClientRects().length'))
    def click(self):
        self.page.evaluate_expression('(()=>{const e=(' + self.expression + ')[0];if(!e)throw new Error("没有找到课程入口");e.click();return true;})()')
    def wait_for(self, state='attached', timeout=20000):
        end = time.monotonic() + timeout / 1000
        while time.monotonic() < end:
            if self.count() and (state != 'visible' or self.is_visible()):
                return
            self.page.wait_for_timeout(150)
        raise TimeoutError('课程页面没有完成加载。')
