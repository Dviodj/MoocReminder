"""Email reminder channel; standard library only, no browser or GUI imports.

The transport is deliberately narrow: `Notifier` is the extension point, and
`SmtpNotifier` is its first implementation. A future relay or HTTP API can be
added by implementing the same `send` signature without touching callers.
"""
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate
import re
import smtplib
import ssl
import queue
import threading

import secrets_store

# Token replaced by the calling layer when it renders an item list.
DEFAULT_PORT = 465

# Providers most Chinese users reach for. Ports verified against each service:
# 465 uses implicit TLS, 587 upgrades with STARTTLS.
PRESETS = {
    'qq.com': dict(server='smtp.qq.com', port=465, security='ssl'),
    '163.com': dict(server='smtp.163.com', port=465, security='ssl'),
    '126.com': dict(server='smtp.126.com', port=465, security='ssl'),
    'outlook.com': dict(server='smtp.office365.com', port=587, security='starttls'),
    'hotmail.com': dict(server='smtp.office365.com', port=587, security='starttls'),
    'aliyun.com': dict(server='smtp.aliyun.com', port=465, security='ssl'),
    'foxmail.com': dict(server='smtp.qq.com', port=465, security='ssl'),
}

SECTION_LABELS = {'作业': '作业', '测验': '测验'}


class EmailError(RuntimeError):
    """A user-actionable delivery failure, already phrased for the interface."""


@dataclass
class EmailSettings:
    """Validated email configuration; the password arrives already decrypted."""
    enabled: bool = False
    recipient: str = ''
    sender: str = ''
    server: str = ''
    port: int = DEFAULT_PORT
    security: str = 'ssl'
    password: str = ''
    digest_times: list = field(default_factory=list)

    @classmethod
    def from_settings(cls, settings, password=''):
        email = settings.get('email') or {}
        return cls(enabled=bool(email.get('enabled')), recipient=str(email.get('recipient', '')),
            sender=str(email.get('sender', '')), server=str(email.get('server', '')),
            port=int(email.get('port', DEFAULT_PORT)), security=str(email.get('security', 'ssl')),
            password=password, digest_times=list(email.get('digest_times') or []))


@dataclass
class Mail:
    subject: str
    text: str
    html: str = ''


def suggest_server(address):
    """Return a provider preset for an address, or an empty dict."""
    match = re.search(r'@([^@\s]+)$', (address or '').strip())
    if not match:
        return {}
    domain = match.group(1).lower()
    if domain in PRESETS:
        return dict(PRESETS[domain])
    for suffix, preset in PRESETS.items():
        if domain.endswith('.' + suffix):
            return dict(preset)
    return {}


def valid_address(address):
    return bool(re.fullmatch(r'[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+', (address or '').strip()))


def valid_digest_times(times):
    """Accept 'HH:MM' entries; return them normalised, or None when invalid."""
    if not isinstance(times, list):
        return None
    normalised = []
    for entry in times:
        if not isinstance(entry, str):
            return None
        match = re.fullmatch(r'\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*', entry)
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 23 or minute > 59:
            return None
        text = '%02d:%02d' % (hour, minute)
        if text not in normalised:
            normalised.append(text)
    return sorted(normalised)


def validate(config, password=None):
    """Raise EmailError with a user-facing reason, or return an EmailSettings."""
    settings = EmailSettings.from_settings({'email': config}, password if password is not None else (config or {}).get('password', ''))
    if not settings.recipient.strip():
        raise EmailError('请填写接收提醒的邮箱地址。')
    if not valid_address(settings.recipient):
        raise EmailError('接收提醒的邮箱地址格式不正确。')
    if settings.sender.strip() and not valid_address(settings.sender):
        raise EmailError('发件邮箱地址格式不正确。')
    if not settings.enabled:
        return settings
    if not settings.server.strip():
        raise EmailError('请填写邮件服务器地址，或先填入邮箱以自动识别。')
    if not isinstance(settings.port, int) or not 1 <= settings.port <= 65535:
        raise EmailError('邮件服务器端口需要在 1–65535 之间。')
    if settings.security not in ('ssl', 'starttls'):
        raise EmailError('加密方式只支持 SSL 或 STARTTLS。')
    if not settings.password:
        raise EmailError('请填写邮箱授权码（不是登录密码）。')
    return settings


