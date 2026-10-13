import json
import time
from urllib.parse import urljoin

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
