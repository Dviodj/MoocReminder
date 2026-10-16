"""Read the stored send log.

`portal.record_email` keeps one line per attempted message inside
`data['email_log']`. Those keys are internal (`alert|<revision>|<window>`,
`digest|<date>|<time>`, `test|<uuid>`, plus `attempt|` retry counters), so this
module turns them into rows a person can read without knowing the naming.

Kept free of Qt and of the network so it can be tested on its own.
"""
from datetime import datetime

RESULT_LABELS = {'sent': '已发送', 'failed': '发送失败'}
UNKNOWN_RESULT = '已记录'
# Bookkeeping the program writes into the same dict but that is not a delivery:
# `attempt|` counts retries, and `due|` marks a summary as already handled today
# (older versions stored those markers in `email_log`).
HIDDEN_PREFIXES = ('attempt|', 'due|')


def _split(value):
    """Split `'sent 2026-02-14T08:00:00+08:00 · smtp timeout'` into parts."""
    status, _, rest = str(value or '').partition(' ')
    rest = rest.strip()
    detail = ''
    if ' · ' in rest:
        rest, _, detail = rest.partition(' · ')
    return status.strip(), rest.strip(), detail.strip()


def _kind(key):
    """A readable type and note for one log key."""
    parts = str(key).split('|')
    if parts[0] == 'alert':
        note = ''
        if len(parts) > 2:
            try:
                note = f'提前 {float(parts[2]):g} 小时'
            except ValueError:
                note = ''
        return '作业提醒', note
    if parts[0] == 'digest':
        stamp = parts[2] if len(parts) > 2 else ''
        return '每日汇总', f'计划 {stamp}' if stamp else ''
    if parts[0] == 'test':
        return '测试邮件', ''
    return str(key), ''


def _moment(text):
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def entries(log, limit=200):
    """Newest first rows: key, kind, result, ok, failed, when, note."""
    rows = []
    for key, value in dict(log or {}).items():
        key = str(key)
        if key.startswith(HIDDEN_PREFIXES):
            continue
        status, stamp, detail = _split(value)
        kind, note = _kind(key)
        moment = _moment(stamp)
        rows.append(dict(
            key=key, kind=kind,
            result=RESULT_LABELS.get(status, UNKNOWN_RESULT),
            ok=status == 'sent', failed=status == 'failed',
            when=moment.strftime('%m月%d日 %H:%M') if moment else (stamp or '—'),
            sort=moment.timestamp() if moment else 0.0,
            note=' · '.join(part for part in (note, detail) if part)))
    rows.sort(key=lambda row: row['sort'], reverse=True)
    return rows[:limit]


def summary(log):
    """Counts for the heading: how many sent, how many failed."""
    rows = [row for row in entries(log, limit=None) if not row['key'].startswith('test|')]
    failed = sum(1 for row in rows if row['failed'])
    return dict(total=len(rows), failed=failed, sent=sum(1 for row in rows if row['ok']))
