"""Site-script coverage on the browser the program actually ships.

These run the real ASSIGNMENTS/HOME_CARDS scripts against real rendered pages in
QtWebEngine, which is what reads the MOOC site at runtime. They need a desktop
session, not network access.
"""

import _bootstrap  # noqa: F401  (puts src/ on sys.path)
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qt_bootstrap import prepare_qt
prepare_qt(ROOT / 'qt-test-data')

from PySide6.QtCore import QEventLoop, QStandardPaths, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from site_scripts import ASSIGNMENTS_SCRIPT, HOME_CARDS_SCRIPT, CATEGORIES_SCRIPT, classify_assignment_page

# QtWebEngine creates a data folder per profile under the standard data
# location. Redirect those paths into the workspace so a restricted %APPDATA%
# cannot stop the browser from starting.
QStandardPaths.setTestModeEnabled(True)


def wait_until(predicate, timeout=15.0):
    """Run the Qt event loop until predicate() is true or the timeout expires."""
    loop = QEventLoop()
    outcome = {'done': False}
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    deadline.start(int(timeout * 1000))
    poll = QTimer()
    poll.setInterval(50)

    def check():
        if predicate():
            outcome['done'] = True
            loop.quit()

    poll.timeout.connect(check)
    poll.start()
    loop.exec()
    poll.stop()
    deadline.stop()
    return outcome['done']


