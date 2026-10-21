"""Deadline and reminder rules; no browser or GUI dependencies."""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urlsplit, parse_qs, urljoin

BEIJING = timezone(timedelta(hours=8))
HOME_URL = 'https://www.icourse163.org/home.htm?userId=0#/home/course'
COURSE_URL = 'https://www.icourse163.org/learn/EXAMPLE-1000?tid=1000#/learn/testlist'
# How often the summary mail goes out. `interval` counts days from a fixed
# reference day instead of from the last save, so "every 3 days" keeps the same
# rhythm across restarts and settings changes rather than drifting each time the
# page is opened. 2024-01-01 is a Monday, so the reference day and the weekly
# cycle agree with each other.
DIGEST_FREQUENCIES = ('daily', 'weekly', 'interval')
DIGEST_EPOCH = date(2024, 1, 1)
MIN_DIGEST_INTERVAL = 2
MAX_DIGEST_INTERVAL = 30
DEFAULT_DIGEST_INTERVAL = 3
WEEKDAY_NAMES = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')

DEFAULT_SETTINGS = dict(home_url=HOME_URL, manual_urls=[], interval_minutes=30,
                        reminder_hours=[72, 24, 3], include_quizzes=True, include_finished=False,
                        # Whether the user has already answered the "start with
                        # Windows?" card, so it is shown once and never again.
                        autostart_prompted=False,
                        # Ids of announcements the user has dismissed; kept per
                        # account because the card lives in that account's view.
                        announcement_seen=[],
                        # Opt-out of the version/announcement request. It is on
                        # by default, so the manual "检查更新" button uses this
                        # setting exactly like the automatic check does.
                        update_check=True,
                        email=dict(enabled=False, recipient='', sender='', server='', port=465,
                                   security='ssl', password='', digest_times=[],
                                   digest_frequency='daily', digest_weekday=0,
                                   digest_interval=DEFAULT_DIGEST_INTERVAL))

# Per-account settings the program may add later: an older data.json simply
# lacks them, and the defaults fill the gap. They are still validated, so a
# hand-edited file cannot smuggle in something the reader cannot use.
SOFT_SETTING_KEYS = ('announcement_seen',)


def now_beijing():
    return datetime.now(BEIJING)


@dataclass(frozen=True)
class Course:
    name: str
    url: str


@dataclass(frozen=True)
class Assignment:
    uid: str
    course: str
    course_url: str
    name: str
    kind: str
    deadline: datetime | None
    deadline_text: str
    status: str
    checked_at: str

    @property
    def revision(self):
        return self.uid + '|' + (self.deadline.isoformat() if self.deadline else self.deadline_text)

    def to_dict(self):
        data = asdict(self)
        data['deadline'] = self.deadline.isoformat() if self.deadline else None
        return data

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data['deadline'] = datetime.fromisoformat(data['deadline']) if data['deadline'] else None
        if data['deadline'] and data['deadline'].tzinfo is None:
            raise ValueError('Deadline must include a timezone')
        return cls(**data)


HOME_HOSTS = ('www.icourse163.org', 'icourse163.org', 'm.icourse163.org')
# Pasted links arrive wrapped in whatever the user copied: quotes, Chinese
# punctuation, a trailing full stop from a chat message.
_TRIM = ' \t\r\n' + '\'"“”‘’「」《》<>（）()'
_USER_ID = re.compile(r'userId[=/](\d+)', re.IGNORECASE)


def clean_pasted(text):
    """Strip the decoration a copied link usually carries."""
    cleaned = str(text or '').strip().strip(_TRIM).strip()
    return cleaned.rstrip('。，、,;；.!！?？').strip()


def home_host(url):
    """The host of a pasted link, tolerating a missing scheme."""
    text = clean_pasted(url)
    if text and '//' not in text:
        text = 'https://' + text.lstrip('/')
    return (urlsplit(text).hostname or '').lower()


def account_id(home_url):
    """The numeric userId, wherever the user's paste happened to keep it."""
    text = clean_pasted(home_url)
    found = _USER_ID.search(text)
    return found.group(1) if found else ''


def normalize_home_url(url):
    """The canonical course home link for a pasted personal page, else None.

    The personal page is copied in many shapes -- http, the mobile host, a link
    with no fragment, one wrapped in quotes. Accept them all and store a single
    canonical form so the account id, the reader and the profile folder agree.
    The placeholder userId=0 normalizes too, so the shipped default survives.
    """
    text = clean_pasted(url)
    if not text:
        return None
    if '//' not in text:
        text = 'https://' + text.lstrip('/')
    p = urlsplit(text)
    if p.username or p.password or (p.hostname or '').lower() not in HOME_HOSTS:
        return None
    user_id = account_id(text)
    if not user_id.isdigit():
        return None
    return f'https://www.icourse163.org/home.htm?userId={user_id}#/home/course'


