import importlib.util
import pathlib
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('update_fx', ROOT / 'scripts' / 'update_fx.py')
fx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fx)


class FXRepairTests(unittest.TestCase):
    def test_parse_bank_realistic_html(self):
        today = datetime.now(timezone(timedelta(hours=8))).strftime('%Y/%m/%d')
        html = f'''<h1>臺灣銀行</h1><p>牌價最新掛牌時間：{today} 12:01</p>
          <table><tbody>
          <tr><td><div>美金 (<span>USD</span>)</div></td><td>32.500</td><td>33.210</td><td>32.900</td><td>33.000</td></tr>
          <tr><td>南非幣 (ZAR)</td><td>-</td><td>-</td><td>1.900</td><td>1.980</td></tr>
          </tbody></table>'''.encode('utf-8')
        got = fx.from_html(html)
        self.assertEqual(got['USD']['buy'], 32.9)
        self.assertEqual(got['USD']['sell'], 33.0)
        self.assertEqual(got['ZAR']['buy'], 1.9)
        self.assertEqual(got['ZAR']['sell'], 1.98)
        self.assertTrue(got['USD']['quotedAt'])

    def test_parse_csv(self):
        contents = '幣別,現金買入,現金賣出,即期買入,即期賣出\nUSD,32.5,33.2,32.9,33.0\nZAR,-,-,1.9,1.98\n'
        quotes = fx.from_csv(contents.encode('utf-8-sig'))
        self.assertEqual(quotes['ZAR']['buy'], 1.9)
        self.assertEqual(quotes['USD']['sell'], 33.0)

    def test_reject_cash_missing_and_unreasonable_quotes(self):
        self.assertIsNone(fx.pair('ZAR', '-', '-'))
        self.assertIsNone(fx.pair('USD', '32.9', '32.1'))
        self.assertIsNone(fx.pair('USD', '1.92', '2.00'))

    def test_cached_is_marked(self):
        old = {'rates': {'USD': {'buy':32.9,'sell':33.0,'quotedAt':'2026-11-13T10:00:00+08:00'}}}
        doc = fx.make_doc(old, {}, {}, ['來源暫時無法取得'], '2026-11-16T12:00:00+08:00')
        self.assertFalse(doc['ok'])
        self.assertEqual(doc['rates']['USD']['status'], 'cached')
        self.assertIsNone(doc['rates']['ZAR']['buy'])
        self.assertEqual(doc['rates']['ZAR']['status'], 'unavailable')

    def test_new_rates_are_fresh(self):
        fresh = {'USD': {'buy':32.9,'sell':33.0,'quotedAt':''},'ZAR':{'buy':1.9,'sell':1.98,'quotedAt':''}}
        doc = fx.make_doc({}, fresh, {k:('BOT','https://rate.bot.com.tw') for k in fresh}, [],'2026-11-16T12:00:00+08:00')
        self.assertTrue(doc['ok'])
        self.assertEqual(doc['rates']['USD']['status'],'found')
        self.assertEqual(doc['rates']['ZAR']['status'],'found')

    def test_page_keeps_fund_and_fx_sections(self):
        page = (ROOT/'index.html').read_text('utf-8')
        self.assertIn('data/live.json', page)
        self.assertIn('data/fx.json', page)
        self.assertIn('美元・南非幣即期匯率', page)
        self.assertNotIn('id="latestDividends"', page)
        self.assertEqual(page.count('id="fxList"'), 1)


if __name__ == '__main__':
    unittest.main()