class SiteScriptTests(unittest.TestCase):
    """The scripts must agree with the page shapes the site serves."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # The default profile avoids Chromium creating a named profile folder
        # under %APPDATA%, which a restricted environment may refuse.
        cls.profile = QWebEngineProfile.defaultProfile()
        storage = ROOT / '.build/temp/site-test-profile'
        (storage / 'storage').mkdir(parents=True, exist_ok=True)
        (storage / 'cache').mkdir(parents=True, exist_ok=True)
        cls.profile.setPersistentStoragePath(str(storage / 'storage'))
        cls.profile.setCachePath(str(storage / 'cache'))
        cls.page = QWebEnginePage(cls.profile)
        cls.loaded = False

        def on_load(ok):
            cls.loaded = bool(ok)

        cls.page.loadFinished.connect(on_load)

    def setUp(self):
        SiteScriptTests.loaded = False

    def render(self, html):
        self.page.setHtml(html)
        self.assertTrue(wait_until(lambda: SiteScriptTests.loaded), '页面未能在限定时间内渲染完成')

    def evaluate(self, script):
        box = {}
        self.page.runJavaScript('JSON.stringify((' + script + ')())',
                                lambda value: box.update(value=value))
        self.assertTrue(wait_until(lambda: 'value' in box), '脚本未在限定时间内返回')
        import json
        return json.loads(box['value'])

    def test_reads_submission_deadline_and_ignores_peer_review(self):
        self.render('''<div id="courseLearn-inner-box"><h2>测验与作业</h2>
          <div class="j-empty">老师还没有发布测试和作业，请耐心等待</div>
          <div class="m-chapterQuizHwItem"><div class="j-titleName">第二章</div>
            <div class="u-quizHwListItem"><div class="titleBox"><h4 class="j-name">第2章作业</h4>
              <div class="j-submitTime">截止时间：2026/10/18 23:30</div><a class="j-quizBtn">前往作业</a></div>
              <div class="detail">提交作业后不参与互评将影响成绩<div class="j-evalEnd">截止时间：2026/10/25 23:30</div></div>
            </div>
            <div class="u-quizHwListItem"><div class="titleBox"><h4 class="j-name">第四周 振动单元测试</h4>
              <div class="j-submitTime">截止时间：2026/10/18 23:30</div><a class="j-quizBtn">前往测验</a></div>
              <div class="j-submitCount">0/3</div></div></div></div>''')
        data = self.evaluate(ASSIGNMENTS_SCRIPT)
        self.assertEqual(classify_assignment_page(data), 'loaded')
        self.assertEqual(len(data['rows']), 2)
        self.assertEqual(data['rows'][0]['deadline'], '截止时间：2026/10/18 23:30')
        self.assertEqual(data['rows'][1]['status'], '未提交')
        self.assertEqual(data['rows'][1]['kind'], '测验')

    def test_only_an_explicit_empty_list_counts_as_empty(self):
        self.render('<div id="courseLearn-inner-box"><h2>测验与作业</h2>'
                    '<div class="j-empty">老师还没有发布测试和作业，请耐心等待</div></div>')
        self.assertEqual(classify_assignment_page(self.evaluate(ASSIGNMENTS_SCRIPT)), 'empty')
        self.render('<!doctype html><html><body><div id="courseLearn-inner-box"><h2>测验与作业</h2></div></body></html>')
        self.assertEqual(classify_assignment_page(self.evaluate(ASSIGNMENTS_SCRIPT)), 'error')
        self.render('<!doctype html><html><body><div>课程介绍 登录 | 注册</div></body></html>')
        self.assertEqual(classify_assignment_page(self.evaluate(ASSIGNMENTS_SCRIPT)), 'login_required')

    def test_a_malformed_row_is_not_silently_dropped(self):
        self.render('<div id="courseLearn-inner-box"><h2>测验与作业</h2>'
                    '<div class="u-quizHwListItem"><div class="titleBox"><h4 class="j-name">作业</h4></div></div></div>')
        self.assertEqual(classify_assignment_page(self.evaluate(ASSIGNMENTS_SCRIPT)), 'error')

    def test_course_cards_keep_the_finished_flag(self):
        self.render('''<div class="j-course-card-box"><div class="title"><span class="text">概率论与数理统计</span></div>
          <div class="course-status">2027年1月15日学期结束</div></div>
          <div class="j-course-card-box"><div class="title"><span class="text">旧课</span></div>
          <div class="course-status">2026年7月14日已结束</div></div>''')
        data = self.evaluate(HOME_CARDS_SCRIPT)
        self.assertEqual([row['name'] for row in data], ['概率论与数理统计', '旧课'])
        self.assertEqual([row['finished'] for row in data], [False, True])

    def test_categories_come_from_the_home_links(self):
        self.render('''<a href="#/home/course">MOOC课程</a><a href="#/home/spocCourse">SPOC课程</a>
          <a href="#/home/other">其它</a>''')
        self.assertEqual(self.evaluate(CATEGORIES_SCRIPT), ['#/home/course', '#/home/spocCourse'])


class SettleTests(unittest.TestCase):
    """`_settle` must not accept 'still loading' as an empty result.

    This is the guard that stops the reader from reporting "no assignments" for
    a course whose list simply had not rendered yet. It is independent of which
    browser renders the page, so it runs against a scripted page.
    """

    def setUp(self):
        from browser import MoocBrowser
        self.reader = MoocBrowser(tempfile.mkdtemp())

    def test_row_appearing_late_is_waited_for(self):
        from site_scripts import ASSIGNMENTS_SCRIPT, classify_assignment_page
        empty = dict(rows=[], root=True, module='测验与作业', empty=True, login=False, courseName='课程')
        filled = dict(empty, rows=[dict(name='源课程作业', deadline='2026/10/18 23:30', chapter='',
                                        status='', kind='作业')], empty=False)
        page = ScriptedPage([empty, empty, filled, filled, filled, filled, filled, filled])
        data = self.reader._settle(page, ASSIGNMENTS_SCRIPT,
                                   lambda value: classify_assignment_page(value) != 'error', timeout=5)
        self.assertEqual([row['name'] for row in data['rows']], ['源课程作业'])

    def test_a_page_that_never_settles_reports_an_error(self):
        from site_scripts import ASSIGNMENTS_SCRIPT, classify_assignment_page
        empty = dict(rows=[], root=True, module='测验与作业', empty=True, login=False, courseName='课程')
        page = ScriptedPage(lambda: dict(empty))
        with self.assertRaises(RuntimeError):
            self.reader._settle(page, ASSIGNMENTS_SCRIPT,
                                lambda value: classify_assignment_page(value) != 'error', timeout=2)

    def test_cancel_stops_the_wait(self):
        from site_scripts import ASSIGNMENTS_SCRIPT, classify_assignment_page
        reader = type(self.reader)(tempfile.mkdtemp(), cancelled=lambda: True)
        page = ScriptedPage([dict(rows=[], root=True, module='测验与作业', empty=True, login=False, courseName='')])
        with self.assertRaises(InterruptedError):
            reader._settle(page, ASSIGNMENTS_SCRIPT,
                           lambda value: classify_assignment_page(value) != 'error', timeout=2)


class ScriptedPage:
    """Minimal page that returns a fixed or per-call scripted value."""

    def __init__(self, values):
        self.values = values if callable(values) else list(values)
        self.calls = 0

    def evaluate(self, script):
        if callable(self.values):
            return self.values()
        value = self.values[min(self.calls, len(self.values) - 1)]
        self.calls += 1
        return value

    def wait_for_timeout(self, milliseconds):
        return None
