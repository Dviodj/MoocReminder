import unittest
import tempfile
from playwright.sync_api import sync_playwright
from site_scripts import ASSIGNMENTS_SCRIPT, HOME_CARDS_SCRIPT, classify_assignment_page
from browser import configure_runtime_temp, MoocBrowser


class SiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        configure_runtime_temp(cls.temp.name)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def test_reads_submission_not_peer_review_and_includes_spoc_source_rows(self):
        self.page.set_content('''<div id="courseLearn-inner-box"><h2>测验与作业</h2>
          <div class="j-empty">老师还没有发布测试和作业，请耐心等待</div>
          <div class="m-chapterQuizHwItem"><div class="j-titleName">第二章</div>
            <div class="u-quizHwListItem"><div class="titleBox"><h4 class="j-name">第2章作业</h4>
              <div class="j-submitTime">截止时间：2026/10/18 23:30</div><a class="j-quizBtn">前往作业</a></div>
              <div class="detail">提交作业后不参与互评将影响成绩<div class="j-evalEnd">截止时间：2026/10/25 23:30</div></div>
            </div>
            <div class="u-quizHwListItem"><div class="titleBox"><h4 class="j-name">第四周 振动单元测试</h4>
              <div class="j-submitTime">截止时间：2026/10/18 23:30</div><a class="j-quizBtn">前往测验</a></div>
              <div class="j-submitCount">0/3</div></div></div></div>''')
        data = self.page.evaluate(ASSIGNMENTS_SCRIPT)
        self.assertEqual(classify_assignment_page(data), 'loaded')
        self.assertEqual(len(data['rows']), 2)
        self.assertEqual(data['rows'][0]['deadline'], '截止时间：2026/10/18 23:30')
        self.assertEqual(data['rows'][0]['status'], '')
        self.assertEqual(data['rows'][1]['status'], '未提交')
        self.assertEqual(data['rows'][1]['kind'], '测验')

    def test_only_explicit_empty_list_is_successful_empty(self):
        self.page.set_content('<div id="courseLearn-inner-box"><h2>测验与作业</h2><div class="j-empty">老师还没有发布测试和作业，请耐心等待</div></div>')
        self.assertEqual(classify_assignment_page(self.page.evaluate(ASSIGNMENTS_SCRIPT)), 'empty')
        self.page.set_content('<div id="courseLearn-inner-box"><h2>测验与作业</h2></div>')
        self.assertEqual(classify_assignment_page(self.page.evaluate(ASSIGNMENTS_SCRIPT)), 'error')
        self.page.set_content('<div>课程介绍 登录 | 注册</div>')
        self.assertEqual(classify_assignment_page(self.page.evaluate(ASSIGNMENTS_SCRIPT)), 'login_required')

    def test_malformed_row_is_not_silently_dropped(self):
        self.page.set_content('<div id="courseLearn-inner-box"><h2>测验与作业</h2><div class="u-quizHwListItem"><div class="titleBox"><h4 class="j-name">作业</h4></div></div></div>')
        self.assertEqual(classify_assignment_page(self.page.evaluate(ASSIGNMENTS_SCRIPT)), 'error')

    def test_cards_without_href_are_discovered_and_finished_status_preserved(self):
        self.page.set_content('''<div class="j-course-card-box"><div class="title"><span class="text">概率论与数理统计</span></div><div class="course-status">2027年1月15日学期结束</div></div>
          <div class="j-course-card-box"><div class="title"><span class="text">旧课</span></div><div class="course-status">2026年7月14日已结束</div></div>''')
        data = self.page.evaluate(HOME_CARDS_SCRIPT)
        self.assertEqual([r['name'] for r in data], ['概率论与数理统计', '旧课'])
        self.assertEqual([r['finished'] for r in data], [False, True])

    def test_delayed_spoc_source_is_not_accepted_as_empty(self):
        self.page.set_content('<div id="courseLearn-inner-box"><h2>测验与作业</h2><div class="j-empty">老师还没有发布测试和作业，请耐心等待</div></div>')
        self.page.evaluate('''() => setTimeout(() => {
          document.getElementById('courseLearn-inner-box').insertAdjacentHTML('beforeend',
          '<div class="u-quizHwListItem"><div class="titleBox"><h4 class="j-name">源课程作业</h4><div class="j-submitTime">2026/10/18 23:30</div></div></div>');
        }, 2200)''')
        reader = MoocBrowser(self.temp.name)
        data = reader._settle(self.page, ASSIGNMENTS_SCRIPT, lambda d: classify_assignment_page(d) != 'error', timeout=5)
        self.assertEqual(len(data['rows']), 1)

    def test_discovery_reads_second_page_with_same_named_course_and_popup(self):
        context = self.browser.new_context()
        first = '''<a id="card-one" class="j-course-card-box" onclick="window.open('https://www.icourse163.org/learn/NEU-1?tid=111#/learn/content')"><div class="title"><span class="text">同名课程</span></div><div class="school">学校甲</div><div class="course-status">2027年1月15日学期结束</div></a>'''
        second = '''<a id="card-two" class="j-course-card-box" onclick="window.open('https://www.icourse163.org/learn/NEU-2?tid=222#/learn/content')"><div class="title"><span class="text">同名课程</span></div><div class="school">学校乙</div><div class="course-status">2027年2月15日学期结束</div></a>'''
        import json
        html = ('<a href="https://www.icourse163.org/home.htm?userId=123456">个人中心</a><a href="#/home/course">MOOC课程</a>'
                + '<div id="cards">' + first + '</div><a id="next">下一页</a>'
                + '<script>document.getElementById("next").onclick=()=>{document.getElementById("cards").innerHTML='
                + json.dumps(second) + ';document.getElementById("next").className="th-bk-disable-gh";};</script>')
        context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html; charset=utf-8', body=html if '/home.htm' in route.request.url else '<h1>学习页面</h1>'))
        reader = MoocBrowser(self.temp.name)
        reader.context = context
        try:
            courses, errors = reader.discover_courses('https://www.icourse163.org/home.htm?userId=123456#/home/course')
            self.assertEqual(errors, [])
            self.assertEqual([c.url for c in courses], ['https://www.icourse163.org/learn/NEU-1?tid=111#/learn/testlist', 'https://www.icourse163.org/learn/NEU-2?tid=222#/learn/testlist'])
        finally:
            context.close()

    def test_cancel_during_page_wait_stops_immediately(self):
        reader = MoocBrowser(self.temp.name, cancelled=lambda: True)
        with self.assertRaises(InterruptedError):
            reader._settle(self.page, ASSIGNMENTS_SCRIPT, lambda d: True)


if __name__ == '__main__':
    unittest.main()
