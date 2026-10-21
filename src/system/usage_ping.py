"""Count roughly how many people run this program, without a server.

The publisher owns one GitHub issue and every opted-in install writes its own
comment there.  The comment says two things and nothing else: a random
identifier generated on that machine, and the program version.  Nothing about
the user, the courses, or the machine is sent, and the identifier is not
derived from anything — it cannot be traced back.

Why comments and not the issue body: the body is shared, so two installs that
started at the same second would overwrite each other's row, and the API only
lets the owner rewrite a body, which this program must never need.  A comment
is written once by the install that owns it and edited afterwards, so parallel
machines never collide.

Why an issue and not a server: this program has no server, and adding one would
mean someone has to keep it running and trustworthy.  A GitHub issue in the
publisher's own repository is a place the user can look at, and it disappears
the moment it is closed.

Reading needs no token at all; writing needs the publisher's, which is why the
feature is off unless the build ships one.
"""

from datetime import date
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

import paths
from version import VERSION

# Where the count lives. The publisher creates an issue titled ISSUE_TITLE.
ISSUE_URL = 'https://api.github.com/repos/Dviodj/MoocReminder/issues/1'
ISSUE_TITLE = 'MoocReminder 使用统计（程序自动填写，请勿关闭）'
USER_AGENT = 'MoocReminder'
TIMEOUT_SECONDS = 8.0
# Reading the comment list needs no credential; writing does.  A build without
# the token simply has nowhere to write, and then nothing is ever sent.
UPLOAD_TOKEN = ''
MAX_COMMENTS = 100
# One install is one comment, and GitHub serves a hundred per page: past that
# the oldest installs fall off the page the program reads.  The count is
# therefore an estimate and the dialog words it that way.
_ID = re.compile(r'^[0-9a-f]{16}$')
_LINE = re.compile(r'^`(?P<id>[0-9a-f]{16})`\s*(?P<version>\S*)\s*$')
REPORT_FILE = 'usage_report.json'
# Folders already consulted, so the file is read at most once per path.
_loaded = set()


