"""Version check and announcements, pulled from the latest GitHub release.

There is no server behind this program: accounts and records never leave the
machine.  What the program *reads* is the public "latest release" of this
repository that GitHub already publishes, so announcing something is publishing
a release, not deploying a service.

Two things follow from that, and both are deliberate:

* Every failure is silent.  A student who is offline, behind a firewall, or
  served something unexpected must still get a window with no error box in it.
* Nothing is ever sent.  The request is the same GET for every user; there is no
  account id, no machine id, no usage record, and no token.

Why the pages and not the REST API: an unauthenticated call to api.github.com is
limited to 60 requests *per hour per IP*, shared by everyone behind that address.
A whole dorm or campus NAT would exhaust the budget between them and lose update
notices for the rest of the hour — the failure being invisible made it worse.
``github.com/<repo>/releases/latest`` answers with a redirect to the current tag
and is not rate limited the same way, so the version is read from where that
redirect points and the notes from that release page.

Announcements ride in the release notes, so a release can say more than "new
version".  Inside the notes, an optional fenced block::

    ```mooc-reminder-announcements
    [{"id": "2026-10-20-icon", "title": "标题", "body": "正文",
      "link": "https://...", "level": "info"}]
    ```

The ``id`` is what makes an announcement "already seen": change it to show the
same title again, keep it to stay quiet.
"""

import html
import html.parser
import json
import re
import urllib.error
import urllib.request

from version import VERSION

RELEASES_PAGE = 'https://github.com/Dviodj/MoocReminder/releases'
# The repository named in `<repo>/releases/latest`; the redirect's target is
# parsed against it so that an unexpected tag cannot become "the latest version".
TAG_URL = RELEASES_PAGE + '/latest'
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
# What a redirect from /releases/latest looks like when parsed.
_REDIRECTED_TAG = re.compile(r'/releases/tag/([^/?#]+)$')
# Inside the rendered release page the notes are escaped HTML, and the fenced
# block becomes a <pre> whose text still carries the entities.
_PAGE_BLOCK = re.compile(r'```?mooc-reminder-announcements(.*?)```', re.S)

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
        # Anything other than an explicit true is treated as not pinned, so a
        # stray "" or "no" cannot pin somebody's notice by accident.
        pinned = entry.get('pinned') is True
        announcements.append(dict(id=key.strip(), title=title.strip()[:200],
                                  body=body.strip()[:MAX_TEXT] if isinstance(body, str) else '',
                                  link=link.strip() if isinstance(link, str) and link.strip().startswith('https://') else '',
                                  level=level if level in LEVELS else 'info',
                                  pinned=pinned))
    return dict(latest=latest, url=url, notes=notes, announcements=announcements[:MAX_ANNOUNCEMENTS])


