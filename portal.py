"""Responsive local interface service; browser operations remain on their worker."""
from copy import deepcopy
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import queue
import secrets
import threading
import time
import uuid

from core import now_beijing, due_reminders, remaining_text, account_id, valid_home_url, normalize_course_url
from worker import BrowserWorker

ASSETS = Path(__file__).parent / 'web'


class Controller:
    def __init__(self, store, auto_scan=True, context_provider=None, profile_dir=None):
        self.store = store
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.events = queue.Queue()
        self.worker = BrowserWorker(store.folder, self.events, context_provider=context_provider)
        self.worker.start()
        self.busy = False
        self.connected = False
        self.paused = False
        self.stopping = False
        self.phase = '连接慕课后，作业会自动整理到这里。'
        self.job = ''
        self.job_started = 0
        self.notifications = []
        self.pending_popups = []
        self.closed_at = None
        self.next_scan = time.monotonic() + self.interval()
        self.profile_dir = profile_dir or ('web-profiles' if context_provider else 'native-profiles')
        self.auto_scan = auto_scan
        if auto_scan and (store.folder / self.profile_dir / account_id(store.data['settings']['home_url'])).exists():
            self.next_scan = time.monotonic() + 5

    def interval(self):
        return self.store.data['settings']['interval_minutes'] * 60

    def submit(self, job):
        with self.lock:
            if self.busy:
                raise ValueError('当前操作还在进行，可以取消后重试。')
            self.busy = True
            self.job = job
            self.job_started = time.monotonic()
            self.phase = '正在打开登录网页…' if job == 'login' else '正在整理课程与作业…'
            self.worker.commands.put((job, deepcopy(self.store.data['settings'])))

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
                alerts = due_reminders(self.store.active_items(), self.store.data['settings']['reminder_hours'], state, now_beijing())
                if alerts:
                    self.add_notification(alerts)
                    self.store.data['reminders'] = state
                    self.store.save()
                profile_exists = (self.store.folder / self.profile_dir / account_id(self.store.data['settings']['home_url'])).exists()
                if (self.connected or (self.auto_scan and profile_exists)) and not self.busy and time.monotonic() >= self.next_scan:
                    self.submit('scan')

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
                notifications=deepcopy(self.notifications), data_folder=str(self.store.folder))

    def settings(self, patch):
        with self.lock:
            settings = {**self.store.data['settings'], **patch}
            if not isinstance(settings['home_url'], str) or not valid_home_url(settings['home_url']):
                raise ValueError('课程主页链接需要包含你的 userId。')
            if not isinstance(settings['interval_minutes'], int) or not 5 <= settings['interval_minutes'] <= 1440:
                raise ValueError('检查间隔请填写 5–1440 分钟。')
            if not isinstance(settings['reminder_hours'], list) or not settings['reminder_hours'] or any(not isinstance(h, (int, float)) or not 0 < h <= 8760 for h in settings['reminder_hours']):
                raise ValueError('请填写有效的提前提醒小时数。')
            if not isinstance(settings['manual_urls'], list) or any(not isinstance(u, str) or not normalize_course_url(u) for u in settings['manual_urls']):
                raise ValueError('作业链接需要来自慕课官网，并包含学期 tid。')
            if not all(isinstance(settings[k], bool) for k in ['include_quizzes', 'include_finished']):
                raise ValueError('提醒选项无效。')
            changed = self.store.update_settings(settings)
            self.next_scan = time.monotonic() + self.interval()
            if changed:
                self.connected = False
                self.phase = '已切换账号，请重新连接慕课。'

    def stop(self):
        self.stopping = True
        self.worker.stop()


def create_server(controller, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, status, data):
            content = json.dumps(data, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(content)

        def allowed(self):
            return secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + controller.token)

        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/api/state':
                if not self.allowed():
                    return self.send_json(401, {'error': '未授权'})
                controller.closed_at = None
                return self.send_json(200, controller.snapshot())
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.css': ('app.css', 'text/css; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8')}
            if path not in files:
                return self.send_json(404, {'error': '页面不存在'})
            name, kind = files[path]
            content = (ASSETS / name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                if length > 64000:
                    return self.send_json(413, {'error': '请求过大'})
                data = json.loads(self.rfile.read(length) or b'{}')
                origin = self.headers.get('Origin')
                expected_origin = f'http://127.0.0.1:{self.server.server_port}'
                if origin and origin != expected_origin:
                    return self.send_json(403, {'error': '不允许来自其他网页的操作'})
                if self.path == '/api/closing' and origin == expected_origin and isinstance(data, dict) and data.get('token') == controller.token:
                    controller.closed_at = time.monotonic()
                    return self.send_json(200, {'ok': True})
                if not self.allowed():
                    return self.send_json(401, {'error': '未授权'})
                if not isinstance(data, dict):
                    raise ValueError('请求格式不正确')
                with controller.lock:
                    if self.path in ['/api/login', '/api/scan']:
                        controller.submit('login' if self.path == '/api/login' else 'scan')
                        return self.send_json(202, {'ok': True})
                    if self.path == '/api/cancel':
                        controller.worker.cancel()
                        controller.phase = '正在取消当前操作…'
                    elif self.path == '/api/settings':
                        controller.settings(data)
                    elif self.path == '/api/pause':
                        controller.paused = bool(data.get('paused'))
                    elif self.path == '/api/complete':
                        item = next((item for item in controller.store.items() if item.uid == data.get('uid')), None)
                        if not item:
                            raise ValueError('没有找到这项作业')
                        controller.store.mark_completed(item, bool(data.get('completed')))
                    elif self.path == '/api/test-notification':
                        controller.add_notification(test=True)
                    elif self.path == '/api/quit':
                        controller.stop()
                    else:
                        return self.send_json(404, {'error': '操作不存在'})
                return self.send_json(200, {'ok': True})
            except (ValueError, TypeError, KeyError, OSError) as exc:
                return self.send_json(400, {'error': str(exc)})
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server
