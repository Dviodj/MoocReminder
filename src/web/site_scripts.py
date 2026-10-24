"""DOM selectors verified against the authenticated MOOC and SPOC pages.

The site moved its course pages to a new client application: the old one rendered
one widget (``#courseLearn-inner-box``) with one row class, the new one renders a
list of cards per section (``article.learn-assessment-card``). Both shapes are
read here, because users are on both while the site rolls the change out, and
because an old export or an older course page may still answer with the old DOM.

A SPOC course is a third shape: it has no assessment list page (the section opens
an empty panel) and keeps its work as ``#/learn/hw`` and ``#/learn/quiz`` routes,
which the 即将到期 panel of its announcement page does list together.
"""

# The new page needs a real navigation: its data is fetched by the client after
# load, so the section cannot simply be opened by URL — a cold request is
# bounced back to the course introduction.
# A SPOC course uses the same client but renders its nav as an Ant Design menu
# (``li.ant-menu-item``) rather than as learn-navigation-link anchors, so both
# shapes are read here.
SECTION_SCRIPT = r'''() => {
  const text = document.body.innerText || '';
  const links = Array.from(document.querySelectorAll('a.learn-navigation-link'));
  const items = Array.from(document.querySelectorAll('li.ant-menu-item'));
  const tabs = (links.length ? links.map(e => e.innerText.trim())
    : items.map(e => (e.innerText || '').trim())).filter(Boolean);
  const at = links.find(e => e.getAttribute('aria-current') === 'page')?.innerText.trim() ||
    (items.find(e => /ant-menu-item-selected/.test(e.className))?.innerText || '').trim();
  return { tabs, at: at || '',
    login: /登录\s*\|\s*注册|请先登录/.test(text) };
}'''

# Click the section link in the course's own navigation. Clicking the link beats
# setting location.href: the application keeps its state and swaps the panel
# instead of reloading and losing the session round-trip.
OPEN_SECTION_SCRIPT = r'''(want) => {
  const links = Array.from(document.querySelectorAll('a.learn-navigation-link'));
  const items = Array.from(document.querySelectorAll('li.ant-menu-item'));
  const pick = list => list.find(e => (e.innerText || '').trim() === want) ||
    list.find(e => (e.innerText || '').includes(want));
  const hit = pick(links) || pick(items);
  if (!hit) {
    return { clicked: false, tabs: links.concat(items).map(e => (e.innerText || '').trim()).filter(Boolean) };
  }
  hit.click();
  return { clicked: true, href: hit.getAttribute('href') || '' };
}'''

# The course introduction page carries the "already enrolled, go to study"
# button, and following it is the only way to discover the term id (tid) the new
# pages are addressed by. A SPOC course labels the same step "开始学习".
ENTER_SCRIPT = r'''() => {
  const wanted = /^(已参加，进入学习|继续学习|进入学习|开始学习)$/;
  const nodes = Array.from(document.querySelectorAll(
    '.course-enroll-info_course-enroll_buttons_enroll-btn, .u-btn, .ux-btn, a, button, [role="button"]'));
  const hit = nodes.find(e => wanted.test((e.innerText || '').trim()));
  if (!hit) return { clicked: false, text: (document.body.innerText || '').slice(0, 400) };
  hit.click();
  return { clicked: true, text: (hit.innerText || '').trim() };
}'''