def _get(url, timeout, opener):
    """Body of one request, or ``None`` for any failure.

    ``HTTPError`` is not a special case: a 404 or a rate-limit page is just
    another reason this check has nothing to say.
    """
    headers = {'User-Agent': USER_AGENT, 'Accept': 'text/html,application/json'}
    try:
        with opener(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
            body = response.read()
    except Exception:
        # URLError, timeout, TLS trouble, DNS, a proxy that answers with HTML...
        # none of it is the user's problem, and none of it should reach them.
        return None
    return body or None


def fetch(urls=(), timeout=TIMEOUT_SECONDS, opener=None):
    """Raw body of the first URL that answers; ``None`` when none does."""
    opener = opener or urllib.request.urlopen
    for url in urls:
        body = _get(url, timeout, opener)
        if body:
            return body
    return None


def latest_tag(timeout=TIMEOUT_SECONDS, opener=None):
    """The tag ``/releases/latest`` redirects to, e.g. ``'v0.2.2'``.

    Not every opener follows redirects (and the tests use one that does not), so
    both answers are accepted: a redirect whose ``Location`` names the tag, and a
    page that has already landed on ``/releases/tag/<tag>``.
    """
    opener = opener or urllib.request.urlopen
    headers = {'User-Agent': USER_AGENT, 'Accept': 'text/html'}
    try:
        response = opener(urllib.request.Request(TAG_URL, headers=headers), timeout=timeout)
    except urllib.error.HTTPError as error:
        # Refusing to redirect is how a stub client says where it would go.
        found = _REDIRECTED_TAG.search(error.headers.get('Location', '') if error.headers else '')
        return found.group(1) if found else None
    except Exception:
        return None
    with response:
        found = _REDIRECTED_TAG.search(response.geturl() or '') if hasattr(response, 'geturl') else None
    return found.group(1) if found else None


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


def announcements_in_page(page):
    """The announcement list carried by a rendered release page.

    GitHub renders the fenced block as ``<pre lang="mooc-reminder-announcements">``
    whose text still holds HTML entities, so the entities are unescaped before
    the JSON is read.  Using the parser rather than a regex keeps navigation
    markup and JSON payloads embedded elsewhere in the page out of the way.
    """
    if isinstance(page, (bytes, bytearray)):
        try:
            page = page.decode('utf-8')
        except UnicodeDecodeError:
            return []
    if not isinstance(page, str):
        return []
    for block in _code_blocks(page, 'mooc-reminder-announcements'):
        try:
            found = json.loads(html.unescape(block))
        except ValueError:
            continue
        if isinstance(found, list):
            return found
    return []


class _CodeBlockParser(html.parser.HTMLParser):
    """Collects the text of ``<pre>``/``<code>`` elements tagged with a language."""

    def __init__(self, language):
        super().__init__(convert_charrefs=False)
        self.language = language
        self.depth = 0
        self.current = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag not in ('pre', 'code'):
            return
        attributes = dict(attrs)
        if self.depth or attributes.get('lang') == self.language or attributes.get('class') == self.language:
            self.depth += 1
            if self.depth == 1:
                self.current = []

    def handle_endtag(self, tag):
        if tag in ('pre', 'code') and self.depth:
            self.depth -= 1
            if not self.depth:
                self.blocks.append(''.join(self.current))
                self.current = []

    def handle_data(self, data):
        if self.depth:
            self.current.append(data)

    def handle_entityref(self, name):
        # convert_charrefs=False is needed to keep the block's text intact, which
        # means entities arrive here instead of being resolved; putting them back
        # is what keeps `&quot;` from being swallowed.
        if self.depth:
            self.current.append(html.unescape('&' + name + ';'))

    def handle_charref(self, name):
        if self.depth:
            self.current.append(html.unescape('&#' + name + ';'))


def _code_blocks(page, language):
    """Text of every ``<pre>``/``<code>`` element tagged with ``language``."""
    parser = _CodeBlockParser(language)
    try:
        parser.feed(page)
        parser.close()
    except Exception:
        # A truncated or hostile page is just "no announcements".
        return []
    return parser.blocks


# Elements that end a line of prose.  Everything else (a link, bold, a path in
# ``<code>``) is inline and must not break the sentence it sits in.
_BLOCK_TAGS = frozenset(('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol',
                         'li', 'div', 'blockquote', 'table', 'tr', 'td', 'th',
                         'details', 'summary'))


class _NotesParser(html.parser.HTMLParser):
    """Renders the marked-up notes of a release page as plain text.

    Reading markup with regular expressions cannot find the end of an element
    that contains others: the first ``</div>`` it meets belongs to the code
    block, not to the body, and the notes stop in the wrong place.  This walks
    the nesting instead, and discards code blocks: the announcement block is
    data, shown as announcements, not as prose.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.started = False
        self.depth = 0
        self.skip = 0
        # The text of a heading that is holding no other element.  A block that
        # follows it may be the announcement block, and then the heading that
        # introduced it has to go too, or it reads as the notes' last word.
        self.heading = None
        self.parts = []

    def _gap(self, text):
        if self.parts and self.parts[-1] != '\n':
            self.parts.append(text)

    def _drop_heading_if_a_block_follows(self, tag):
        if self.heading is None:
            return
        if tag == 'pre':
            self.heading = None          # it introduced the block, drop both
        else:
            self.parts.append(self.heading)
            self.heading = None

    def handle_starttag(self, tag, attrs):
        if tag not in ('br', 'img', 'hr', 'input'):
            self._drop_heading_if_a_block_follows(tag)
        if self.skip or tag in ('pre', 'script', 'style'):
            if tag not in ('br', 'img', 'hr', 'input'):
                self.skip += 1
            return
        if not self.started:
            if 'markdown-body' in (dict(attrs).get('class') or ''):
                self.started = True
                self.depth = 1
            return
        self.depth += 1
        if tag == 'br':
            self.depth -= 1
            self.parts.append('\n')
        elif tag == 'li':
            self._gap('\n')
            self.parts.append('· ')
        elif tag in _BLOCK_TAGS:
            self._gap('\n')

    def handle_endtag(self, tag):
        if self.heading is not None and tag in ('pre', 'code'):
            self.heading = None
        if self.skip:
            if tag not in ('br', 'img', 'hr', 'input'):
                self.skip = max(0, self.skip - 1)
            return
        if not self.started:
            return
        self.depth -= 1
        if tag in _BLOCK_TAGS:
            self._gap('\n')
        if self.depth <= 0:
            self.started = False

    def handle_data(self, data):
        if self.started and not self.skip:
            self.parts.append(data)
            if len(data.strip()) > 1:
                self._last = data

    def text(self):
        body = ''.join(self.parts)
        lines = [line.strip() for line in body.replace('\r\n', '\n').split('\n')]
        kept = [line for line in lines if line != '公告']
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()


def release_notes(page):
    """One line standing in for the release notes on the card."""
    if isinstance(page, (bytes, bytearray)):
        page = page.decode('utf-8', 'replace')
    if not isinstance(page, str):
        return ''
    # The release body lives under `.markdown-body`; scroll bars, navigation and
    # the JSON that GitHub puts in the document head are all outside it.
    start = page.find('markdown-body')
    if start < 0:
        return ''
    end = page.find('</div>', page.find('For more details', start) if 'For more details' in page[start:] else start)
    segment = page[start:start + 20000]
    for line in re.findall(r'<p[^>]*>(.*?)</p>|<h[1-6][^>]*>(.*?)</h[1-6]>|<li[^>]*>(.*?)</li>', segment, re.S):
        plain = ' '.join(part for part in line if part)
        plain = html.unescape(re.sub(r'<[^>]+>', '', plain)).strip()
        if plain and 'mooc-reminder-announcements' not in plain:
            return re.sub(r'\s+', ' ', plain)
    return ''


def all_page(tag, page):
    """Release notes plus every announcement a rendered release page lists."""
    text = page.decode('utf-8', 'replace') if isinstance(page, (bytes, bytearray)) else (page or '')
    notes = release_notes(text)
    # The card shows one line, so it gets the first short paragraph; the archive
    # has room for what the card cannot show, and there is a lot of it (the
    # notes end in a full change log).
    return dict(notes=notes, detail=notes_block(text),
                announcements=announcements_in_page(text))


def notes_block(page):
    """The release notes as plain text, or ``''``.

    Rendered to text rather than kept as HTML: the archive shows it in a plain
    label, and feeding a page's markup to a rich-text widget would also feed it
    whatever else the page contained.
    """
    if isinstance(page, (bytes, bytearray)):
        page = page.decode('utf-8', 'replace')
    if not isinstance(page, str):
        return ''
    parser = _NotesParser()
    try:
        parser.feed(page)
        parser.close()
    except Exception:
        return ''
    return parser.text()[:MAX_TEXT]


def archive_url(page=100):
    """Where the program sends someone who wants the full list of releases."""
    return RELEASES_PAGE + ('?page=%d' % page if page else '')


def older_pages(page):
    """Links to the older releases listed at the bottom of a release page."""
    if isinstance(page, (bytes, bytearray)):
        page = page.decode('utf-8', 'replace')
    if not isinstance(page, str):
        return []
    found = []
    for href in re.findall(r'href="([^"]+)"', page):
        href = html.unescape(href)
        if '/releases' in href and 'page=' in href and 'expanded_assets' not in href:
            href = href.split('#')[0]
            if href.startswith('/'):
                href = 'https://github.com' + href
            if href.startswith('https://') and href not in found:
                found.append(href)
    return found


def older_releases(page, timeout=TIMEOUT_SECONDS, opener=None):
    """``[{tag, url}]`` for the releases after the first on this page.

    GitHub groups releases by day on the releases page, so one page can hold
    several; what makes them releases is the link to their tag page.
    """
    if isinstance(page, (bytes, bytearray)):
        page = page.decode('utf-8', 'replace')
    if not isinstance(page, str):
        return []
    found, seen = [], set()
    for match in re.finditer(r'href="([^"]*/releases/tag/([^"/?#]+))"', page):
        href, tag = match.group(1), match.group(2)
        if tag in seen or tag == 'latest' or not version_tuple(tag):
            continue
        seen.add(tag)
        if href.startswith('/'):
            href = 'https://github.com' + href
        if href.startswith('https://'):
            found.append(dict(tag=tag, url=href))
    return found[:MAX_ANNOUNCEMENTS]


def all(timeout=TIMEOUT_SECONDS, opener=None, pages=3):
    """Every release still worth reading, newest first.  Never raises.

    ``check`` answers "what is the latest"; this answers "what has been
    announced", which for the announcement archive means walking back a few
    releases.  It reuses the page the version check already had to fetch, so the
    usual cost is the same two requests plus one per older release.
    """
    tag = latest_tag(timeout, opener)
    if not tag:
        return []
    url = RELEASES_PAGE + '/tag/' + tag
    body = fetch((url,), timeout, opener) or b''
    found = [dict(tag=tag, url=url, page=body)]
    queue = older_pages(body) if pages > 1 else []
    while queue and len(found) < max(1, pages):
        body = fetch((queue.pop(0),), timeout, opener)
        if not body:
            continue
        queue.extend(older_pages(body))
        for entry in older_releases(body):
            if all(entry['tag'] != item['tag'] for item in found):
                found.append(dict(tag=entry['tag'], url=entry['url'], page=None))
        if len(found) > pages * MAX_ANNOUNCEMENTS:
            break
    for item in found[:max(1, pages)]:
        if item['page'] is None:
            item['page'] = fetch((item['url'],), timeout, opener) or b''
    return found[:max(1, pages)]


def check(urls=(), timeout=TIMEOUT_SECONDS, opener=None):
    """The parsed document, or ``None``.  Never raises.

    ``urls`` is only used by callers that name a page to consult instead of the
    live repository (the tests do); normally the tag comes from the redirect and
    the notes from that tag's page.
    """
    tag = latest_tag(timeout, opener)
    page = None
    if tag:
        page = fetch(urls or (RELEASES_PAGE + '/tag/' + tag,), timeout, opener)
    else:
        # No redirect answer: the page itself may still name the tag, and a
        # caller may have supplied whole documents to read instead.
        body = fetch(urls, timeout, opener)
        found = _REDIRECTED_TAG.search(body.decode('utf-8', 'replace') if body else '') \
            if body and b'/releases/tag/' in body else None
        if found:
            tag, page = found.group(1), body
        elif body and b'"tag_name"' in body[:2000]:
            return from_release(body)
    if not tag:
        return None
    page = page or b''
    return from_page(tag, page)


def from_page(tag, page):
    """Turn one release page into the announcement document."""
    text = page.decode('utf-8', 'replace') if isinstance(page, (bytes, bytearray)) else (page or '')
    return parse(dict(latest=tag, url=RELEASES_PAGE + '/tag/' + tag,
                      notes=release_notes(text), announcements=announcements_in_page(text)))


def release_page(tag, timeout=TIMEOUT_SECONDS, opener=None):
    """The rendered page of one release, or ``None``.

    The one-click update needs the download links, which live on the page and
    nowhere else; asking for the page directly also means the update does not have
    to wait for, or depend on, the version check that produced the tag.
    """
    tag = str(tag or '').strip().strip('/')
    if not tag or not version_tuple(tag) or '/' in tag:
        return None
    return fetch((RELEASES_PAGE + '/tag/' + tag,), timeout, opener)


def unseen(announcements, seen=''):
    """Announcements the user has not dismissed yet, the pinned one first.

    ``seen`` is a list of ids; a single id is accepted too, because the setting
    started life as one string and readers may still hold that.

    Dismissal is per id, so a pinned notice would otherwise be "most recent
    wins" like every other one. Someone who wants it to stay in front of
    everybody — a welcome message, an outage warning — marks it ``pinned``: it
    is shown first even when newer announcements arrive, and it still goes away
    for good once dismissed.
    """
    if isinstance(seen, str):
        seen = [seen] if seen else []
    dismissed = set(seen or ())
    left = [item for item in announcements if item['id'] not in dismissed]
    pinned = [item for item in left if item.get('pinned')]
    return pinned + [item for item in left if not item.get('pinned')]


def history(releases):
    """Newest first: one entry per version, its notes and its announcements.

    Everything published is kept, including what the user already dismissed: the
    archive exists so that "关掉了" does not mean "找不回来了".  The pinned
    ordering of ``unseen`` is for the card, which must show one thing; a list
    read on purpose is in date order like any archive.
    """
    found = []
    for release in releases or ():
        if not isinstance(release, dict):
            continue
        tag = release.get('tag')
        if not isinstance(tag, str) or not version_tuple(tag):
            continue
        announcements = [item for item in release.get('announcements') or ()
                         if isinstance(item, dict) and isinstance(item.get('id'), str)]
        found.append(dict(tag=tag, url=release.get('url') if isinstance(release.get('url'), str) else '',
                          notes=release.get('notes') if isinstance(release.get('notes'), str) else '',
                          detail=release.get('detail') if isinstance(release.get('detail'), str) else '',
                          latest=release.get('latest') is True,
                          announcements=announcements))
    found.sort(key=lambda item: version_tuple(item['tag']) or (0, 0, 0), reverse=True)
    return found
