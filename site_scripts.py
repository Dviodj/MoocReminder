"""DOM selectors verified against the authenticated MOOC and SPOC pages."""

ASSIGNMENTS_SCRIPT = r'''() => {
  const root = document.getElementById('courseLearn-inner-box');
  const text = document.body.innerText || '';
  const visible = e => !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
  const read = (e,s) => e.querySelector(s)?.innerText?.trim() || '';
  const rows = root ? Array.from(root.querySelectorAll('.u-quizHwListItem')).map(e => {
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
  const totals = new Map(), ordinals = new Map();
  rows.forEach(r => { const key = r.chapter + '|' + r.name + '|' + r.kind; totals.set(key,(totals.get(key)||0)+1); });
  rows.forEach(r => { const key = r.chapter + '|' + r.name + '|' + r.kind; ordinals.set(key,(ordinals.get(key)||0)+1); r.duplicate_ordinal = totals.get(key)>1 ? ordinals.get(key) : 0; });
  const empties = root ? Array.from(root.querySelectorAll('.j-empty')) : [];
  const hasSource = root ? !!root.querySelector('.u-moocbl .j-tips') : false;
  return { rows, root: !!root,
    module: root ? read(root, '.j-moduleName, h2') : '',
    empty: (!hasSource || empties.length >= 2) && empties.some(e => visible(e) && /还没有发布测试和作业/.test(e.innerText)),
    login: !root && /登录\s*\|\s*注册|请先登录|登录后/.test(text),
    courseName: document.querySelector('a[href*="/course/"] img')?.alt ||
      (document.title || '').split('_')[0] };
}'''

HOME_CARDS_SCRIPT = r'''() => Array.from(document.querySelectorAll('.j-course-card-box')).map((e,index) => ({
  index, name: e.querySelector('.title span.text')?.innerText?.trim() || e.querySelector('img')?.alt || '',
  href: e.getAttribute('href') || '',
  fingerprint: [e.id, e.innerText, e.getAttribute('href') || '', e.querySelector('img')?.src || ''].join('|'),
  finished: /已结束|已关闭/.test(e.querySelector('.course-status')?.innerText || '')
}))'''

CATEGORIES_SCRIPT = r'''() => Array.from(document.querySelectorAll('a[href]')).map(e => e.getAttribute('href')).filter(s => s && /^#\/home\/(?:course|spocCourse)$/.test(s))'''

ACCOUNT_SCRIPT = r'''() => {
  const anchor = Array.from(document.querySelectorAll('a[href*="home.htm?userId="]')).find(e => e.innerText.trim() === '个人中心' || e.querySelector('img[alt="用户头像"]'));
  return { id: anchor ? new URL(anchor.href, location.href).searchParams.get('userId') : '',
    login: /登录\s*\|\s*注册|请先登录/.test(document.body.innerText || '') };
}'''


def classify_assignment_page(data):
    if data['rows']:
        if any(not row.get('name') or not row.get('deadline') for row in data['rows']):
            return 'error'
        return 'loaded'
    if data['login']:
        return 'login_required'
    if data['root'] and data['module'] == '测验与作业' and data['empty']:
        return 'empty'
    return 'error'
