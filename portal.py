"""Interface state machine and reminder rules; browser operations stay on their worker."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import queue
import threading
import time
import uuid

from core import (now_beijing, due_reminders, due_digests, remaining_text, account_id,
                  normalize_home_url, home_url_problem, course_url_problem)
from storage import normalize_email_settings
from worker import BrowserWorker

import emailer


class Controller:
    def __init__(self, store, auto_scan=True, context_provider=None, profile_dir=None):
        self.store = store
        self.context_provider = context_provider
        self.lock = threading.RLock()
        self.events = queue.Queue()
        self.worker = BrowserWorker(store.folder, self.events, context_provider=context_provider)
        self.worker.start()
        # The shipped sender settings live next to the program, not in the data
        # folder, so a user only has to give their own address.
        self.mailer = emailer.Mailer(log=self.log_email, on_result=self.record_email,
                                     folder=Path(__file__).parent)
        self.mailer.start()
        self.email_message = ''
        self.busy = False
        self.connected = False
        self.paused = False
        self.stopping = False
        self.phase = '连接慕课后，作业会自动整理到这里。'
        self.job = ''
        self.job_started = 0
        self.notifications = []
        self.pending_popups = []
        self.next_scan = time.monotonic() + self.interval()
        self.profile_dir = profile_dir or ('web-profiles' if context_provider else 'native-profiles')
        self.auto_scan = auto_scan
        if auto_scan and (store.folder / self.profile_dir / account_id(store.data['settings']['home_url'])).exists():
            self.next_scan = time.monotonic() + 5

    def replace_store(self, store):
        """Switch to another account's profile without restarting the program.

        The browser worker is restarted so it releases the previous account's
        cookies, but the embedded browser itself stays open, so a profile that
        is already logged in comes back without signing in again.
        """
        with self.lock:
            previous = self.worker
            previous.stop()
            previous.join(timeout=5)
            self.store = store
            # A fresh queue, so events from the account we just left are dropped.
            self.events = queue.Queue()
            self.worker = BrowserWorker(store.folder, self.events, context_provider=self.context_provider)
            self.worker.start()
            self.busy = False
            self.job = ''
            self.connected = False
            self.email_message = ''
            self.phase = '已切换到' + (store.data['settings'].get('name') or '另一个账号') + '，正在恢复上次结果。'
            self.next_scan = time.monotonic() + self.interval()
            profile_exists = (store.folder / self.profile_dir /
                              account_id(store.data['settings']['home_url'])).exists()
            if profile_exists:
                self.next_scan = time.monotonic() + 5
            return profile_exists

    def interval(self):
        return self.store.data['settings']['interval_minutes'] * 60

    def submit(self, job, extra=None):
        with self.lock:
            if self.busy:
                raise ValueError('当前操作还在进行，可以取消后重试。')
            self.busy = True
            self.job = job
            self.job_started = time.monotonic()
            self.phase = ('正在打开登录网页…' if job == 'login'
                          else '正在用粘贴的 Cookie 登录…' if job == 'cookie_login'
                          else '正在整理课程与作业…')
            settings = deepcopy(self.store.data['settings'])
            if extra:
                settings.update(extra)
            self.worker.commands.put((job, settings))

    def tick(self):
        with self.lock:
            while True:
                try:
                    kind, value = self.events.get_nowait()
                except queue.Empty:
                    break
                if kind == 'progress' and self.busy:
                    self.phase = value
                elif kind == 'login_ready' and self.busy and self.job == 'login':
                    self.busy = False
                    self.phase = '登录网页已打开。完成登录后，点击“检查作业”。'
                    self.job = 'waiting_login'
                elif kind == 'cookie_ready' and self.busy and self.job == 'cookie_login':
                    self.busy = False
                    self.connected = True
                    self.job = 'ready'
                    self.phase = f'已用粘贴的 Cookie 登录（{value} 项），正在检查作业…'
                    self.next_scan = time.monotonic()
                elif kind == 'error':
                    self.busy = False
                    self.job = 'error'
                    self.phase = '连接失败：' + value.splitlines()[0]
                    self.store.data['errors'] = [value]
                    self.store.save()
                    self.next_scan = time.monotonic() + self.interval()
                elif kind == 'result':
                    self.busy = False
                    self.next_scan = time.monotonic() + self.interval()
                    if value.account_id and value.account_id != account_id(self.store.data['settings']['home_url']):
                        self.phase = '账号已切换，请重新连接慕课。'
                        continue
                    self.store.merge_scan(value.items, value.successful_urls)
                    courses = {c['url']: c for c in self.store.data['courses']}
                    courses.update({c.url: {'name': c.name, 'url': c.url} for c in value.courses})
                    self.store.data.update(courses=list(courses.values()), errors=value.errors,
                        last_scan=now_beijing().isoformat(), successful_urls=value.successful_urls)
                    self.store.save()
                    self.connected = bool(value.successful_urls)
                    self.job = 'ready' if self.connected else 'error'
                    self.phase = f'已更新 {len(value.successful_urls)} 门课程 · {len(value.items)} 项作业与测验'
                    if value.errors:
                        self.phase += f' · {len(value.errors)} 项需要检查'
            if self.busy and self.job == 'login' and self.job_started and time.monotonic() - self.job_started > 50:
                self.worker.cancel()
                self.phase = '操作耗时过长，正在取消。稍后可以重新连接。'
            if not self.paused and not self.stopping:
                state = deepcopy(self.store.data['reminders'])
                active = self.store.active_items()
                alerts = due_reminders(active, self.store.data['settings']['reminder_hours'], state, now_beijing())
                if alerts:
                    self.add_notification(alerts)
                    self.store.data['reminders'] = state
                digest_due = due_digests(self.store.data['settings']['email'].get('digest_times'),
                                         self.store.data.setdefault('digest_state', {}), now_beijing())
                self.dispatch_email(alerts, digest_due, active)
                self.store.save()
                profile_exists = (self.store.folder / self.profile_dir / account_id(self.store.data['settings']['home_url'])).exists()
                if (self.connected or (self.auto_scan and profile_exists)) and not self.busy and time.monotonic() >= self.next_scan:
                    self.submit('scan')

    def email_config(self):
        with self.lock:
            return deepcopy(self.store.data['settings'].get('email') or {})

    def log_email(self, message):
        with self.lock:
            self.email_message = str(message)

    def record_email(self, key, ok, detail):
        with self.lock:
            attempts = int(self.store.data['email_log'].get('attempt|' + key, '0') or 0)
            # The reason is kept next to the result so the send log can explain
            # a failure instead of only showing that one happened.
            note = ' '.join(str(detail or '').split())[:160]
            self.store.data['email_log'][key] = (('sent ' if ok else 'failed ')
                + now_beijing().isoformat(timespec='seconds') + (f' · {note}' if note else ''))
            if not ok:
                self.store.data['email_log']['attempt|' + key] = str(attempts + 1)
                self.email_message = detail or '邮件发送失败，请查看连接详情。'
            else:
                self.store.data['email_log'].pop('attempt|' + key, None)
            # Only the size cap applies here. Passing this one revision would
            # delete the history of every other task's reminders, which is what
            # `merge_scan` may do but only with the full set of live revisions.
            self.store.prune_email_log()
            self.store.save()

    @staticmethod
    def _mail_settled(log, key):
        """Whether this key is done with: already sent, or out of attempts.

        A plain `key in log` check counted a *failed* send as settled, so the
        recorded attempt counter never got a chance to retry and one transient
        SMTP error lost the reminder for good.
        """
        if str(log.get(key, '')).startswith('sent '):
            return True
        return int(log.get('attempt|' + key, '0') or 0) >= 3

    def dispatch_email(self, alerts, digest_due, active):
        """Queue the emails that this tick makes due; never blocks on delivery."""
        config = self.store.data['settings'].get('email') or {}
        if not config.get('enabled') or not str(config.get('recipient', '')).strip():
            return False
        log = self.store.data['email_log']
        queued = False
        for window in sorted({window for _, window in alerts or []}):
            group = [item for item, item_window in alerts or [] if item_window == window]
            key = f'alert|{group[0].revision}|{window}'
            if self._mail_settled(log, key):
                continue
            queued = self.mailer.enqueue(key, deepcopy(config), emailer.compose_alert(group, window, now_beijing())) or queued
        for stamp in digest_due or []:
            key = f'digest|{now_beijing().strftime("%Y-%m-%d")}|{stamp}'
            if self._mail_settled(log, key):
                continue
            pending = [item for item in active if not item.deadline or item.deadline > now_beijing()]
            if not pending:
                continue
            queued = self.mailer.enqueue(key, deepcopy(config), emailer.compose_digest(pending, now_beijing())) or queued
        return queued

    def send_test_email(self, config):
        """Validate and send one message so the user can verify the setup."""
        if not config.get('enabled') or not str(config.get('recipient', '')).strip():
            raise ValueError('请先填写接收提醒的邮箱并启用邮件提醒。')
        # A credential the user stored, or the one shipped with the program.
        if not emailer.resolve_password(config, Path(__file__).parent):
            raise ValueError('邮件发送账号尚未配置，请在高级选项里填写邮箱授权码。')
        now = now_beijing()
        mail = emailer.Mail(subject='【测试】MoocReminder 邮件提醒已连接',
            text='这是一封测试邮件。\n\n收到它说明邮箱设置正确：作业临近截止时，提醒会发到这个地址。\n\n'
                 f'发送时间：{now.strftime("%Y-%m-%d %H:%M")}\n—— 由 MoocReminder 自动发送，请勿直接回复。',
            html='<div style="font-family:Microsoft YaHei UI,Segoe UI,sans-serif;padding:20px;color:#28364c">'
                 '<h2 style="margin:0 0 10px">邮件提醒已连接</h2>'
                 '<p style="color:#7e8da3;margin:0 0 12px">这是一封测试邮件。作业临近截止时，提醒会发到这个地址。</p>'
                 f'<p style="color:#a3adbe;font-size:12px;margin:0">发送时间：{now.strftime("%Y-%m-%d %H:%M")}</p></div>')
        self.mailer.enqueue('test|' + uuid.uuid4().hex, deepcopy(config), mail)
        return True

    def add_notification(self, alerts=None, test=False):
        with self.lock:
            message = {'id': uuid.uuid4().hex, 'test': test, 'time': now_beijing().isoformat(),
                'items': [{'course': item.course, 'name': item.name, 'deadline': item.deadline.strftime('%m月%d日 %H:%M'),
                           'remaining': remaining_text(item, now_beijing()), 'url': item.course_url} for item, _ in alerts or []]}
            self.notifications.append(message)
            self.notifications = self.notifications[-20:]
            self.pending_popups.append(message)
            return message

    def snapshot(self):
        with self.lock:
            now = now_beijing()
            completed = set(self.store.data['completed'])
            items = []
            for item in sorted(self.store.items(), key=lambda item: (item.deadline is None, item.deadline or now, item.course, item.name)):
                hours = (item.deadline - now).total_seconds() / 3600 if item.deadline else None
                done = item.revision in completed or item.status == 'completed'
                data = item.to_dict()
                data.update(remaining=remaining_text(item, now), remaining_hours=hours, done=done,
                    expired=hours is not None and hours <= 0, local_completed=item.revision in completed,
                    deadline_label=item.deadline.strftime('%m月%d日 %H:%M') if item.deadline else '待核对',
                    status_label='已完成' if done else '未提交' if item.status == 'pending' else '请核对')
                items.append(data)
            active = [item for item in items if not item['done'] and not item['expired']]
            return dict(items=items, summary=dict(pending=len(active), soon=sum(item['remaining_hours'] is not None and item['remaining_hours'] <= 72 for item in active),
                courses=len(self.store.data['courses'])), settings=deepcopy(self.store.data['settings']),
                busy=self.busy, connected=self.connected, paused=self.paused, phase=self.phase, job=self.job,
                elapsed=round(time.monotonic() - self.job_started) if self.busy else 0,
                next_check=max(0, int(self.next_scan - time.monotonic())), last_scan=self.store.data['last_scan'],
                errors=list(self.store.data['errors']), courses=deepcopy(self.store.data['courses']),
                notifications=deepcopy(self.notifications), data_folder=str(self.store.folder),
                email_log=deepcopy(self.store.data['email_log']), email_status=self.email_message)

    def settings(self, patch):
        """Apply a settings patch and report what could not be used.

        Returns a list of warnings (empty when everything applied). Only a hard
        problem in the account fields raises; a mail account that is not ready
        yet must not throw away the rest of the patch, because the user pastes
        the course link and the mailbox in the same dialog.
        """
        with self.lock:
            settings = {**self.store.data['settings'], **patch}
            home_url = normalize_home_url(settings['home_url'])
            if not home_url:
                raise ValueError(home_url_problem(settings['home_url']))
            settings['home_url'] = home_url
            if not isinstance(settings['interval_minutes'], int) or not 5 <= settings['interval_minutes'] <= 1440:
                raise ValueError('检查间隔请填写 5–1440 分钟。')
            if not isinstance(settings['reminder_hours'], list) or not settings['reminder_hours'] or any(not isinstance(h, (int, float)) or not 0 < h <= 8760 for h in settings['reminder_hours']):
                raise ValueError('请填写有效的提前提醒小时数。')
            if not isinstance(settings['manual_urls'], list):
                raise ValueError('补充作业链接格式不正确。')
            for url in settings['manual_urls']:
                problem = course_url_problem(url) if isinstance(url, str) else '补充作业链接需要是文字。'
                if problem:
                    raise ValueError(problem)
            settings['manual_urls'] = [normalize_course_url(u) for u in settings['manual_urls']]
            if not all(isinstance(settings[k], bool) for k in ['include_quizzes', 'include_finished']):
                raise ValueError('提醒选项无效。')
            # The password the user already saved has to come from storage, not
            # from the merged patch: the form omits it whenever the user did not
            # retype it, and reading the merged value would wipe the saved code.
            stored = self.store.data['settings'].get('email') or {}
            incoming = patch.get('email')
            email = normalize_email_settings({'email': stored if incoming is None else incoming}, stored)
            warnings = []
            if email['enabled']:
                try:
                    # The shipped sender settings fill in whatever the user left blank.
                    emailer.validate(email, emailer.resolve_password(email, Path(__file__).parent),
                                     Path(__file__).parent)
                except emailer.EmailError as exc:
                    # Keep what the user typed so it can be corrected, but never
                    # leave a reminder switched on that cannot be delivered.
                    email['enabled'] = False
                    warnings.append(f'邮件提醒尚未生效：{exc}')
            settings = {**settings, 'email': email}
            changed = self.store.update_settings(settings)
            self.next_scan = time.monotonic() + self.interval()
            if changed:
                self.connected = False
                self.phase = '已切换账号，请重新连接慕课。'
            return warnings

    def stop(self):
        self.stopping = True
        self.worker.stop()
        self.mailer.stop()
