"""Reader boundary checks; no account or external browser is used."""
from pathlib import Path
from types import SimpleNamespace
import threading
import unittest

from qt_bootstrap import prepare_qt
prepare_qt(Path(__file__).resolve().parents[1] / 'qt-test-data')
from PySide6.QtCore import QObject, Signal, QUrl
from PySide6.QtWidgets import QApplication
from qt_reader import Page, Bridge


class FakeWebPage(QObject):
    loadStarted = Signal()
    loadFinished = Signal(bool)
    renderProcessTerminated = Signal(object, int)
    loadingChanged = Signal(object)

    def url(self):
        return QUrl()

    def setUrl(self, url):
        self.loadStarted.emit()
        self.renderProcessTerminated.emit(None, 18)
        self.loadFinished.emit(False)


class ImmediateBridge:
    def call(self, fn, timeout=15, cancelled=lambda: False):
        from qt_reader import Request
        request = Request(fn)
        fn(request)
        if request.error:
            raise request.error
        return request.result


class QtReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_cancelled_request_does_not_open_a_page(self):
        opened = []
        bridge = Bridge()
        with self.assertRaises(InterruptedError):
            bridge.call(lambda r: (opened.append(True), r.finish(True)), cancelled=lambda: True)
        self.assertEqual(opened, [])

    def test_timed_out_request_is_not_executed_when_gui_resumes(self):
        opened, errors = [], []
        bridge = Bridge()
        def call():
            try:
                bridge.call(lambda r: (opened.append(True), r.finish(True)), timeout=0.05)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=call)
        worker.start()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.app.processEvents()
        self.assertIsInstance(errors[0], TimeoutError)
        self.assertEqual(opened, [])

    def test_cancel_during_gui_call_is_not_reported_as_success(self):
        cancelled = threading.Event()
        bridge = Bridge()
        def open_page(request):
            cancelled.set()
            request.finish(True)
        with self.assertRaises(InterruptedError):
            bridge.call(open_page, cancelled=cancelled.is_set)

    def test_failed_navigation_cannot_report_login_page_ready(self):
        errors = []
        manager = SimpleNamespace(bridge=ImmediateBridge(), on_error=errors.append, record=lambda *args, **kwargs: None)
        context = SimpleNamespace(manager=manager, cancelled=lambda: False)
        page = Page(context, FakeWebPage())
        with self.assertRaisesRegex(RuntimeError, '18'):
            page.goto('https://www.icourse163.org/', wait_until='commit', timeout=100)

    def test_visible_login_keeps_renderer_failure_after_failed_load(self):
        errors = []
        manager = SimpleNamespace(bridge=ImmediateBridge(), on_error=errors.append, record=lambda *args, **kwargs: None)
        context = SimpleNamespace(manager=manager, cancelled=lambda: False)
        page = Page(context, FakeWebPage())
        page.window = SimpleNamespace(set_status=lambda *args: None)
        with self.assertRaises(RuntimeError):
            page.goto('https://www.icourse163.org/', wait_until='commit', timeout=100)
        self.assertIn('18', errors[-1])


if __name__ == '__main__':
    unittest.main()
