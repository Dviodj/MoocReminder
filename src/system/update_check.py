"""Version check and announcements, pulled from the latest GitHub release.

There is no server behind this program: accounts and records never leave the
machine.  What the program *reads* is the public "latest release" document that
GitHub already publishes for this repository, so announcing something is
publishing a release, not deploying a service.

Two things follow from that, and both are deliberate:

* Every failure is silent.  A student who is offline, behind a firewall, or
  served something unexpected must still get a window with no error box in it.
* Nothing is ever sent.  The request is the same GET for every user; there is no
  account id, no machine id, no usage record, and no token.

Announcements ride in the release notes, so a release can say more than "new
version".  Inside the notes, an optional fenced block::

    ```mooc-reminder-announcements
    [{"id": "2026-10-20-icon", "title": "标题", "body": "正文",
      "link": "https://...", "level": "info"}]
    ```

The ``id`` is what makes an announcement "already seen": change it to show the
same title again, keep it to stay quiet.
"""

import json
import re
import urllib.request

from version import VERSION

# The announcements come from the releases API, not from raw.githubusercontent.com:
# measured from inside mainland China, the raw host is reset mid-download and the
# jsDelivr mirror stalls, while api.github.com answers in about a second.
RELEASE_API = 'https://api.github.com/repos/Dviodj/MoocReminder/releases/latest'
# Same document for every user, so the request carries nothing that identifies
# anyone, needs no token, and stays fetchable from a browser.
ANNOUNCEMENT_URLS = (RELEASE_API,)
USER_AGENT = 'MoocReminder'
# Short on purpose: the check runs behind the window, and a slow network must
# not pile up connections.
TIMEOUT_SECONDS = 5.0
LEVELS = ('info', 'warning')
# The card would otherwise be unbounded; a hundred announcements is a bug in the
# file, not a plan.
MAX_ANNOUNCEMENTS = 20
MAX_TEXT = 2000
# ```mooc-reminder-announcements ... ``` inside the release notes.
_BLOCK = re.compile(r'```mooc-reminder-announcements\s*\n(.*?)```', re.S)

_VERSION = re.compile(r'v?(\d+)\.(\d+)\.(\d+)$')


def version_tuple(text):
    """``'v0.2.1'`` -> ``(0, 2, 1)``; anything else -> ``None``.

    Returns None rather than raising: ``latest`` comes off the network, and a
    typo there must not be able to break the window.
    """
    if not isinstance(text, str):
        return None
    match = _VERSION.match(text.strip())
    return tuple(int(part) for part in match.groups()) if match else None


def newer(latest, current=VERSION):
    """Whether ``latest`` is a version the user does not have yet.

    Unparsable values compare as "no update", so a malformed file can never
    hide itself behind a permanent "有新版本" prompt.
    """
    here, there = version_tuple(current), version_tuple(latest)
    return bool(here and there and there > here)


def parse(payload):
    """Normalise a decoded document; ``None`` when it is not usable at all.

    Individual announcements that are malformed are dropped one by one instead
    of discarding the whole file, so one bad entry cannot silence the rest.
    """
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode('utf-8')
        except UnicodeDecodeError:
            return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    if not isinstance(payload, dict):
        return None
    latest = payload.get('latest')
    latest = latest.strip() if isinstance(latest, str) and version_tuple(latest) else ''
    url = payload.get('url')
    url = url.strip() if isinstance(url, str) and url.strip().startswith('https://') else ''
    notes = payload.get('notes')
    notes = notes.strip()[:MAX_TEXT] if isinstance(notes, str) else ''
    announcements, seen = [], set()
    for entry in payload.get('announcements') or []:
        if not isinstance(entry, dict):
            continue
        key = entry.get('id')
        title = entry.get('title')
        if not isinstance(key, str) or not key.strip() or key in seen or not isinstance(title, str) or not title.strip():
            continue
        seen.add(key)
        level = entry.get('level')
        body = entry.get('body')
        link = entry.get('link')
        announcements.append(dict(id=key.strip(), title=title.strip()[:200],
                                  body=body.strip()[:MAX_TEXT] if isinstance(body, str) else '',
                                  link=link.strip() if isinstance(link, str) and link.strip().startswith('https://') else '',
                                  level=level if level in LEVELS else 'info'))
    return dict(latest=latest, url=url, notes=notes, announcements=announcements[:MAX_ANNOUNCEMENTS])


def fetch(urls=ANNOUNCEMENT_URLS, timeout=TIMEOUT_SECONDS, opener=None):
    """Raw body of the first URL that answers; ``None`` when none does."""
    opener = opener or urllib.request.urlopen
    for url in urls:
        try:
            headers = {'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'}
            request = urllib.request.Request(url, headers=headers)
            with opener(request, timeout=timeout) as response:
                body = response.read()
        except Exception:
            # URLError, timeout, TLS trouble, DNS, a proxy that answers with
            # HTML... none of it is the user's problem, and none of it should
            # reach them.  Try the next mirror.
            continue
        if body:
            return body
    return None


def from_release(payload):
    """Turn one GitHub release into the announcement document.

    Kept separate from ``parse`` so the awkward part — release notes are
    Markdown written for humans — can be tested on its own.  The announcement
    block is optional: a release whose notes have none still reports its
    version, which is the common case.
    """
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode('utf-8')
        except UnicodeDecodeError:
            return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None
    if not isinstance(payload, dict) or not isinstance(payload.get('tag_name'), str):
        return None
    page = payload.get('html_url')
    body = payload.get('body')
    announcements = []
    match = _BLOCK.search(body) if isinstance(body, str) else None
    if match:
        try:
            found = json.loads(match.group(1))
        except ValueError:
            found = None
        if isinstance(found, list):
            announcements = found
    # The release body is for people: titles, emoji, whole paragraphs of change
    # log.  The card has room for one short line, so the first line is used and
    # the rest is available on the release page the button opens.
    notes = ''
    if isinstance(body, str):
        for line in body.splitlines():
            line = line.strip().lstrip('#').strip()
            if line and not line.startswith('```'):
                notes = line
                break
    return parse(dict(latest=payload['tag_name'],
                      url=page if isinstance(page, str) else '',
                      notes=notes, announcements=announcements))


def check(urls=ANNOUNCEMENT_URLS, timeout=TIMEOUT_SECONDS, opener=None):
    """The parsed document, or ``None``.  Never raises."""
    body = fetch(urls, timeout, opener)
    return from_release(body) if body else None


def unseen(announcements, seen=''):
    """Announcements the user has not dismissed yet, oldest first.

    ``seen`` is a list of ids; a single id is accepted too, because the setting
    started life as one string and readers may still hold that.
    """
    if isinstance(seen, str):
        seen = [seen] if seen else []
    dismissed = set(seen or ())
    return [item for item in announcements if item['id'] not in dismissed]