def compose_alert(items, window, now):
    """One digest for every task that crossed the same reminder window."""
    hours = int(window) if float(window).is_integer() else window
    subject = f'【作业提醒】{len(items)} 项任务将在 {hours} 小时内截止'
    return Mail(subject=subject, text=_render_text(items, window, now), html=_render_html(items, window, now))


def compose_digest(items, now):
    subject = f'【作业提醒】{now.strftime("%m月%d日")} 待完成 {len(items)} 项'
    return Mail(subject=subject, text=_render_text(items, None, now), html=_render_html(items, None, now))


def _deadline_text(item):
    return item.deadline.strftime('%m月%d日 %H:%M') if item.deadline else '待核对'


def remaining_hours(item, now):
    if not item.deadline:
        return None
    return (item.deadline - now).total_seconds() / 3600


def _remaining_text(item, now):
    hours = remaining_hours(item, now)
    if hours is None:
        return '截止时间待核对'
    if hours <= 0:
        return '已截止'
    days, rest = divmod(hours, 24)
    if days >= 1:
        return f'{int(days)} 天 {int(rest)} 小时'
    return f'{max(1, int(hours))} 小时' if hours >= 1 else f'{max(1, int(hours * 60))} 分钟'


def _ordered(items, now):
    """Soonest deadline first; unknown deadlines last."""
    return sorted(items, key=lambda item: (item.deadline is None, item.deadline or now, item.course, item.name))


def _intro(window, count, now):
    if window is None:
        return f'以下 {count} 项任务尚未完成，按截止时间排列。'
    hours = int(window) if float(window).is_integer() else window
    return f'以下 {count} 项任务将在 {hours} 小时内截止，请及时完成。'


def _render_text(items, window, now):
    items = _ordered(items, now)
    lines = [_intro(window, len(items), now), '']
    for item in items:
        lines.append(f'· [{item.kind}] {item.name}')
        lines.append(f'  课程：{item.course}')
        lines.append(f'  截止：{_deadline_text(item)}（剩余 {_remaining_text(item, now)}）')
        lines.append(f'  链接：{item.course_url}')
        lines.append('')
    lines.append('—— 由 MoocReminder 自动发送，请勿直接回复。')
    return '\n'.join(lines).replace('\n\n\n', '\n\n')


def _render_html(items, window, now):
    items = _ordered(items, now)
    rows = []
    for item in items:
        rows.append('<tr><td style="padding:10px 12px;border-bottom:1px solid #eef1f6">'
            f'<div style="font-weight:600;color:#28364c">{_escape(item.name)}'
            f'<span style="color:#8a96a8;font-weight:400"> · {_escape(item.kind)}</span></div>'
            f'<div style="color:#7e8da3;font-size:13px">{_escape(item.course)}</div></td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #eef1f6;color:#5b6e8d;white-space:nowrap">'
            f'{_escape(_deadline_text(item))}<div style="color:#a3adbe;font-size:12px">剩余 {_escape(_remaining_text(item, now))}</div></td>'
            f'<td style="padding:10px 12px;border-bottom:1px solid #eef1f6">'
            f'<a href="{_escape(item.course_url)}" style="color:#3b7eed;text-decoration:none">打开</a></td></tr>')
    return ('<div style="font-family:Microsoft YaHei UI,Segoe UI,sans-serif;background:#f6f8fc;padding:24px">'
        '<div style="max-width:640px;margin:0 auto;background:#fff;border:1px solid #e4e9f2;border-radius:12px;overflow:hidden">'
        '<div style="padding:20px 24px;border-bottom:1px solid #eef1f6">'
        '<div style="font-size:18px;font-weight:600;color:#28364c">MoocReminder 作业提醒</div>'
        f'<div style="color:#7e8da3;font-size:13px;margin-top:6px">{_escape(_intro(window, len(items), now))}</div></div>'
        '<table style="width:100%;border-collapse:collapse">' + ''.join(rows) + '</table>'
        '<div style="padding:16px 24px;color:#a3adbe;font-size:12px">由 MoocReminder 自动发送 · 请勿直接回复</div>'
        '</div></div>')


