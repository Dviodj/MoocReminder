"""Illustrated guide for obtaining an email sender credential.

Only needed by whoever configures the sending account: ordinary users just type
their own address, because the program ships a sender. The guide is built as
HTML so it renders in a dialog with no extra files and no third-party images.
"""
from html import escape

# Where each provider keeps its instructions. Linking out is deliberate: the
# real screenshots belong to the providers, and their console changes often
# enough that a copy shipped inside the program would go stale.
PROVIDER_GUIDES = {
    'qq.com': dict(
        name='QQ 邮箱',
        help_url='https://help.mail.qq.com/detail/0/1087',
        server='smtp.qq.com', port=465, security='SSL',
        entry='设置 → 账号与安全',
        area='POP3/IMAP/SMTP/Exchange/CardDAV 服务',
        verify='用绑定的手机号发送指定短信，再点“我已发送”',
        credential='16 位授权码',
        manage='设备管理 → 授权码管理',
    ),
    'foxmail.com': dict(
        name='Foxmail 邮箱',
        help_url='https://help.mail.qq.com/detail/0/1087',
        server='smtp.qq.com', port=465, security='SSL',
        entry='设置 → 账号与安全',
        area='POP3/IMAP/SMTP/Exchange/CardDAV 服务',
        verify='用绑定的手机号发送指定短信，再点“我已发送”',
        credential='16 位授权码',
        manage='设备管理 → 授权码管理',
    ),
    '163.com': dict(
        name='163 邮箱',
        help_url='https://help.mail.163.com/',
        server='smtp.163.com', port=465, security='SSL',
        entry='设置 → POP3/SMTP/IMAP',
        area='开启 SMTP 服务',
        verify='按提示完成短信验证',
        credential='授权码',
    ),
    '126.com': dict(
        name='126 邮箱',
        help_url='https://help.mail.163.com/',
        server='smtp.126.com', port=465, security='SSL',
        entry='设置 → POP3/SMTP/IMAP',
        area='开启 SMTP 服务',
        verify='按提示完成短信验证',
        credential='授权码',
    ),
}

_CARD = (
    '<table cellspacing="0" cellpadding="0" width="100%" style="margin:6px 0 14px 0">'
    '<tr><td style="background:#f6f8fc;border:1px solid #dfe6f1;border-radius:10px;padding:14px 16px">'
    '{body}</td></tr></table>'
)

_STEP = (
    '<table cellspacing="0" cellpadding="0" width="100%" style="margin:0 0 12px 0">'
    '<tr>'
    # Qt's rich text engine sizes tables but ignores width/height on block
    # elements, so the badge is a one-cell table rather than a styled div.
    '<td width="30" valign="top">'
    '<table cellspacing="0" cellpadding="0"><tr>'
    '<td width="24" height="24" align="center" valign="middle" '
    'style="background:#2f6bff;color:#ffffff;font-weight:bold">{number}</td>'
    '</tr></table>'
    '</td>'
    '<td valign="top">{body}</td>'
    '</tr></table>'
)

_ARROW = '<span style="color:#b6c2d6">&nbsp;→&nbsp;</span>'

_PATH = ('<span style="background:#eef3ff;border:1px solid #b9cdf5;'
         'padding:3px 9px;color:#2b4d94;font-weight:bold">{path}</span>')


def _path_chain(parts):
    """A horizontal breadcrumb: the navigation path, one badge per step."""
    return ('<div style="margin:6px 0 2px 0">'
            + _ARROW.join(_PATH.format(path=escape(part)) for part in parts)
            + '</div>')


def supports(address):
    """The provider guide matching an address, or None."""
    text = str(address or '').strip().lower()
    if '@' not in text:
        return None
    return PROVIDER_GUIDES.get(text.rsplit('@', 1)[1])


