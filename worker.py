"""Serial browser worker; UI communication goes through queues."""
import queue
import threading

from browser import MoocBrowser


class BrowserWorker(threading.Thread):
    def __init__(self, folder, events, context_provider=None):
        super().__init__(name='mooc-browser', daemon=True)
        self.events = events
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.cancel_event = threading.Event()
        self.browser = MoocBrowser(folder, lambda text: events.put(('progress', text)), cancelled=lambda: self.stop_event.is_set() or self.cancel_event.is_set(), context_provider=context_provider)

    def run(self):
        try:
            while not self.stop_event.is_set():
                try:
                    command, settings = self.commands.get(timeout=0.3)
                except queue.Empty:
                    continue
                if command == 'stop':
                    break
                self.cancel_event.clear()
                try:
                    if command == 'login':
                        self.browser.login(settings['home_url'])
                        self.events.put(('login_ready', None))
                    elif command == 'scan':
                        result = self.browser.scan(settings)
                        self.events.put(('result', result))
                except Exception as exc:
                    self.events.put(('error', str(exc)))
        finally:
            try:
                self.browser.close()
            except Exception:
                pass

    def stop(self):
        self.stop_event.set()
        self.cancel_event.set()
        self.commands.put(('stop', None))

    def cancel(self):
        self.cancel_event.set()