def home_url_problem(url):
    """Why a pasted course-home link was rejected, in words the user can act on."""
    text = clean_pasted(url)
    if not text:
        return '请先粘贴你的慕课个人主页链接。'
    host = home_host(text)
    if not host:
        return '这不像一条网址。请在浏览器里打开慕课个人主页，复制地址栏里的完整链接。'
    if not (host == 'icourse163.org' or host.endswith('.icourse163.org')):
        return f'链接需要来自慕课官网（www.icourse163.org），当前是 {host}。'
    if host not in HOME_HOSTS:
        return f'请用 www.icourse163.org 上的链接，当前是 {host}。'
    if account_id(text).isdigit():
        return ''
    if re.search(r'/(course|learn|spoc)/', urlsplit(text).path or ''):
        return '这看起来是某一门课程的链接，不是个人主页。请在慕课顶部点“我的课程”，复制那个页面的链接。'
    return '链接里没有 userId。请在慕课里打开个人主页，复制地址栏里的整条链接（应包含 userId=数字）。'


def valid_home_url(url):
    """A well-formed course home link; the url may still be the userId=0 placeholder."""
    return normalize_home_url(url) is not None


def bound_home_url(url):
    """A course home link that names a real account, not the placeholder."""
    return valid_home_url(url) and account_id(url) not in ('', '0')


def binding_state(settings):
    """What an account still has to bind before it can check assignments.

    The MOOC home link decides whose assignments are read; the email decides
    where reminders are delivered. Both are per account and both are needed.
    """
    settings = settings or {}
    email = settings.get('email') or {}
    return dict(mooc=bound_home_url(str(settings.get('home_url', '') or '')),
                email=bool(str(email.get('recipient', '') or '').strip()))


def is_bound(settings):
    return all(binding_state(settings).values())


def _course_parts(url):
    """(prefix, course id, tid) for a course link, with '' for what is missing."""
    p = urlsplit(urljoin('https://www.icourse163.org/', clean_pasted(url)))
    if p.scheme != 'https' or (p.hostname or '').lower() != 'www.icourse163.org' or p.username or p.password:
        return '', '', ''
    match = re.fullmatch(r'/(?:course|learn|spoc/course|spoc/learn)/([A-Za-z0-9_-]+)', p.path)
    tid = parse_qs(p.query).get('tid', [''])[0]
    if not match:
        return '', '', tid
    prefix = 'spoc/learn' if '/spoc/' in p.path else 'learn'
    return prefix, match[1], tid


def normalize_course_url(url):
    prefix, course, tid = _course_parts(url)
    if not prefix or not tid.isdigit():
        return None
    return f'https://www.icourse163.org/{prefix}/{course}?tid={tid}#/learn/testlist'


def course_url_problem(url):
    """Why a pasted assignment link was rejected, so the user knows what to copy."""
    text = clean_pasted(url)
    if not text:
        return '请粘贴课程或作业页面的链接。'
    host = home_host(text)
    if not host:
        return '这不像一条网址。请打开课程页面，复制地址栏里的完整链接。'
    if 'icourse163.org' not in host:
        return f'作业链接需要来自慕课官网（www.icourse163.org），当前是 {host}。'
    prefix, course, tid = _course_parts(text)
    if not prefix:
        return '这不像课程链接。请在慕课里打开那门课程，复制地址栏里的链接。'
    if not tid.isdigit():
        return (f'链接里没有学期编号 tid。请进入课程的学习页（地址里带 tid=数字）再复制，'
                f'例如 …/learn/{course}?tid=1234567890')
    return ''


def parse_deadline(text):
    match = re.search(r'(20\d{2})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})\s*日?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?', text)
    if not match:
        return None
    try:
        values = [int(v or '0') for v in match.groups()]
        return datetime(*values, tzinfo=BEIJING)
    except ValueError:
        return None


def parse_assignment(row, course):
    name = row.get('name', '').strip()
    if not name:
        raise ValueError('作业条目缺少名称')
    deadline_text = row.get('deadline', '').strip()
    # Only inspect status fields. Descriptions often say "提交作业后…".
    status_text = row.get('status', '').strip()
    if re.search(r'未提交|未完成|待提交|继续作业|继续测验|开始作业|开始测验', status_text):
        status = 'pending'
    elif re.search(r'已提交|已完成|已作答|提交成功', status_text):
        status = 'completed'
    else:
        status = 'unknown'
    kind = row.get('kind') or ('测验' if re.search('测验|测试', name) else '作业')
    identity = '|'.join([course.url, row.get('chapter', ''), name, kind, str(row.get('duplicate_ordinal', 0))])
    uid = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
    return Assignment(uid, course.name, course.url, name, kind,
                      parse_deadline(deadline_text), deadline_text, status, now_beijing().isoformat())