def guide_html(address=None, provider=None):
    """The full illustrated guide, tailored to a provider when it is known."""
    info = PROVIDER_GUIDES.get(provider) if provider else supports(address)
    if not info:
        info = PROVIDER_GUIDES['qq.com']
    name = info['name']
    parts = [part.strip() for part in info['entry'].split('→')]

    rows = [
        '<h2 style="margin:0 0 2px 0;font-size:17px">获取发件邮箱的授权码</h2>',
        f'<p style="color:#7e8da3;margin:0 0 14px 0">以 {escape(name)} 为例。'
        '授权码不是邮箱登录密码，它是专门给第三方程序用的。</p>',
        _STEP.format(number=1, body=(
            f'<b>打开邮箱网页版，进入账号与安全</b><br>'
            f'<span style="color:#5b6b82">登录 {escape(name)} 网页版后，按下面的路径进入。</span>'
            + _path_chain(parts))),
        _STEP.format(number=2, body=(
            f'<b>开启 SMTP 服务</b><br>'
            f'<span style="color:#5b6b82">找到“{escape(info["area"])}”，把旁边的开关打开。</span>'
            + _CARD.format(body=(
                f'<span style="color:#5b6b82">要找到的区域：</span><br>'
                f'<b style="color:#2b4d94">{escape(info["area"])}</b>'))),),
        _STEP.format(number=3, body=(
            f'<b>完成验证，拿到授权码</b><br>'
            f'<span style="color:#5b6b82">{escape(info["verify"])}。验证通过后页面会显示一串'
            f'{escape(info["credential"])}，这就是要填的内容。</span>'
            + _CARD.format(body=(
                '<span style="color:#a8434a">授权码只显示一次，请当场复制保存。'
                '关掉页面后只能重新生成。</span>'))),),
        _STEP.format(number=4, body=(
            '<b>填回本程序</b><br>'
            '<span style="color:#5b6b82">把邮箱地址与授权码填到上面的输入框。'
            '服务器、端口、加密方式可按下表留空，程序会按服务商自动填写。</span>'
            + _CARD.format(body=_settings_table(info))),),
        _STEP.format(number=5, body=(
            '<b>发一封测试邮件确认</b><br>'
            '<span style="color:#5b6b82">保存后点“发送测试邮件”。收到就说明配置正确。</span>')),
    ]
    if info.get('manage'):
        rows.append(_STEP.format(number=6, body=(
            '<b>日后如何更换授权码</b><br>'
            f'<span style="color:#5b6b82">在“{escape(info["manage"])}”里可以停用或重置：'
            '授权码遗忘或怀疑泄露时，在那里生成新的，旧的会立即失效。</span>')))
    rows.append(_trouble(info))
    return ''.join(rows)


def _settings_table(info):
    return (
        '<table cellspacing="0" cellpadding="6" width="100%" style="font-size:13px">'
        '<tr style="color:#7e8da3"><td width="34%">邮件服务器</td><td width="22%">端口</td>'
        '<td>加密方式</td></tr>'
        f'<tr><td><b>{escape(info["server"])}</b></td><td><b>{info["port"]}</b></td>'
        f'<td><b>{escape(info["security"])}</b></td></tr>'
        '</table>')


def _trouble(info):
    return (
        '<h3 style="margin:16px 0 6px 0;font-size:15px">常见问题</h3>'
        + _CARD.format(body=(
            '<b>提示“邮箱授权码不正确”</b><br>'
            '<span style="color:#5b6b82">多半是填了邮箱登录密码。授权码是一串'
            f'{escape(info["credential"])}，两者不同；也可以到邮箱的“授权码管理”里重新生成一个。</span>'))
        + _CARD.format(body=(
            '<b>提示“无法连接邮件服务器”</b><br>'
            '<span style="color:#5b6b82">检查服务器与端口是否和上表一致。'
            '公司或校园网络有时会拦截 465 端口，换用手机热点试一次即可判断。</span>'))
        + _CARD.format(body=(
            '<b>找不到“账号与安全”</b><br>'
            f'<span style="color:#5b6b82">邮箱改版后入口位置会变。'
            f'<a href="{escape(info["help_url"])}">打开官方帮助页</a>看当前的真实界面。</span>'))
        + f'<p style="color:#a3adbe;font-size:12px;margin:10px 0 0 0">'
          f'步骤依据 {escape(info["name"])} 官方说明整理；界面改版时请以'
          f'<a href="{escape(info["help_url"])}">官方帮助页</a>为准。</p>')
