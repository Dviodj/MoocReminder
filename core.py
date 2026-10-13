"""Deadline and reminder rules; no browser or GUI dependencies."""
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urlsplit, parse_qs, urljoin

BEIJING = timezone(timedelta(hours=8))
HOME_URL = 'https://www.icourse163.org/home.htm?userId=0#/home/course'
COURSE_URL = 'https://www.icourse163.org/learn/EXAMPLE-1000?tid=1000#/learn/testlist'
DEFAULT_SETTINGS = dict(home_url=HOME_URL, manual_urls=[], interval_minutes=30,
                        reminder_hours=[72, 24, 3], include_quizzes=True, include_finished=False)


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


def valid_home_url(url):
    p = urlsplit(url)
    return p.scheme == 'https' and p.hostname == 'www.icourse163.org' and p.path == '/home.htm' and bool(parse_qs(p.query).get('userId', [''])[0].isdigit())


def account_id(home_url):
    return parse_qs(urlsplit(home_url).query).get('userId', [''])[0]


def normalize_course_url(url):
    p = urlsplit(urljoin('https://www.icourse163.org/', url))
    if p.scheme != 'https' or p.hostname != 'www.icourse163.org' or p.username or p.password:
        return None
    match = re.fullmatch(r'/(?:course|learn|spoc/course|spoc/learn)/([A-Za-z0-9_-]+)', p.path)
    tid = parse_qs(p.query).get('tid', [''])[0]
    if not match or not tid.isdigit():
        return None
    prefix = 'spoc/learn' if '/spoc/' in p.path else 'learn'
    return f'https://www.icourse163.org/{prefix}/{match[1]}?tid={tid}#/learn/testlist'


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