def due_reminders(items, hours, state, now):
    """Mark crossed windows and return only the most urgent new window per item."""
    windows = sorted({float(h) for h in hours if float(h) > 0})
    alerts = []
    for item in items:
        if item.status == 'completed' or not item.deadline:
            continue
        remaining = (item.deadline - now).total_seconds() / 3600
        if remaining <= 0:
            continue
        eligible = [h for h in windows if remaining <= h]
        if not eligible:
            continue
        window = min(eligible)
        seen = state.setdefault(item.revision, [])
        if window not in seen:
            alerts.append((item, window))
        state[item.revision] = sorted(set(seen + eligible))
    return alerts


def _bounded_int(value, low, high, fallback):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if low <= number <= high else fallback


def digest_cadence(email):
    """The stored summary cadence as (frequency, weekday, interval).

    A field that is missing, of the wrong type or out of range falls back to the
    daily schedule, because a damaged setting must never turn the summaries off
    without saying so.
    """
    email = email or {}
    frequency = email.get('digest_frequency')
    if frequency not in DIGEST_FREQUENCIES:
        frequency = 'daily'
    return (frequency,
            _bounded_int(email.get('digest_weekday'), 0, 6, 0),
            _bounded_int(email.get('digest_interval'), MIN_DIGEST_INTERVAL, MAX_DIGEST_INTERVAL,
                         DEFAULT_DIGEST_INTERVAL))


def digest_day_due(day, frequency='daily', weekday=0, interval=DEFAULT_DIGEST_INTERVAL):
    """Whether a summary is scheduled on this calendar day."""
    day = day.date() if isinstance(day, datetime) else day
    if frequency == 'weekly':
        return day.weekday() == weekday
    if frequency == 'interval':
        elapsed = (day - DIGEST_EPOCH).days
        return elapsed >= 0 and elapsed % interval == 0
    return True


def digest_horizon(frequency='daily', weekday=0, interval=DEFAULT_DIGEST_INTERVAL):
    """How many days back a scheduled day still belongs to the current cycle."""
    if frequency == 'weekly':
        return 7
    if frequency == 'interval':
        return interval
    return 0


def digest_target_day(now, frequency='daily', weekday=0, interval=DEFAULT_DIGEST_INTERVAL):
    """The scheduled day whose summary is still outstanding, and whether it is today.

    A desktop program is not always running. Without this look-back a weekly
    summary would be skipped outright whenever the machine was off that day;
    with it the summary arrives late -- once -- instead of never.
    """
    day = now.date() if isinstance(now, datetime) else now
    for offset in range(0, digest_horizon(frequency, weekday, interval) + 1):
        candidate = day - timedelta(days=offset)
        if digest_day_due(candidate, frequency, weekday, interval):
            return candidate, offset == 0
    return day, True


def next_digest_day(now, frequency='daily', weekday=0, interval=DEFAULT_DIGEST_INTERVAL):
    """The next scheduled day, today included."""
    day = now.date() if isinstance(now, datetime) else now
    for offset in range(0, MAX_DIGEST_INTERVAL + 1):
        candidate = day + timedelta(days=offset)
        if digest_day_due(candidate, frequency, weekday, interval):
            return candidate
    return day


def digest_cadence_text(frequency='daily', weekday=0, interval=DEFAULT_DIGEST_INTERVAL):
    """The cadence in the words the settings page uses: 每天 / 每周三 / 每 3 天."""
    if frequency == 'weekly':
        return '每' + WEEKDAY_NAMES[weekday % 7]
    if frequency == 'interval':
        return f'每 {interval} 天'
    return '每天'


def due_digests(times, state, now, frequency='daily', weekday=0, interval=DEFAULT_DIGEST_INTERVAL):
    """Summary times that are due now, or that were missed earlier in the cycle.

    A time fires once per day: when its minute arrives, or on the first check
    after it has passed, so opening the program at 09:30 still delivers the
    08:00 summary. With a weekly or every-N-days cadence the mark is written
    against the day the summary belonged to, so a summary missed while the
    machine was off is still delivered -- late, and exactly once.

    The mark lives under its own `due|` prefix. It used to be written as
    `digest|<date>|<time>`, which is exactly the key the send log uses, so the
    log already looked "sent" by the time the mailer was asked -- and the daily
    summary was never sent at all.
    """
    day, today = digest_target_day(now, frequency, weekday, interval)
    due = []
    stamp = day.strftime('%Y-%m-%d')
    for entry in times or []:
        match = re.fullmatch(r'(\d{2}):(\d{2})', str(entry))
        if not match:
            continue
        if today and (int(match.group(1)), int(match.group(2))) > (now.hour, now.minute):
            continue
        key = f'due|digest|{stamp}|{entry}'
        if state.get(key) == stamp:
            continue
        state[key] = stamp
        due.append(entry)
    return sorted(due)


def remaining_text(item, now):
    if not item.deadline:
        return '截止时间待核对'
    seconds = (item.deadline - now).total_seconds()
    if seconds <= 0:
        return '已截止'
    minutes = max(1, int(seconds / 60))
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f'{days}天{hours}小时'
    return f'{hours}小时{minutes}分' if hours else f'{minutes}分钟'