# One script for both page generations. Which one answered is reported back in
# `layout`, so a page that is neither is an error rather than "no assignments".
ASSIGNMENTS_SCRIPT = r'''() => {
  const text = document.body.innerText || '';
  const visible = e => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const read = (e,s) => e.querySelector(s)?.innerText?.trim() || '';
  // The redesigned header puts the title, its badges and the teachers in one
  // block, so innerText of the header reads "概率论与数理统计\n认证学习\n一流课程".
  // The heading on its own is the course name; the title tag is the fallback.
  const courseNameOf = () => document.querySelector('h1')?.innerText?.trim() ||
    read(document, '.learn-header, header [class*="title"]') ||
    (document.title || '').split('_')[0];
  const root = document.getElementById('courseLearn-inner-box');
  const cards = Array.from(document.querySelectorAll('article.learn-assessment-card'));
  const kindOf = s => /quiz|测验/i.test(s || '') ? '测验' : '作业';
  const rows = cards.map(card => {
    const button = read(card, '.learn-assessment-head, button');
    const html = card.outerHTML.slice(0, 400);
    const panel = (card.querySelector('[aria-controls]')?.getAttribute('aria-controls') || '') + ' ' + html;
    const status = read(card, '.learn-assessment-status, .learn-assessment-state');
    return { name: read(card, '.learn-assessment-title') || read(card, '[class*="title"]'),
      deadline: read(card, '.learn-assessment-deadline') || read(card, '[class*="deadline"]'),
      chapter: read(card.closest('section') || card, '.learn-exam-group-header, [class*="group-header"]'),
      status: (/已提交|已完成|已作答/.test(button + ' ' + status) ? '已提交 ' : '') + (button + ' ' + status).trim(),
      kind: kindOf(panel) };
  });
  if (rows.length) {
    const totals = new Map(), ordinals = new Map();
    rows.forEach(r => { const key = r.chapter + '|' + r.name + '|' + r.kind; totals.set(key,(totals.get(key)||0)+1); });
    rows.forEach(r => { const key = r.chapter + '|' + r.name + '|' + r.kind; ordinals.set(key,(ordinals.get(key)||0)+1); r.duplicate_ordinal = totals.get(key)>1 ? ordinals.get(key) : 0; });
    const section = document.querySelector('a.learn-navigation-link[aria-current="page"]')?.innerText.trim() || '';
    return { layout: 'cards', rows, section, root: true, module: section || '测验与作业', empty: false,
      login: false,
      courseName: courseNameOf() };
  }
  // A SPOC course renders "测验与作业" as an empty panel: its assessments are
  // individual #/learn/hw and #/learn/quiz routes with no list page at all. The
  // one place the course does list them together is the 即将到期 panel on its
  // announcement page ("unit work, due 2026/10/25 23:30"), so that panel is read
  // as the section's list.
  const due = Array.from(document.querySelectorAll('h2.tit')).find(h => h.innerText.trim() === '即将到期');
  const dueRows = (due ? Array.from(due.parentElement.querySelectorAll('a[href*="#/learn/hw?"], a[href*="#/learn/quiz?"], a[href*="#/learn/exam?"]')) : [])
    .map(a => {
      const href = a.getAttribute('href') || '';
      const box = a.closest('li, div, dd, dt, p');
      const text = ((box || a).innerText || '').trim();
      const when = text.match(/(?:截止提交时间|互评截止时间|截止时间|截止日期)\s*[:：]\s*(\d{4}\/\d{1,2}\/\d{1,2}\s+\d{1,2}:\d{2})/);
      return { name: (a.innerText || '').trim(),
        deadline: when ? when[1] : '',
        chapter: '', status: /已提交|已完成/.test(text) ? '已提交' : '',
        kind: /\/quiz\?/.test(href) ? '测验' : (/\/exam\?/.test(href) ? '考试' : '作业'),
        href };
    })
    .filter(row => row.name && row.deadline);
  if (dueRows.length) {
    const totals = new Map(), ordinals = new Map();
    dueRows.forEach(r => { const key = r.chapter + '|' + r.name + '|' + r.kind; totals.set(key,(totals.get(key)||0)+1); });
    dueRows.forEach(r => { const key = r.chapter + '|' + r.name + '|' + r.kind; ordinals.set(key,(ordinals.get(key)||0)+1); r.duplicate_ordinal = totals.get(key)>1 ? ordinals.get(key) : 0; });
    return { layout: 'due', rows: dueRows, section: '测验与作业', root: true, module: '测验与作业',
      empty: false, login: false, courseName: courseNameOf() };
  }
  const legacy = root ? Array.from(root.querySelectorAll('.u-quizHwListItem')).map(e => {
    let status = read(e, '.j-previewStatus');
    const count = read(e, '.j-submitCount').match(/^(\d+)\s*\//);
    if (count) status = Number(count[1]) > 0 ? '已提交' : '未提交';
    const button = read(e, '.titleBox .j-quizBtn');
    if (/已提交|已完成|未提交|未完成/.test(button)) status += ' ' + button;
    return { name: read(e, '.titleBox .j-name'),
      deadline: read(e, '.titleBox .j-submitTime'),
      chapter: read(e.closest('.m-chapterQuizHwItem') || e, '.j-titleName'),
      status: status.trim(), kind: /测验/.test(button) ? '测验' : '作业' };
  }) : [];
  if (legacy.length) {
    const totals = new Map(), ordinals = new Map();
    legacy.forEach(r => { const key = r.chapter + '|' + r.name + '|' + r.kind; totals.set(key,(totals.get(key)||0)+1); });
    legacy.forEach(r => { const key = r.chapter + '|' + r.name + '|' + r.kind; ordinals.set(key,(ordinals.get(key)||0)+1); r.duplicate_ordinal = totals.get(key)>1 ? ordinals.get(key) : 0; });
    return { layout: 'legacy', rows: legacy, section: '测验与作业', root: true,
      module: read(root, '.j-moduleName, h2'), empty: false, login: false,
      courseName: document.querySelector('a[href*="/course/"] img')?.alt ||
        courseNameOf() };
  }
  const empties = root ? Array.from(root.querySelectorAll('.j-empty')) : [];
  const hasSource = root ? !!root.querySelector('.u-moocbl .j-tips') : false;
  const state = document.querySelector('.learn-empty-state-title, [class*="empty-state"]');
  const done = !root && !!state && visible(state) && /暂无|还没有/.test(state.innerText || '');
  return { layout: done ? 'cards' : 'none', rows: [],
    section: document.querySelector('a.learn-navigation-link[aria-current="page"]')?.innerText.trim() || '',
    root: !!root,
    module: root ? read(root, '.j-moduleName, h2') : '',
    empty: done || ((!hasSource || empties.length >= 2) &&
      empties.some(e => visible(e) && /还没有发布测试和作业/.test(e.innerText))),
    login: !root && !done && /登录\s*\|\s*注册|请先登录|登录后/.test(text),
    courseName: document.querySelector('a[href*="/course/"] img')?.alt ||
      (document.title || '').split('_')[0] };
}'''

