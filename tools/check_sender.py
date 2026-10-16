"""Check the publisher's sender configuration, and optionally send a test mail.

This is the step between "I registered a dedicated reminder mailbox" and "I can
build a release package": it proves the credential works before it is shipped to
every user, where a wrong value would silently break reminders for all of them.

The authorization code is never printed — only whether one is present.

    python tools/check_sender.py
    python tools/check_sender.py --to you@qq.com
"""
from pathlib import Path
import argparse
from datetime import datetime
import sys

# This file lives in tools/, so the folder holding paths.py is one level up.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import paths
import emailer

ROOT = paths.PROGRAM_ROOT
# Keys the shipped file must carry for ordinary users to receive reminders.
REQUIRED = ('sender', 'server', 'password')


def report(folder):
    """Describe the shipped sender without revealing the credential."""
    path = Path(folder) / emailer.SENDER_FILE
    print('配置文件 :', path)
    if not path.is_file():
        print('存在     : 否')
        print()
        print('还没有配置发件邮箱。把 email_sender.example.json 复制为')
        print('email_sender.json，填入专门注册的提醒邮箱与它的授权码。')
        return None
    print('存在     : 是')
    config = emailer.load_sender(folder, refresh=True)
    for name in emailer.SENDER_KEYS:
        value = config.get(name)
        if name == 'password':
            shown = '已填写（%d 位，此处不显示）' % len(str(value)) if value else '（空）'
        else:
            shown = value if value not in (None, '') else '（空）'
        print('%-9s: %s' % (name, shown))
    missing = [name for name in REQUIRED if not str(config.get(name, '') or '').strip()]
    print()
    if missing:
        print('未通过 ：缺少 %s；用户将收不到提醒，打包也会被拒绝。' % '、'.join(missing))
        return None
    print('已就绪 ：普通用户只填自己的收件邮箱即可收到提醒。')
    return config


def verify_syntax(config):
    """Catch the mistakes that only show up when a real send is attempted."""
    problems = []
    if not emailer.valid_address(config.get('sender', '')):
        problems.append('sender 不是有效的邮箱地址')
    if config.get('security') not in ('ssl', 'starttls'):
        problems.append('security 必须是 ssl 或 starttls')
    port = config.get('port')
    if not isinstance(port, int) or not 1 <= port <= 65535:
        problems.append('port 需要在 1–65535 之间')
    if not str(config.get('server', '') or '').strip():
        problems.append('server 为空')
    return problems


def send_test(config, recipient):
    """Deliver one real message so the credential is proven, not assumed."""
    settings = emailer.EmailSettings(
        enabled=True, recipient=recipient, sender=config['sender'],
        server=config['server'], port=config.get('port', emailer.DEFAULT_PORT),
        security=config.get('security', 'ssl'), password=config['password'])
    mail = emailer.compose_digest([], datetime.now())
    emailer.get_notifier().send(settings, mail)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description='检查发件邮箱配置（发布者用）')
    parser.add_argument('--folder', default=str(ROOT), help='email_sender.json 所在目录')
    parser.add_argument('--to', default='', help='向该地址真发一封测试邮件，验证授权码确实可用')
    args = parser.parse_args(argv)

    config = report(args.folder)
    if config is None:
        return 1

    problems = verify_syntax(config)
    if problems:
        print('字段有误：' + '；'.join(problems))
        return 1

    if not args.to:
        print('提示    ：加 --to 你的邮箱 可真发一封测试邮件，确认授权码可用。')
        return 0

    if not emailer.valid_address(args.to):
        print('--to 不是有效的邮箱地址：' + args.to)
        return 1
    print()
    print('正在以 %s 向 %s 发送测试邮件…' % (config['sender'], args.to))
    try:
        send_test(config, args.to)
    except emailer.EmailError as exc:
        print('发送失败：' + str(exc))
        return 1
    print('发送成功：请查收（也可能在垃圾邮件里）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
