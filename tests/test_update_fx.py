import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from update_fx import parse_bank_html,parse_bank_csv,build_document,rate_pair

class BankFxTest(unittest.TestCase):
    def test_html_parser_two_currencies(self):
        html = '''<html><body><h3>牌價最新掛牌時間：2026/11/13 16:02</h3><table>
        <tr><td>美金 (USD)</td><td>32.44</td><td>33.11</td><td>32.79</td><td>32.89</td><td>查詢</td></tr>
        <tr><td>南非幣 (ZAR)</td><td>-</td><td>-</td><td>1.915</td><td>1.995</td><td>查詢</td></tr>
        </table></body></html>'''
        fx = parse_bank_html(html.encode('utf-8'))
        self.assertEqual(fx['USD']['buy'],32.79)
        self.assertEqual(fx['USD']['sell'],32.89)
        self.assertEqual(fx['ZAR']['buy'],1.915)
        self.assertEqual(fx['ZAR']['sell'],1.995)
        self.assertIn('2026-11-13',fx['USD']['quotedAt'])

    def test_csv_parser_no_header(self):
        data = 'USD,32.4,33.1,32.8,32.9\nZAR,-,-,1.91,2.00\n'
        fx = parse_bank_csv(data.encode('utf-8'))
        self.assertEqual(fx['USD']['sell'],32.9)
        self.assertEqual(fx['ZAR']['buy'],1.91)

    def test_ignores_invalid_or_mid_market_rates(self):
        self.assertIsNone(rate_pair('2.0','1.8'))
        self.assertIsNone(rate_pair('—','1.9'))
        self.assertIsNone(rate_pair('1.0','20.0'))

    def test_cached_not_pretended_fresh(self):
        old = {'rates':{'USD':{'buy':32.1,'sell':32.3,'quotedAt':'2026-11-12T16:00:00+08:00', 'fetchedAt':'2026-11-12T16:01:00+08:00'}}}
        d=build_document(old, None, message='下載失敗', at='2026-11-13T09:15:00+08:00')
        self.assertFalse(d['ok'])
        self.assertEqual(d['rates']['USD']['status'],'cached')
        self.assertEqual(d['rates']['USD']['fetchedAt'],'2026-11-12T16:01:00+08:00')
        self.assertIsNone(d['rates']['ZAR']['buy'])

if __name__=='__main__':
    unittest.main()