def load_credentials(folder=None):
    """Take the publisher's write credential from ``usage_report.json``.

    The token cannot live in the source: this repository is public and GitHub
    revokes a leaked token on sight.  It therefore ships the same way the
    sender mailbox does — an untracked file beside the program, copied into the
    portable package by the packaging script.  A build without the file simply
    cannot write, which turns the feature off instead of failing loudly.
    """
    global UPLOAD_TOKEN, ISSUE_URL
    if folder is None:
        folder = PROGRAM_ROOT
    found = Path(folder)/REPORT_FILE
    if found in _loaded:
        return bool(UPLOAD_TOKEN)
    try:
        configured = json.loads(found.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        _loaded.add(found)
        return False
    if not isinstance(configured, dict):
        _loaded.add(found)
        return False
    token = str(configured.get('github_token', '') or '').strip()
    url = str(configured.get('issue_url', '') or '').strip()
    if not token:
        _loaded.add(found)
        return False
    UPLOAD_TOKEN = token
    if url:
        ISSUE_URL = url
    _loaded.add(found)
    return True


def available():
    """Whether this build can actually write the count."""
    return bool(ISSUE_URL.strip()) and bool(UPLOAD_TOKEN.strip())


def reads_available():
    """Whether the count can at least be looked at."""
    return bool(ISSUE_URL.strip())


def new_id():
    """A random install identifier: 16 hex characters, nothing derived."""
    return os.urandom(8).hex()


def valid_id(value):
    return isinstance(value, str) and bool(_ID.match(value))


def line_for(install):
    """The text this install posts: its id and the version. Nothing else."""
    if not valid_id(install):
        raise ValueError('安装标识无效。')
    return f'`{install}` {VERSION}'


def parse_line(text):
    """``install`` from one comment body, or '' when it is not ours."""
    match = _LINE.match((text or '').strip())
    return match.group('id') if match else ''


def comments_url(issue_url=None):
    return f'{(issue_url or ISSUE_URL).rstrip("/")}/comments'


def _request(url, method='GET', payload=None, timeout=TIMEOUT_SECONDS, opener=None,
             token=''):
    """One JSON request; ``(status, parsed)``; ``(0, None)`` for any failure.

    Never raises: the ping runs behind the window and a student who is offline
    must not see anything about it.
    """
    data = None
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        response = (opener or urllib.request.urlopen)(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc.code, _decoded(exc)
    except Exception:
        return 0, None
    return getattr(response, 'status', 200) or 200, _decoded(response)


def _decoded(response):
    try:
        raw = response.read()
    except Exception:
        return None
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception:
        return None


def lists(entries):
    """``{install: newest comment}`` for the comments that are ours.

    GitHub returns the page oldest first, so a later entry overwrites an earlier
    one: an install that posted twice is counted once, and the row this program
    will edit is the one the user would see last.
    """
    found = {}
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        install = parse_line(entry.get('body'))
        if not install:
            continue
        previous = found.get(install)
        if previous is None or _newer(entry, previous):
            found[install] = entry
    return found


def _newer(candidate, current):
    """Whether ``candidate`` is the later comment, by id then by order."""
    try:
        return int(candidate.get('id') or 0) > int(current.get('id') or 0)
    except (TypeError, ValueError):
        return True


def count(entries):
    """How many installs the page currently shows."""
    return len(lists(entries))


def send(install, today=None, opener=None, token=None, issue_url=None):
    """Write this install into the count, creating or editing its own comment.

    Returns ``(ok, message)``.  ``ok`` is False for "could not send now", which
    is normal for an offline machine and must never become an error box.
    """
    if not valid_id(install):
        return False, '安装标识无效。'
    if not (issue_url or ISSUE_URL).strip():
        return False, '这个版本还没有设置统计地址。'
    here = token if token is not None else UPLOAD_TOKEN
    if not here.strip():
        return False, '这个版本没有统计写入权限。'
    status, issue = _request(issue_url or ISSUE_URL, opener=opener, token=here)
    if status != 200 or not isinstance(issue, dict):
        return False, f'读不到统计页（{status or "网络不通"}）。'
    if issue.get('state') == 'closed':
        # Closing the issue is the publisher's "stop counting" switch.
        return False, '统计已停止。'
    url = f'{(issue_url or ISSUE_URL).rstrip("/")}/comments'
    # Read with the same credential that writes: the anonymous view of this
    # endpoint is served from a cache that stays up to a minute behind, so an
    # anonymous read right after our own write still shows the old page and the
    # program would post a second comment for an install that already has one.
    status, entries = _request(url, opener=opener, token=here)
    if status != 200 or not isinstance(entries, list):
        return False, f'读不到统计页（{status or "网络不通"}）。'
    mine = lists(entries).get(install)
    wanted = line_for(install)
    if mine and (mine.get('body') or '').strip() == wanted:
        return True, f'今天已经记过了，当前共有 {count(entries)} 台设备。'
    if mine:
        status, _ = _request(f'{url}/{mine.get("id")}', 'PATCH', {'body': wanted},
                             opener=opener, token=here)
    else:
        status, _ = _request(url, 'POST', {'body': wanted}, opener=opener, token=here)
    if status not in (200, 201):
        return False, f'统计没写进去（{status or "网络不通"}）。'
    # The page just read is already stale by one when a comment was added, but
    # saying "every device that is there, plus mine" is what the user asked
    # about — an exact figure costs another round trip and nobody needs it.
    total = count(entries) if mine else count(entries) + 1
    return True, f'已记录，当前共有 {total} 台设备。'


REPORTED_KEY = 'reported'
FAILED_KEY = 'failed'


def due(status, today=None):
    """Whether a ping may go out now, given the install's own log.

    ``status`` is this machine's ``usage_status`` dict: ``reported`` is the date
    of the last successful send, ``failed`` the date of the last attempt that
    did not get through.  At most one attempt per day either way — a machine
    that is offline every morning must not retry ten times a minute.
    """
    today = today or date.today().isoformat()
    if not isinstance(status, dict) or not valid_id(status.get('install')):
        return False
    if not available():
        return False
    return today not in (status.get(REPORTED_KEY), status.get(FAILED_KEY))


def report(status, today=None, opener=None, sender=send):
    """Send if due and return the ``usage_status`` dict to save.

    The dict is returned even when nothing was sent, so the caller can write it
    back unchanged.  ``sender`` is injectable because the tests cannot reach the
    network.
    """
    today = today or date.today().isoformat()
    status = dict(status or {})
    if not available():
        return status
    install = status.get('install') or ''
    if not valid_id(install):
        status.pop('install', None)
        return status
    if today in (status.get(REPORTED_KEY), status.get(FAILED_KEY)):
        return status
    try:
        ok, message = sender(install, today, opener=opener)
    except Exception as exc:  # never let a ping break the window
        ok, message = False, str(exc)
    status[REPORTED_KEY if ok else FAILED_KEY] = today
    status['message'] = message
    return status


def forget():
    """What a user who opts out gets back: an install with no memory."""
    return {}
