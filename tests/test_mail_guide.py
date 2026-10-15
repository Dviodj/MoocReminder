import unittest

import mail_guide


class ProviderLookupTests(unittest.TestCase):
    def test_a_known_domain_selects_its_provider(self):
        self.assertEqual(mail_guide.supports('admin@qq.com')['name'], 'QQ 邮箱')
        self.assertEqual(mail_guide.supports('admin@foxmail.com')['name'], 'Foxmail 邮箱')
        self.assertEqual(mail_guide.supports('admin@163.com')['name'], '163 邮箱')
        self.assertEqual(mail_guide.supports('admin@126.com')['name'], '126 邮箱')

    def test_lookup_ignores_case_and_surrounding_space(self):
        self.assertEqual(mail_guide.supports('  Admin@QQ.COM  ')['name'], 'QQ 邮箱')

    def test_an_unknown_or_malformed_address_has_no_provider(self):
        for text in ('admin@company.example', 'not-an-address', '', None, '@', 'a@'):
            self.assertIsNone(mail_guide.supports(text), text)


class GuideHtmlTests(unittest.TestCase):
    def test_the_qq_guide_carries_the_verified_steps(self):
        html = mail_guide.guide_html('admin@qq.com')
        # The current official path, not the older "设置 → 账户" one.
        self.assertIn('账号与安全', html)
        self.assertIn('POP3/IMAP/SMTP/Exchange/CardDAV 服务', html)
        self.assertIn('16 位授权码', html)
        self.assertIn('我已发送', html)

    def test_the_guide_shows_the_server_settings_to_fill_in(self):
        html = mail_guide.guide_html('admin@qq.com')
        self.assertIn('smtp.qq.com', html)
        self.assertIn('465', html)
        self.assertIn('SSL', html)

    def test_an_unrecognised_address_falls_back_to_the_example(self):
        html = mail_guide.guide_html('admin@company.example')
        self.assertIn('QQ 邮箱', html)
        self.assertIn('smtp.qq.com', html)

    def test_each_provider_gets_its_own_server_not_another_ones(self):
        self.assertIn('smtp.163.com', mail_guide.guide_html('admin@163.com'))
        self.assertNotIn('smtp.qq.com', mail_guide.guide_html('admin@163.com'))
        self.assertIn('smtp.qq.com', mail_guide.guide_html('admin@qq.com'))
        self.assertNotIn('smtp.163.com', mail_guide.guide_html('admin@qq.com'))

    def test_every_guide_links_to_its_official_help_page(self):
        for address in ('admin@qq.com', 'admin@163.com', 'admin@126.com'):
            html = mail_guide.guide_html(address)
            self.assertIn('help.mail.', html, address)

    def test_no_step_is_left_unrendered(self):
        html = mail_guide.guide_html('admin@qq.com')
        for placeholder in ('{number}', '{body}', '{path}', '{info'):
            self.assertNotIn(placeholder, html, placeholder)

    def test_troubleshooting_is_always_included(self):
        for address in ('admin@qq.com', 'admin@company.example', None):
            html = mail_guide.guide_html(address)
            self.assertIn('常见问题', html)
            self.assertIn('邮箱授权码不正确', html)

    def test_the_address_is_escaped_rather_than_injected(self):
        # supports() gates what reaches the markup, so a hostile address must
        # simply fall back to the default guide instead of reaching the output.
        html = mail_guide.guide_html('<script>alert(1)</script>@qq.com')
        self.assertNotIn('<script>', html)

    def test_a_provider_can_be_forced_by_name(self):
        html = mail_guide.guide_html(provider='163.com')
        self.assertIn('smtp.163.com', html)


if __name__ == '__main__':
    unittest.main()
