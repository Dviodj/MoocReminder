"""Atomic local persistence, including failed-course cache retention."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys
import uuid

from core import Assignment, DEFAULT_SETTINGS, account_id, binding_state, valid_home_url
import emailer
import secrets_store


def default_data_dir(portable_base=None):
    base = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))
    candidate = base / 'MoocDeadlineReminder'
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        # On Windows tempfile retries permission failures as name collisions.
        # Probe once so a restricted AppData folder cannot stall startup.
        probe = candidate/('.write-probe-'+uuid.uuid4().hex)
        descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.close(descriptor)
        finally:
            probe.unlink(missing_ok=True)
        return candidate
    except OSError:
        if portable_base is None:
            portable_base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).parent
        return Path(portable_base) / 'data'


def normalize_email_settings(patch, current=None):
    """Merge an email patch, keep the stored password unless a new one arrives."""
    current = dict(current or {})
    incoming = patch.get('email')
    if not isinstance(incoming, dict):
        raise ValueError('邮件设置格式不正确。')
    merged = {**current, **incoming}
    password = str(merged.get('password', '') or '')
    if not password or not secrets_store.protected(password):
        # Empty means "keep the saved one"; anything else is a fresh secret.
        merged['password'] = secrets_store.encrypt(password) if password else str(current.get('password', '') or '')
    try:
        settings = emailer.EmailSettings.from_settings({'email': merged})
    except (TypeError, ValueError) as exc:
        raise ValueError('邮件设置格式不正确。') from exc
    if not isinstance(settings.enabled, bool) or not isinstance(settings.port, int) or not 1 <= settings.port <= 65535:
        raise ValueError('邮件设置格式不正确。')
    if settings.security not in ('ssl', 'starttls'):
        raise ValueError('邮件加密方式无效。')
    times = emailer.valid_digest_times(incoming.get('digest_times', current.get('digest_times', [])))
    if times is None:
        raise ValueError('发信时刻请填写 HH:MM，例如 08:00 或 08:00,20:00。')
    merged['digest_times'] = times
    # Remember whether the user chose to configure the sender themselves.
    advanced = incoming.get('advanced', current.get('advanced', False))
    return dict(enabled=settings.enabled, recipient=settings.recipient.strip(), sender=settings.sender.strip(),
                server=settings.server.strip(), port=settings.port, security=settings.security,
                password=merged['password'], digest_times=times, advanced=bool(advanced))

class Store:
    def __init__(self, folder=None, settings_base=None):
        self.folder = Path(folder) if folder else default_data_dir()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'data.json'
        # Settings shared by every account; a profile only overrides its own.
        self.base_settings = deepcopy(settings_base) if settings_base else {}
        self.warning = ''
        self.data = dict(settings=deepcopy(DEFAULT_SETTINGS), assignments=[], reminders={}, completed=[],
                         courses=[], last_scan='', errors=[], successful_urls=[], email_log={})
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(saved, dict):
                    raise ValueError('Invalid settings data')
                for row in saved.get('assignments', []):
                    Assignment.from_dict(row)
                candidate = {**self.data, **saved}
                candidate['settings'] = {**deepcopy(DEFAULT_SETTINGS), **self.base_settings, **saved.get('settings', {})}
                settings = candidate['settings']
                if (not isinstance(settings['home_url'], str) or not valid_home_url(settings['home_url']) or
                    not isinstance(settings['interval_minutes'], int) or not 5 <= settings['interval_minutes'] <= 1440 or
                    not isinstance(settings['reminder_hours'], list) or not settings['reminder_hours'] or
                    any(not isinstance(h, (int, float)) or not 0 < h <= 8760 for h in settings['reminder_hours']) or
                    not isinstance(settings['manual_urls'], list) or any(not isinstance(u, str) for u in settings['manual_urls']) or
                    not isinstance(candidate['completed'], list) or not isinstance(candidate['reminders'], dict) or
                    not isinstance(candidate['courses'], list) or not isinstance(candidate['errors'], list)):
                    raise ValueError('本地设置格式无效')
                normalize_email_settings({'email': candidate['settings'].get('email', {})})
                if not isinstance(candidate.get('email_log', {}), dict) or any(
                        not isinstance(key, str) or not isinstance(value, str) for key, value in candidate.get('email_log', {}).items()):
                    raise ValueError('本地邮件发送记录格式无效')
                for key in ['completed', 'successful_urls', 'errors']:
                    if not isinstance(candidate[key], list) or any(not isinstance(v, str) for v in candidate[key]):
                        raise ValueError(f'本地 {key} 记录格式无效')
                for key, windows in candidate['reminders'].items():
                    if not isinstance(key, str) or not isinstance(windows, list) or any(not isinstance(h, (int, float)) or not 0 < h <= 8760 for h in windows):
                        raise ValueError('本地提醒记录格式无效')
                for course in candidate['courses']:
                    if not isinstance(course, dict) or not isinstance(course.get('name'), str) or not isinstance(course.get('url'), str):
                        raise ValueError('本地课程记录格式无效')
                if not isinstance(candidate['last_scan'], str):
                    raise ValueError('本地检查时间格式无效')
                if any(item.status not in ['pending', 'completed', 'unknown'] for item in [Assignment.from_dict(r) for r in candidate['assignments']]):
                    raise ValueError('本地作业状态无效')
                self.data = candidate
            except (ValueError, TypeError, KeyError) as exc:
                shutil.copy2(self.path, self.folder / 'data.corrupt.backup.json')
                self.warning = f'本地记录损坏，已备份并恢复默认设置：{exc}'

    def save(self):
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.folder, suffix='.tmp', delete=False) as f:
            temp = Path(f.name)
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        try:
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def items(self):
        return [Assignment.from_dict(row) for row in self.data['assignments']]

    def active_items(self):
        completed = set(self.data['completed'])
        quizzes = self.data['settings']['include_quizzes']
        return [item for item in self.items() if item.revision not in completed and
                (quizzes or item.kind != '测验')]

    def binding_state(self):
        """Whether this account has bound both its MOOC course page and its email."""
        return binding_state(self.data['settings'])

    def is_bound(self):
        return all(self.binding_state().values())

    def mark_completed(self, item, completed):
        revisions = set(self.data['completed'])
        if completed:
            revisions.add(item.revision)
        else:
            revisions.discard(item.revision)
        self.data['completed'] = sorted(revisions)
        self.save()

    def merge_scan(self, new_items, successful_urls):
        success = set(successful_urls)
        by_id = {item.uid: item for item in self.items() if item.course_url not in success}
        by_id.update({item.uid: item for item in new_items})
        self.data['assignments'] = [item.to_dict() for item in by_id.values()]
        revisions = {item.revision for item in by_id.values()}
        self.data['completed'] = [key for key in self.data['completed'] if key in revisions]
        self.data['reminders'] = {key: value for key, value in self.data['reminders'].items() if key in revisions}
        self.prune_email_log(revisions)
        self.save()

    def prune_email_log(self, revisions=None):
        """Keep send history bounded, and drop entries for vanished tasks."""
        log = self.data['email_log']
        if revisions is not None:
            log = {key: value for key, value in log.items()
                   if not key.startswith('alert|') or key.split('|')[1] in revisions}
        if len(log) > 500:
            log = dict(sorted(log.items(), key=lambda pair: str(pair[1]))[-500:])
        self.data['email_log'] = log

    def update_settings(self, settings):
        """Merge a settings patch; only a changed MOOC account clears the cache."""
        current = self.data['settings']
        merged = {**deepcopy(current), **deepcopy(settings)}
        changed_account = account_id(merged['home_url']) != account_id(current['home_url'])
        if changed_account:
            # The stored tasks and marks belong to the previous MOOC account.
            self.data.update(assignments=[], reminders={}, completed=[], courses=[], last_scan='', errors=[], successful_urls=[])
        if current.get('email', {}).get('recipient') != merged.get('email', {}).get('recipient'):
            # A new recipient deserves a fresh set of reminders.
            self.data['email_log'] = {}
        self.data['settings'] = merged
        self.save()
        return changed_account
