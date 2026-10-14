"""MOOC reader. The caller supplies the embedded browser it should drive.

Reading always goes through a context provider, which the desktop program sets
to the QtWebEngine adapter. Without one there is no browser to read with, so
`ensure` reports that instead of quietly doing nothing.
"""
from dataclasses import dataclass, field
from pathlib import Path
import re
import time

from core import Course, normalize_course_url, parse_assignment, valid_home_url, account_id
from site_scripts import ASSIGNMENTS_SCRIPT, HOME_CARDS_SCRIPT, CATEGORIES_SCRIPT, ACCOUNT_SCRIPT, classify_assignment_page


class LoginRequired(RuntimeError):
    pass


@dataclass
class ScanResult:
    items: list = field(default_factory=list)
    courses: list = field(default_factory=list)
    successful_urls: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    account_id: str = ''

 
def _is_empty(data):
    """True when a script result reports 'nothing here' rather than content."""
    return ((isinstance(data, list) and not data)
            or (isinstance(data, dict) and data.get('empty') and not data.get('rows')))


class MoocBrowser:
    def __init__(self, folder, progress=lambda text: None, cancelled=lambda: False, context_provider=None):
        self.folder = Path(folder)
        self.progress = progress
        self.context = None
        self.headless = None
        self.account = ''
        self.cancelled = cancelled
        self.context_provider = context_provider

    def _check_cancel(self):
        if self.cancelled():
            raise InterruptedError('检查已停止')

    def close(self):
        try:
            if self.context:
                self.context.close()
        finally:
            self.context = None

    def ensure(self, headless=True, user_id=''):
        if not self.context_provider:
            raise RuntimeError('没有可用的内置浏览器，无法读取课程。请通过“启动MoocReminder.bat”启动程序。')
        self.context = self.context_provider(user_id, not headless, self.cancelled)
        self.headless, self.account = headless, user_id
        return self.context

    def login(self, home_url):
        if not valid_home_url(home_url):
            raise ValueError('课程主页须是慕课官网 /home.htm?userId=数字')
        context = self.ensure(headless=False, user_id=account_id(home_url))
        # Startup tabs can be replaced by Edge; use a page we create and own.
        page = context.new_page()
        page.goto('https://www.icourse163.org/', wait_until='commit', timeout=12000)
        page.bring_to_front()
        self.progress('请在打开的浏览器中登录。登录完成后返回本程序，点击“检查作业”。')

    def _goto(self, page, url):
        self._check_cancel()
        page.goto(url, wait_until='domcontentloaded', timeout=30000)
        self._check_cancel()
        try:
            page.wait_for_load_state('networkidle', timeout=10000)
        except Exception:
            pass

    def _settle(self, page, script, ready, timeout=20):
        # SPA data is asynchronous. Wait for a stable result before accepting emptiness.
        until = time.monotonic() + timeout
        last = None
        stable_since = None
        while time.monotonic() < until:
            self._check_cancel()
            data = page.evaluate(script)
            if ready(data):
                if not _is_empty(data) and data == last and stable_since and time.monotonic() - stable_since >= 1.5:
                    return data
                if data != last:
                    stable_since = time.monotonic()
                last = data
            else:
                last = None
                stable_since = None
            page.wait_for_timeout(350)
        # The fallback must reject a result that is merely stable: an empty list
        # that never fills in means the page did not finish loading, and
        # accepting it would report the course as having no assignments.
        if last is not None and ready(last) and not _is_empty(last) and stable_since and time.monotonic() - stable_since >= 1.5:
            return last
        raise RuntimeError('页面数据未完成加载，或官网页面结构发生变化；已保留上次结果。')

    def _verify_account(self, page, expected):
        evidence = self._settle(page, ACCOUNT_SCRIPT, lambda d: bool(d['id']) or d['login'])
        if not evidence['id']:
            raise LoginRequired('请先登录慕课，然后再检查作业。')
        if evidence['id'] != expected:
            raise LoginRequired('登录账号与设置中的课程主页不一致，请切换账号或修改主页链接。')

    def discover_courses(self, home_url, include_finished=False):
        if not valid_home_url(home_url):
            raise ValueError('课程主页链接不正确')
        page = self.context.new_page()
        courses, errors = {}, []
        try:
            self._goto(page, home_url)
            self._verify_account(page, account_id(home_url))
            try:
                page.locator('a[href="#/home/course"]').first.wait_for(state='attached', timeout=20000)
            except Exception as exc:
                raise LoginRequired('未能读取个人课程主页，请点击“登录慕课”重新登录。') from exc
            categories = list(dict.fromkeys(page.evaluate(CATEGORIES_SCRIPT)))
            if not categories:
                raise RuntimeError('未发现 MOOC / SPOC 分类，请确认主页链接和登录状态。')
            base = home_url.split('#')[0]
            for category in categories:
                self._check_cancel()
                self.progress('读取' + ('SPOC' if 'spoc' in category else 'MOOC') + '课程…')
                self._goto(page, base + category)
                try:
                    self._settle(page, HOME_CARDS_SCRIPT,
                                 lambda cards: bool(cards) or bool(re.search('还没有.*课程|暂无.*课程', page.locator('body').inner_text())))
                    visited = set()
                    for number in range(50):
                        self._check_cancel()
                        cards = page.evaluate(HOME_CARDS_SCRIPT)
                        signature = tuple(c['fingerprint'] for c in cards)
                        if signature in visited:
                            raise RuntimeError('课程分页没有更新，无法确认已读完全部课程。')
                        visited.add(signature)
                        for card in cards:
                            self._check_cancel()
                            if card['finished'] and not include_finished:
                                continue
                            name = card['name'] or '未命名课程'
                            try:
                                url = normalize_course_url(card['href']) if card['href'] else None
                                if not url:
                                    popup = None
                                    try:
                                        with page.expect_popup(timeout=15000) as opened:
                                            page.locator('.j-course-card-box').nth(card['index']).locator('.title span.text').click()
                                        popup = opened.value
                                        popup.wait_for_url(re.compile(r'https://www\.icourse163\.org/(?:spoc/)?learn/'), timeout=20000)
                                        url = normalize_course_url(popup.url)
                                    finally:
                                        if popup:
                                            popup.close()
                                if not url:
                                    raise RuntimeError('学习链接缺少学期编号，请手动添加该课程的作业页链接。')
                                courses[url] = Course(name, url)
                            except Exception as exc:
                                errors.append(name + '：' + str(exc).splitlines()[0])
                        next_page = page.get_by_text('下一页', exact=True)
                        if next_page.count() == 0:
                            break
                        link = next_page.first
                        cls = link.get_attribute('class') or ''
                        if 'disable' in cls or link.get_attribute('aria-disabled') == 'true' or not link.is_visible():
                            break
                        link.click()
                        self._settle(page, HOME_CARDS_SCRIPT, lambda c: bool(c) and tuple(r['fingerprint'] for r in c) != signature)
                    else:
                        raise RuntimeError('课程页数超过 50 页，已停止检查。')
                except Exception as exc:
                    errors.append(('SPOC' if 'spoc' in category else 'MOOC') + '分类：' + str(exc).splitlines()[0])
            return list(courses.values()), errors
        finally:
            page.close()

    def scan_course(self, course):
        page = self.context.new_page()
        try:
            self._goto(page, course.url)
            if '/learn/' not in page.url:
                raise LoginRequired('作业链接跳转到课程介绍，请重新登录或确认已选课。')
            self._verify_account(page, self.account)
            data = self._settle(page, ASSIGNMENTS_SCRIPT, lambda d: classify_assignment_page(d) != 'error')
            state = classify_assignment_page(data)
            if state == 'login_required':
                raise LoginRequired('登录已失效，请重新登录。')
            real_course = Course(data['courseName'] or course.name, course.url)
            return [parse_assignment(row, real_course) for row in data['rows']], real_course
        finally:
            page.close()

    def scan(self, settings):
        self.ensure(headless=True, user_id=account_id(settings['home_url']))
        result = ScanResult(account_id=self.account)
        try:
            courses, errors = self.discover_courses(settings['home_url'], settings['include_finished'])
            result.errors.extend(errors)
        except Exception as exc:
            courses = []
            result.errors.append('课程主页：' + str(exc).splitlines()[0])
        by_url = {course.url: course for course in courses}
        for raw in settings['manual_urls']:
            url = normalize_course_url(raw)
            if url:
                by_url.setdefault(url, Course('手动添加课程', url))
            else:
                result.errors.append('手动作业链接无效：' + raw)
        if not by_url:
            result.errors.append('未读取到可检查的课程。请登录或在设置中添加作业页面链接。')
        for i, course in enumerate(by_url.values(), 1):
            self._check_cancel()
            self.progress(f'检查 {i}/{len(by_url)}：{course.name}')
            try:
                items, actual = self.scan_course(course)
                result.items.extend(items)
                result.courses.append(actual)
                result.successful_urls.append(course.url)
            except Exception as exc:
                result.errors.append(course.name + '：' + str(exc).splitlines()[0])
                if isinstance(exc, LoginRequired):
                    self.progress('检测到登录问题，请重新登录。')
        return result