HOME_CARDS_SCRIPT = r'''() => Array.from(document.querySelectorAll('.j-course-card-box')).map((e,index) => {
  // The card itself is not a link: the whole card is a click target handled by
  // the page's own script, which opens a popup. The one address the markup does
  // carry is an anchor to the course's score page (#/learn/score); it names the
  // course and its term, which is all the reader needs to build the learn URL.
  const anchors = Array.from(e.querySelectorAll('a[href]')).map(a => a.getAttribute('href') || '');
  const learn = anchors.find(h => /^\/(?:spoc\/)?learn\/[A-Za-z0-9_-]+/.test(h)) ||
    anchors.find(h => /^\/(?:spoc\/)?course\/[A-Za-z0-9_-]+/.test(h)) || '';
  return {
    index, name: e.querySelector('.title span.text')?.innerText?.trim() || e.querySelector('img')?.alt || '',
    href: e.getAttribute('href') || '', learn,
    fingerprint: [e.id, e.innerText, e.getAttribute('href') || '', e.querySelector('img')?.src || ''].join('|'),
    finished: /已结束|已关闭/.test(e.querySelector('.course-status')?.innerText || '')
  };
})'''

CATEGORIES_SCRIPT = r'''() => Array.from(document.querySelectorAll('a[href]')).map(e => e.getAttribute('href')).filter(s => s && /^#\/home\/(?:course|spocCourse)$/.test(s))'''

ACCOUNT_SCRIPT = r'''() => {
  // The redesigned course page (…/mooc/learn/…) carries no "个人中心" link at
  // all; it publishes the signed-in user on window.webUser instead, set by the
  // inline business-runtime-globals script. The old page still needs the link.
  const globals = window.webUser || {};
  const fromGlobals = String(globals.userId || globals.id || '');
  const anchor = Array.from(document.querySelectorAll('a[href*="home.htm?userId="]')).find(e => e.innerText.trim() === '个人中心' || e.querySelector('img[alt="用户头像"]'));
  return { id: fromGlobals || (anchor ? new URL(anchor.href, location.href).searchParams.get('userId') : ''),
    name: String(globals.name || globals.nickname || ''),
    login: /登录\s*\|\s*注册|请先登录/.test(document.body.innerText || '') };
}'''


def classify_assignment_page(data):
    if data['rows']:
        if any(not row.get('name') or not row.get('deadline') for row in data['rows']):
            return 'error'
        return 'loaded'
    if data['login']:
        return 'login_required'
    # The new page names its sections in the navigation bar, so the heading test
    # only applies to the old one; requiring it there rejects a page that is
    # plainly showing "暂无考试" and sends the reader looking for a bug.
    heading = data.get('module') or data.get('section') or ''
    if data.get('empty') and (data.get('root') or heading in ('测验与作业', '考试')):
        return 'empty'
    return 'error'
