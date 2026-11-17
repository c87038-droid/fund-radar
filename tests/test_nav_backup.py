"""No network required; validate secondary NAV only when exact share class and date are visible."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / 'scripts' / 'update_funds.py'
spec = importlib.util.spec_from_file_location('nav_updater_backup', path)
nav = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nav)


def page(heading, number, ccy, date):
    return (f'<html><head><title>基金淨值</title></head><body>'
            f'<h1>{heading}</h1><div>觀察</div>'
            f'<div>最新值 {number}{ccy}({date})</div>'
            f'<div>近20日淨值</div><div>11/13 | 99.9999</div>'
            f'</body></html>').encode('utf-8')


class BackupTests(unittest.TestCase):
    def test_exact_am_and_date(self):
        expected=nav.NAV_BACKUP_PAGES['LU0820561818'][1]
        got=nav.parse_backup_nav_html(page(expected,'8.0276','美元','2026/11/16'),expected,'USD')
        self.assertEqual((got['nav'],got['navDate']),(8.0276,'2026-11-16'))

    def test_zar_fund_and_quote(self):
        expected=nav.NAV_BACKUP_PAGES['LU1883841964'][1]
        got=nav.parse_backup_nav_html(page(expected,'307.21','南非幣','2026/11/16'),expected,'ZAR')
        self.assertEqual(got['nav'],307.21)

    def test_wrong_share_class_must_fail(self):
        expected=nav.NAV_BACKUP_PAGES['LU0820561818'][1]
        wrong='安聯收益成長基金-BM9穩定月收類股(美元)'
        with self.assertRaisesRegex(ValueError,'基金名稱'):
            nav.parse_backup_nav_html(page(wrong,'9.8888','美元','2026/11/16'),expected,'USD')

    def test_wrong_currency_must_fail(self):
        expected=nav.NAV_BACKUP_PAGES['LU1883841964'][1]
        with self.assertRaisesRegex(ValueError,'最新淨值'):
            nav.parse_backup_nav_html(page(expected,'307.21','美元','2026/11/16'),expected,'ZAR')

    def test_chart_only_no_latest_must_fail(self):
        expected=nav.NAV_BACKUP_PAGES['LU0820561818'][1]
        txt=(f'<html><h1>{expected}</h1><p>近20日淨值 11/16 8.0000</p></html>').encode()
        with self.assertRaisesRegex(ValueError,'最新淨值'):
            nav.parse_backup_nav_html(txt,expected,'USD')

    def test_choose_more_recent_backup(self):
        fid='LU0820561818'
        src={k:{'ok':True,'rows':[]} for k in nav.SOURCES}
        src['overseas_nav']['rows']=[{'ISINCODE':fid,'日期':'2026/11/13','基金淨值':'8.1500','計價幣別':'美元'}]
        secondary={fid:{'nav':8.0276,'navDate':'2026-11-16','navSource':'MoneyDJ 基金專頁（備援，非基金公司官方）'}}
        result=nav.update_data({'funds':{}},src,'2026-11-18T12:00:00+08:00',backup_nav=secondary)
        got=result['funds'][fid]
        self.assertEqual(got['nav'],8.0276)
        self.assertEqual(got['navDate'],'2026-11-16')
        self.assertIn('MoneyDJ',got['navSource'])

    def test_newer_old_snapshot_never_replaced(self):
        fid='LU0820561818'
        src={k:{'ok':True,'rows':[]} for k in nav.SOURCES}
        prior={'funds':{fid:{'nav':8.99,'navDate':'2026-11-17','dividends':[]}}}
        secondary={fid:{'nav':8.0276,'navDate':'2026-11-16','navSource':'MoneyDJ'}}
        got=nav.update_data(prior,src,'2026-11-18T12:00:00+08:00',backup_nav=secondary)['funds'][fid]
        self.assertEqual(got['nav'],8.99)
        self.assertEqual(got['navStatus'],'cached')

    def test_official_preferred_on_same_date(self):
        fid='LU0820561818'
        src={k:{'ok':True,'rows':[]} for k in nav.SOURCES}
        src['overseas_nav']['rows']=[{'ISINCODE':fid,'日期':'2026/11/16','基金淨值':'8.0276','計價幣別':'美元'}]
        second={fid:{'nav':8.0277,'navDate':'2026-11-16','navSource':'MoneyDJ'}}
        got=nav.update_data({'funds':{}},src,'2026-11-18T12:00:00+08:00',backup_nav=second)['funds'][fid]
        self.assertEqual(got['nav'],8.0276)
        self.assertIn('集保',got['navSource'])

if __name__ == '__main__':
    unittest.main()
