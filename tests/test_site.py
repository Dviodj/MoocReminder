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
from site_scripts import (ASSIGNMENTS_SCRIPT, ENTER_SCRIPT, HOME_CARDS_SCRIPT, SECTION_SCRIPT,
                          CATEGORIES_SCRIPT, classify_assignment_page)

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
        # One page is reused, so a loadFinished left over from the previous
        # render can arrive after this setHtml and make the wait below return
        # while the old document is still showing. Waiting for the main
        # frame's own commit signal (rather than only loadFinished) rules that
        # out without touching the HTML the tests assert against.
        self.page.setHtml(html)
        self.assertTrue(wait_until(lambda: SiteScriptTests.loaded), '页面未能在限定时间内渲染完成')
        self.assertTrue(wait_until(self._painted), '页面未能在限定时间内绘制完成')

    def _painted(self):
        box = {}
        self.page.runJavaScript('document.readyState', lambda value: box.update(value=value))
        wait_until(lambda: 'value' in box, timeout=2.0)
        return box.get('value') == 'complete'

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

    def render_cards(self, *cards):
        self.render('''<div class="LearnContentFrame_list__bmm_1 learn-exam-list">
          <nav><a class="learn-navigation-link" href="/mooc/learn/NEU-1002822009/announce?tid=1">公告</a>
          <a class="learn-navigation-link" aria-current="page"
             href="/mooc/learn/NEU-1002822009/assessments?tid=1">测验与作业</a></nav>
          <section><div class="learn-exam-group-header">第一章 随机事件</div>
          <div class="learn-exam-group-items">''' + ''.join(cards) + '''</div></section></div>''')

    def test_the_new_course_page_is_read_like_the_old_one(self):
        self.render_cards(
            '''<article class="learn-assessment-card"><div class="learn-assessment-head-row">
                 <button aria-controls="assessment-panel-homework-1258594885" class="learn-assessment-head" type="button">
                   <span class="learn-assessment-title">第1章作业</span>
                   <span class="learn-assessment-deadline">截止时间：2026/09/28 23:30</span></button></div></article>''',
            '''<article class="learn-assessment-card"><div class="learn-assessment-head-row">
                 <button aria-controls="assessment-panel-quiz-1258592000" class="learn-assessment-head" type="button">
                   <span class="learn-assessment-title">第1章测验</span>
                   <span class="learn-assessment-deadline">截止时间：2026/10/12 23:30</span></button></div></article>''')
        data = self.evaluate(ASSIGNMENTS_SCRIPT)
        self.assertEqual(data['layout'], 'cards')
        self.assertEqual(classify_assignment_page(data), 'loaded')
        self.assertEqual([row['name'] for row in data['rows']], ['第1章作业', '第1章测验'])
        self.assertEqual(data['rows'][0]['deadline'], '截止时间：2026/09/28 23:30')
        # The kind is not in the visible text, only in the panel id.
        self.assertEqual([row['kind'] for row in data['rows']], ['作业', '测验'])
        self.assertEqual(data['rows'][0]['chapter'], '第一章 随机事件')

    def test_the_new_page_can_say_it_has_nothing_to_do(self):
        self.render('''<div class="LearnContentFrame_list__bmm_1"><nav>
          <a class="learn-navigation-link" aria-current="page">测验与作业</a></nav>
          <h2 class="learn-empty-state-title">暂无作业和测验</h2></div>''')
        self.assertEqual(classify_assignment_page(self.evaluate(ASSIGNMENTS_SCRIPT)), 'empty')

    def test_a_new_page_without_a_list_is_not_mistaken_for_an_empty_one(self):
        # A section still loading must not be reported as "no assignments".
        self.render('''<div class="LearnContentFrame_list__bmm_1"><nav>
          <a class="learn-navigation-link" aria-current="page">测验与作业</a></nav></div>''')
        self.assertEqual(classify_assignment_page(self.evaluate(ASSIGNMENTS_SCRIPT)), 'error')

    def test_the_section_is_chosen_from_the_course_navigation(self):
        self.render('''<nav><a class="learn-navigation-link" aria-current="page"
            href="/mooc/learn/NEU-1002822009/announce?tid=1">公告</a>
          <a class="learn-navigation-link" href="/mooc/learn/NEU-1002822009/assessments?tid=1">测验与作业</a></nav>''')
        section = self.evaluate(SECTION_SCRIPT)
        self.assertEqual(section['at'], '公告')
        self.assertIn('测验与作业', section['tabs'])

    def test_entering_a_course_prefers_the_enrol_button(self):
        self.render('''<a class="ux-btn">立即参加</a>
          <a class="course-enroll-info_course-enroll_buttons_enroll-btn ux-btn">已参加，进入学习</a>''')
        box = {}
        self.page.runJavaScript('JSON.stringify((' + ENTER_SCRIPT + ')())',
                                lambda value: box.update(value=value))
        self.assertTrue(wait_until(lambda: 'value' in box), '脚本未在限定时间内返回')
        import json
        self.assertTrue(json.loads(box['value'])['clicked'])

    def test_the_redesigned_course_page_reports_the_signed_in_user(self):
        # The new page has no "个人中心" link: the user comes from the inline
        # business-runtime-globals script, which leaves window.webUser behind.
        self.render('''<script>window.webUser = {"id": "1535016144", "userId": "1535016144",
            "name": "智医2501胡家晋20253989"};</script><h1>概率论与数理统计</h1>''')
        from site_scripts import ACCOUNT_SCRIPT
        data = self.evaluate(ACCOUNT_SCRIPT)
        self.assertEqual(data['id'], '1535016144')
        self.assertEqual(data['name'], '智医2501胡家晋20253989')
        self.assertFalse(data['login'])

    def test_the_old_course_page_still_reports_the_user_from_its_link(self):
        self.render('''<a href="https://www.icourse163.org/home.htm?userId=777001">个人中心</a>''')
        from site_scripts import ACCOUNT_SCRIPT
        self.assertEqual(self.evaluate(ACCOUNT_SCRIPT)['id'], '777001')

    def test_a_page_without_any_user_marker_asks_for_a_login(self):
        self.render('<div>课程介绍 登录 | 注册</div>')
        from site_scripts import ACCOUNT_SCRIPT
        data = self.evaluate(ACCOUNT_SCRIPT)
        self.assertEqual(data['id'], '')
        self.assertTrue(data['login'])

    def test_the_course_name_stops_at_the_title(self):
        # The redesigned header keeps the name, its badges and the teachers in
        # one block, so reading its innerText gives several lines.
        self.render_cards(
            '''<article class="learn-assessment-card"><div class="learn-assessment-head-row">
                 <button aria-controls="assessment-panel-homework-1" class="learn-assessment-head">
                   <span class="learn-assessment-title">第1章作业</span>
                   <span class="learn-assessment-deadline">截止时间：2026/09/28 23:30</span></button></div></article>''')
        self.page.runJavaScript('''document.body.insertAdjacentHTML('afterbegin',
            '<header><div class="learn-sub-header-identity"><h1>概率论与数理统计</h1>'
            + '<span class="learn-sub-header-tag">认证学习</span>'
            + '<span class="learn-sub-header-tag">一流课程</span></div></header>')''')
        self.assertTrue(wait_until(lambda: True))
        data = self.evaluate(ASSIGNMENTS_SCRIPT)
        self.assertEqual(data['courseName'], '概率论与数理统计')

    def test_a_spoc_course_is_read_from_its_due_panel(self):
        # A SPOC course has no assessment list page: its 测验与作业 panel renders
        # empty and the work is a set of #/learn/hw and #/learn/quiz routes. The
        # one place those are listed together is the 即将到期 panel.
        self.render('''<div id="courseLearn-inner-box"><h2 class="u-learn-moduletitle">公告</h2>
          <h2 class="tit">即将到期</h2><div class="infos">
            <a class="witit" href="#/learn/hw?id=1258598636">第四周&nbsp;振动单元作业</a>
            <p>互评截止时间：2026/10/25 23:30</p>
            <a class="witit" href="#/learn/quiz?id=1258601516">第五周&nbsp;波动（I）单元测验</a>
            <p>截止提交时间：2026/10/25 23:30</p></div></div>''')
        data = self.evaluate(ASSIGNMENTS_SCRIPT)
        self.assertEqual(data['layout'], 'due')
        self.assertEqual(classify_assignment_page(data), 'loaded')
        self.assertEqual([row['deadline'] for row in data['rows']], ['2026/10/25 23:30', '2026/10/25 23:30'])
        self.assertEqual([row['kind'] for row in data['rows']], ['作业', '测验'])

    def test_a_due_entry_without_a_deadline_is_not_guessed(self):
        # The panel is only trusted for what it states plainly; an entry with no
        # deadline leaves the page classified as a failure, not as a row.
        self.render('''<div id="courseLearn-inner-box"><h2 class="tit">即将到期</h2>
          <div class="infos"><a href="#/learn/hw?id=1">第四周 振动单元作业</a></div></div>''')
        self.assertEqual(classify_assignment_page(self.evaluate(ASSIGNMENTS_SCRIPT)), 'error')

    def test_the_spoc_navigation_is_an_ant_menu(self):
        self.render('''<ul class="ant-menu"><li class="ant-menu-item ant-menu-item-selected">公告</li>
          <li class="ant-menu-item">测验与作业</li><li class="ant-menu-item">考试</li></ul>''')
        data = self.evaluate(SECTION_SCRIPT)
        self.assertEqual(data['tabs'], ['公告', '测验与作业', '考试'])
        self.assertEqual(data['at'], '公告')
        from site_scripts import OPEN_SECTION_SCRIPT
        box = {}
        self.page.runJavaScript('JSON.stringify((' + OPEN_SECTION_SCRIPT + ')("测验与作业"))',
                                lambda value: box.update(value=value))
        self.assertTrue(wait_until(lambda: 'value' in box), '脚本未在限定时间内返回')
        import json
        self.assertTrue(json.loads(box['value'])['clicked'])

    def test_a_spoc_course_is_entered_through_start_studying(self):
        self.render('<a class="u-btn j-nextlink" href="#/learn/content">开始学习</a>')
        box = {}
        self.page.runJavaScript('JSON.stringify((' + ENTER_SCRIPT + ')())',
                                lambda value: box.update(value=value))
        self.assertTrue(wait_until(lambda: 'value' in box), '脚本未在限定时间内返回')
        import json
        self.assertEqual(json.loads(box['value'])['text'], '开始学习')

    def test_a_card_carries_the_course_anchor_even_without_an_href(self):
        # The card itself is not a link; the one address in its markup is the
        # score anchor, which still names the course and needs no term id.
        self.render('''<div class="j-course-card-box"><div class="title"><span class="text">概率论与数理统计</span></div>
          <a href="/university/NEU">东北大学</a><a href="/learn/NEU-1002822009#/learn/score">成绩</a></div>''')
        data = self.evaluate(HOME_CARDS_SCRIPT)
        self.assertEqual(data[0]['href'], '')
        self.assertEqual(data[0]['learn'], '/learn/NEU-1002822009#/learn/score')
        from browser import loose_course_url
        self.assertEqual(loose_course_url(data[0]['learn']),
                         'https://www.icourse163.org/learn/NEU-1002822009#/learn/testlist')


    def test_the_section_script_is_a_function_that_takes_its_section(self):
        # evaluate() calls its argument with no arguments, so a script taking a
        # parameter has to be invoked explicitly with the name it wants.
        self.render('''<nav><a class="learn-navigation-link" aria-current="page"
            href="/mooc/learn/NEU-1002822009/announce?tid=1">公告</a>
          <a class="learn-navigation-link" href="/mooc/learn/NEU-1002822009/assessments?tid=1">测验与作业</a></nav>''')
        from site_scripts import OPEN_SECTION_SCRIPT
        box = {}
        self.page.runJavaScript('JSON.stringify((' + OPEN_SECTION_SCRIPT + ')("测验与作业"))',
                                lambda value: box.update(value=value))
        self.assertTrue(wait_until(lambda: 'value' in box), '脚本未在限定时间内返回')
        import json
        clicked = json.loads(box['value'])
        self.assertTrue(clicked['clicked'])
        self.assertEqual(clicked['href'], '/mooc/learn/NEU-1002822009/assessments?tid=1')


class CourseAddressTests(unittest.TestCase):
    """The addresses the reader builds, and what it accepts."""

    def test_a_learn_link_without_a_term_id_is_still_a_learn_link(self):
        from browser import learn_address
        self.assertEqual(learn_address('https://www.icourse163.org/learn/NEU-1002822009#/learn/testlist'),
                         'https://www.icourse163.org/learn/NEU-1002822009')
        self.assertEqual(learn_address('https://www.icourse163.org/learn/NEU-1002822009?tid=1488060444#/x'),
                         'https://www.icourse163.org/learn/NEU-1002822009?tid=1488060444')
        self.assertIsNone(learn_address('https://www.icourse163.org/channel/2001.htm'))

    def test_a_spoc_anchor_keeps_its_prefix(self):
        from browser import loose_course_url
        self.assertEqual(loose_course_url('/spoc/learn/NEU-1486300165#/learn/score'),
                         'https://www.icourse163.org/spoc/learn/NEU-1486300165#/learn/testlist')
        self.assertIsNone(loose_course_url('/university/NEU'))


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
