"""MOOC reader. The caller supplies the embedded browser it should drive.

Reading always goes through a context provider, which the desktop program sets
to the QtWebEngine adapter. Without one there is no browser to read with, so
`ensure` reports that instead of quietly doing nothing.
"""
from dataclasses import dataclass, field
from pathlib import Path
import json
import re
import time
from urllib.parse import urlsplit

from core import (Course, normalize_course_url, parse_assignment, valid_home_url,
                  normalize_home_url, home_url_problem, account_id)
from site_scripts import (ASSIGNMENTS_SCRIPT, ENTER_SCRIPT, HOME_CARDS_SCRIPT, OPEN_SECTION_SCRIPT,
                          SECTION_SCRIPT, CATEGORIES_SCRIPT, ACCOUNT_SCRIPT, classify_assignment_page)


class LoginRequired(RuntimeError):
    pass


# The site replaced its course pages: the section that lists quizzes and
# homework is now reached through the course's own navigation instead of a
# fixed address, and the address itself carries a term id that only exists
# after the course is entered from its introduction page. Everything below
# works with either generation, so a course that still serves the old page is
# read exactly as before.
SECTION = '测验与作业'
_COURSE_KEY = re.compile(r'/((?:spoc/)?(?:course|learn))/([A-Za-z0-9_-]+)')
_LEARN_PATH = re.compile(r'/(?:spoc/)?learn/[A-Za-z0-9_-]+')
_TERM = re.compile(r'[?&]tid=(\d+)')


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


def learn_address(url):
    """The address of a course's learn pages, built from any course address.

    Both the old and the new site call the same course the same way in the URL;
    only the section that follows differs, and the new site takes the section
    from navigation rather than from the address. A link found on the course
    home has no term id, and does not need one: the site resolves the term from
    the session and redirects.
    """
    parts = urlsplit(url or '')
    course = _COURSE_KEY.search(parts.path or '')
    if not course:
        return None
    term = _TERM.search('?' + (parts.query or ''))
    prefix = 'spoc/learn' if course.group(1).startswith('spoc') else 'learn'
    address = f'https://www.icourse163.org/{prefix}/{course.group(2)}'
    return address + ('?tid=' + term.group(1) if term else '')


def loose_course_url(anchor):
    """Turn a card's own anchor (``/learn/NEU-1#/learn/score``) into a course URL."""
    match = re.match(r'^/((?:spoc/)?learn)/([A-Za-z0-9_-]+)', anchor or '')
    if not match:
        return None
    return f'https://www.icourse163.org/{match.group(1)}/{match.group(2)}#/learn/testlist'


def intro_address(url):
    """The course introduction page, which is where a term id can be learned."""
    learn = learn_address(url)
    if not learn:
        return None
    parts = urlsplit(learn)
    course = _COURSE_KEY.search(parts.path).group(2)
    prefix = 'spoc/course' if '/spoc/' in parts.path else 'course'
    return f'https://www.icourse163.org/{prefix}/{course}?tid={_TERM.search(learn).group(1)}'


# Cookies the MOOC site issues; anything else in a pasted header is ignored so a
# user cannot accidentally hand over unrelated sessions.
MOOC_COOKIE_NAMES = ('NTESSTUDYSI', 'STUDY_SESS', 'STUDY_INFO', 'EDUWEBDEVICE', 'P_INFO',
                     'NTES_SESS', 'S_INFO', 'STUDY_PERSIST')
_BY_UPPER = {name.upper(): name for name in MOOC_COOKIE_NAMES}


def cookie_fields(pasted):
    """The `name=value` fields in whatever shape the user pasted.

    A Cookie header is only one of the things people copy: a cURL command, the
    developer tools' cookie table (tab separated, one cookie per line) and a
    plain list of name/value lines all turn up, and every one of them used to
    parse to nothing with no explanation.
    """
    text = str(pasted or '')
    if not text.strip():
        return []
    curl = re.search(r'-H\s*[\'"]Cookie:\s*(.*?)[\'"]', text, re.IGNORECASE | re.DOTALL)
    if curl:
        text = curl.group(1)
    else:
        text = re.sub(r'^\s*Cookie:\s*', '', text.strip(), flags=re.IGNORECASE)
    fields = []
    for line in re.split(r'[\r\n]+', text):
        line = line.strip()
        if not line:
            continue
        columns = [column.strip() for column in line.split('\t') if column.strip()]
        # The devtools cookie table has no '=' in its first two columns.
        if len(columns) >= 2 and '=' not in columns[0]:
            fields.append(f'{columns[0]}={columns[1]}')
            continue
        fields.extend(part.strip() for part in line.split(';'))
    return [field for field in fields if field]


def cookie_names_found(pasted):
    """Every cookie name in the paste, so a rejection can say what it saw."""
    names = []
    for field in cookie_fields(pasted):
        name = field.partition('=')[0].strip().strip('"')
        if name and name not in names:
            names.append(name)
    return names


