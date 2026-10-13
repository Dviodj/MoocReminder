"""Atomic local persistence, including failed-course cache retention."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys
import uuid

from core import Assignment, DEFAULT_SETTINGS, account_id, valid_home_url


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


class Store:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else default_data_dir()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / 'data.json'
        self.warning = ''
        self.data = dict(settings=deepcopy(DEFAULT_SETTINGS), assignments=[], reminders={}, completed=[],
                         courses=[], last_scan='', errors=[], successful_urls=[])
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(saved, dict):
                    raise ValueError('Invalid settings data')
                for row in saved.get('assignments', []):
                    Assignment.from_dict(row)
                candidate = {**self.data, **saved}
                candidate['settings'] = {**deepcopy(DEFAULT_SETTINGS), **saved.get('settings', {})}
                settings = candidate['settings']
                if (not isinstance(settings['home_url'], str) or not valid_home_url(settings['home_url']) or
                    not isinstance(settings['interval_minutes'], int) or not 5 <= settings['interval_minutes'] <= 1440 or
                    not isinstance(settings['reminder_hours'], list) or not settings['reminder_hours'] or
                    any(not isinstance(h, (int, float)) or not 0 < h <= 8760 for h in settings['reminder_hours']) or
                    not isinstance(settings['manual_urls'], list) or any(not isinstance(u, str) for u in settings['manual_urls']) or
                    not isinstance(candidate['completed'], list) or not isinstance(candidate['reminders'], dict) or
                    not isinstance(candidate['courses'], list) or not isinstance(candidate['errors'], list)):
                    raise ValueError('本地设置格式无效')
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
        self.save()

    def update_settings(self, settings):
        changed_account = account_id(settings['home_url']) != account_id(self.data['settings']['home_url'])
        if changed_account:
            self.data.update(assignments=[], reminders={}, completed=[], courses=[], last_scan='', errors=[], successful_urls=[])
        self.data['settings'] = deepcopy(settings)
        self.save()
        return changed_account