def _escape(value):
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


class Notifier:
    """Extension point: any channel that can deliver one message."""

    name = 'notifier'

    def send(self, settings, mail):  # pragma: no cover - interface only
        raise NotImplementedError


class SmtpNotifier(Notifier):
    name = 'smtp'
    timeout = 20

    def send(self, settings, mail):
        message = EmailMessage()
        message['Subject'] = mail.subject
        message['From'] = formataddr(('MoocReminder', settings.sender or settings.recipient))
        message['To'] = settings.recipient
        message['Date'] = formatdate(localtime=True)
        message.set_content(mail.text)
        if mail.html:
            message.add_alternative(mail.html, subtype='html')
        try:
            if settings.security == 'ssl':
                client = smtplib.SMTP_SSL(settings.server, settings.port, timeout=self.timeout,
                    context=ssl.create_default_context())
            else:
                client = smtplib.SMTP(settings.server, settings.port, timeout=self.timeout)
            with client:
                if settings.security == 'starttls':
                    client.ehlo()
                    client.starttls(context=ssl.create_default_context())
                    client.ehlo()
                client.login(settings.sender or settings.recipient, settings.password)
                client.send_message(message)
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError('邮箱授权码不正确，或该邮箱未开启 SMTP 服务。') from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise EmailError('收件邮箱被服务器拒绝，请检查地址是否正确。') from exc
        except smtplib.SMTPSenderRefused as exc:
            raise EmailError('发件邮箱被服务器拒绝，请检查发件地址。') from exc
        except smtplib.SMTPException as exc:
            raise EmailError('邮件发送失败：' + str(exc)[:120]) from exc
        except (OSError, ssl.SSLError) as exc:
            raise EmailError('无法连接邮件服务器，请检查服务器地址、端口和网络。') from exc
        return True


def password_saved(token):
    """True when a stored secret is present and still readable by this user."""
    return bool(token) and bool(secrets_store.decrypt(token))


def get_notifier(name='smtp'):
    """Resolve a channel by name so new transports stay additive."""
    notifiers = {SmtpNotifier.name: SmtpNotifier}
    if name not in notifiers:
        raise EmailError('不支持的提醒通道：' + str(name))
    return notifiers[name]()


class Mailer:
    """Delivery queue; the interface thread only enqueues, never waits.

    `resolve` maps a stored config dict to an (EmailSettings, Notifier) pair, so
    tests can substitute a fake channel and the password stays out of the queue.
    """

    def __init__(self, log=None, resolve=None, on_result=None):
        self.events = queue.Queue()
        self.log = log or (lambda message: None)
        self.on_result = on_result or (lambda key, ok, detail: None)
        self.resolve = resolve or (lambda config: (validate(config, secrets_store.decrypt(config.get('password', ''))),
                                                   get_notifier()))
        self.inflight = set()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name='mooc-mailer', daemon=True)
        self.thread.start()

    def stop(self):
        self.events.put(('stop', None))
        if self.thread:
            self.thread.join(timeout=3)

    def enqueue(self, key, config, mail):
        """Queue one message. Returns False when it is already in flight."""
        if not key or key in self.inflight:
            return False
        self.inflight.add(key)
        self.events.put(('send', (key, config, mail)))
        return True

    def _run(self):
        while True:
            kind, payload = self.events.get()
            if kind == 'stop':
                return
            key, config, mail = payload
            try:
                settings, notifier = self.resolve(config)
                notifier.send(settings, mail)
                self.log(f'邮件已发送：{mail.subject}')
                self.on_result(key, True, '')
            except EmailError as exc:
                self.log('邮件发送失败：' + str(exc))
                self.on_result(key, False, str(exc))
            except Exception as exc:
                self.log('邮件发送异常：' + type(exc).__name__)
                self.on_result(key, False, '邮件发送异常，请查看连接详情。')
            finally:
                self.inflight.discard(key)