def parse_cookie_header(pasted):
    """Read a Cookie header copied from a browser into (name, value, domain)."""
    pairs = []
    for field in cookie_fields(pasted):
        if '=' not in field:
            continue
        name, _, value = field.partition('=')
        name, value = name.strip().strip('"'), value.strip().strip('"')
        canonical = _BY_UPPER.get(name.upper(), '')
        if not canonical or not value or any(canonical == seen for seen, _, _ in pairs):
            continue
        pairs.append((canonical, value, '.icourse163.org'))
    return pairs


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
        problem = home_url_problem(home_url)
        if problem:
            raise ValueError(problem)
        context = self.ensure(headless=False, user_id=account_id(home_url))
        # Startup tabs can be replaced by Edge; use a page we create and own.
        page = context.new_page()
        page.goto('https://www.icourse163.org/', wait_until='commit', timeout=12000)
        page.bring_to_front()
        self.progress('请在打开的浏览器中登录。登录完成后返回本程序，点击“检查作业”。')

    def login_with_cookies(self, home_url, pasted):
        """Sign in by installing cookies copied from the user's own browser.

        The fallback for environments where the built-in window cannot complete
        a login. The pasted session is verified against the MOOC site, so a bad
        or expired paste is reported instead of silently producing no data.
        """
        problem = home_url_problem(home_url)
        if problem:
            raise ValueError(problem)
        cookies = parse_cookie_header(pasted)
        if not cookies:
            found = cookie_names_found(pasted)
            if found:
                # Name what was actually there: without this the user only sees
                # "no cookies found" and cannot tell what to copy instead.
                raise ValueError('粘贴的内容里没有慕课登录 Cookie（读到的是：' + '、'.join(found[:8])
                                 + '）。请在已登录慕课的浏览器里按 F12 → 网络（Network）→ 刷新 → '
                                   '点第一个请求 → 复制“Cookie”请求头的全部内容。')
            raise ValueError('没有解析出任何 Cookie。请从浏览器开发者工具复制 Cookie 请求头。')
        context = self.ensure(headless=True, user_id=account_id(home_url))
        context.apply_cookies(cookies)
        page = context.new_page()
        try:
            self._goto(page, home_url)
            self._verify_account(page, account_id(home_url))
        finally:
            page.close()
        self.progress(f'已用粘贴的 Cookie 登录，共 {len(cookies)} 项。')
        return len(cookies)

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

    def _open_assessments(self, page, course):
        """Get the page that lists quizzes and homework onto `page`.

        The new site no longer serves that list at a fixed address: opening one
        directly is bounced back to the course introduction. It has to be
        entered through the introduction page, and its section is then chosen
        from the course navigation. The old site answered the address directly,
        so it is still tried first — if it answers, nothing else is needed.
        """
        target = learn_address(course.url)
        if not target:
            raise RuntimeError('学习链接不正确，请手动添加该课程的作业页链接。')
        for attempt in (0, 1):
            self._goto(page, target if attempt == 0 else (intro_address(target) or target))
            if _LEARN_PATH.search(urlsplit(page.url).path or ''):
                break
            if attempt:
                raise LoginRequired('作业链接跳转到课程介绍，请重新登录或确认已选课。')
            entered = page.evaluate(ENTER_SCRIPT)
            if not entered.get('clicked'):
                raise LoginRequired('没有找到“已参加，进入学习”按钮，请确认已经选修这门课。')
            try:
                page.wait_for_url(re.compile(r'https://www\.icourse163\.org/(?:spoc/)?learn/'), timeout=20000)
            except Exception as exc:
                # Reaching the learn pages needs one more navigation on the new
                # site; if that did not happen the browser is on the
                # introduction page and the list will never appear.
                raise LoginRequired('未能进入课程学习页，请重新登录或确认已选课。') from exc
        # The old page carried the whole list in one document under a fixed
        # anchor. The new one only fetches a section's data once that section is
        # the current one, so the section has to be chosen by clicking it.
        self._settle(page, SECTION_SCRIPT, lambda d: bool(d.get('tabs')))
        section = page.evaluate(SECTION_SCRIPT)
        if section.get('login') and not section.get('tabs'):
            raise LoginRequired('登录已失效，请重新登录。')
        if section.get('tabs') and section.get('at') != SECTION and SECTION in section.get('tabs'):
            # The script takes the section name, so it is invoked here rather
            # than through evaluate(), which calls its argument with no
            # arguments of its own.
            clicked = page.evaluate_expression('(' + OPEN_SECTION_SCRIPT + ')(' + json.dumps(SECTION) + ')')
            if not clicked.get('clicked'):
                raise RuntimeError('未能切换到“测验与作业”，官网页面结构可能已发生变化。')
            self._settle(page, SECTION_SCRIPT, lambda d: d.get('at') == SECTION)

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
                                if not url and card.get('learn'):
                                    # The card's own anchor names the course; the
                                    # site resolves the term from the session, so
                                    # the learn URL works without a tid. It is kept
                                    # without one: the stored link is a label here,
                                    # and the reader adds the tid it lands on.
                                    url = loose_course_url(card['learn'])
                                if not url:
                                    # The card has neither an href nor a course
                                    # anchor, so the only way to learn its
                                    # address is to open it. The page's script
                                    # attaches to the click, and it lands on
                                    # /mooc/learn/<id>/<section>, which
                                    # normalize_course_url now accepts.
                                    popup = None
                                    try:
                                        with page.expect_popup(timeout=15000) as opened:
                                            page.locator('.j-course-card-box').nth(card['index']).locator('.title span.text').click()
                                        popup = opened.value
                                        popup.wait_for_url(re.compile(r'https://www\.icourse163\.org/(?:spoc/|mooc/)?learn/'), timeout=20000)
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
            self._open_assessments(page, course)
            # The account check reads the page that is actually open, so it has
            # to come after the navigation: the login gate sends an expired
            # session to the introduction page, which carries no account marker.
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
