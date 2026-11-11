"""Offline fixture tests; no external network needed."""
import importlib.util
from pathlib import Path
import unittest

p = Path(__file__).resolve().parents[1] / 'scripts' / 'update_funds.py'
spec = importlib.util.spec_from_file_location('fund_updater', p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FundUpdaterTests(unittest.TestCase):
    def test_seven_exact_funds_only(self):
        self.assertEqual(len(mod.FUNDS), 7)
        self.assertEqual(sum(s['currency']=='ZAR' for s in mod.FUNDS.values()),1)
        self.assertEqual(sum(s['currency']=='USD' for s in mod.FUNDS.values()),6)

    def test_overseas_share_class_isin_and_currency(self):
        rows = [
            {'ISINCODE':'LU0820561818','日期':'2026/11/07','基金淨值':'8.4212','計價幣別':'美元','基金代碼':'F1'},
            {'ISINCODE':'LU2023250330','日期':'2026/11/07','基金淨值':'9.1403','計價幣別':'美元','基金代碼':'F2'},
            {'ISINCODE':'LU1883841964','日期':'2026/11/07','基金淨值':'12.3432','計價幣別':'美元','基金代碼':'WRONG'},
            {'ISINCODE':'LU1883841964','日期':'2026/11/07','基金淨值':'101.2100','計價幣別':'南非幣','基金代碼':'F3'},
            {'ISINCODE':'TWSE2330','日期':'2026/11/07','基金淨值':'1400','計價幣別':'美元','基金代碼':'STOCK'},
        ]
        found, code=mod.latest_overseas_nav(rows)
        self.assertEqual(found['LU0820561818']['nav'],8.4212)
        self.assertEqual(found['LU2023250330']['nav'],9.1403)
        self.assertEqual(found['LU1883841964']['nav'],101.21)
        self.assertNotIn('WRONG',code)
        self.assertNotIn('TWSE2330',found)

    def test_domestic_exact_class_tax_id(self):
        rows=[
            {'基金統編':'73637204D','基金淨值':'8.001','日期':'2026-11-07','幣別':'USD'},
            {'基金統編':'73637204C','基金淨值':'18.1','日期':'2026-11-07','幣別':'USD'},
            {'基金統編':'38522113J','基金淨值':'8.128','日期':'2026-11-07','幣別':'USD'},
            {'基金統編':'38522113K','基金淨值':'29.999','日期':'2026-11-07','幣別':'USD'},
        ]
        found=mod.latest_local_nav(rows)
        self.assertEqual(found['TW000T0759D2']['nav'],8.001)
        self.assertEqual(found['TW000T1811K5']['nav'],8.128)

    def test_offshore_dividends_mapped_by_code(self):
        rows=[{'基金代碼':'CODE-1','基金幣別':'美元','除息日':'2026/10/15','配息發放日':'2026/10/19','單位數':'0.0600'},
              {'基金代碼':'CODE-OTHER','基金幣別':'美元','除息日':'2026/10/15','配息發放日':'2026/10/19','單位數':'999'}]
        found=mod.overseas_dividends(rows,{'CODE1':'LU0820561818'})
        self.assertEqual(found['LU0820561818'][0]['perUnit'],0.06)
        self.assertEqual(len(found),1)

    def test_local_dividends_exact_match(self):
        rows=[{'基金統編':'38522113J','配息幣別':'美元','除息日':'2026/10/15','收益分配發放日':'2026/10/19','每單位分派金額':'0.07'},
              {'基金統編':'38522113K','配息幣別':'人民幣','除息日':'2026/10/15','收益分配發放日':'2026/10/19','每單位分派金額':'1.20'}]
        found=mod.local_dividends(rows)
        self.assertEqual(found['TW000T1811K5'][0]['perUnit'],0.07)

    def test_failure_preserves_existing_and_marks_cached(self):
        existing={'funds':{'LU0820561818':{'nav':8.0888,'navDate':'2026-10-08','dividends':[{'exDate':'2026-10-15','payDate':'2026-10-19','perUnit':0.06}]}}}
        sources={name:{'ok':False,'rows':[],'error':'offline'} for name in mod.SOURCES}
        got=mod.update_data(existing,sources,'2026-11-10T21:45:00+08:00')
        f=got['funds']['LU0820561818']
        self.assertEqual(f['nav'],8.0888)
        self.assertEqual(f['navStatus'],'cached')
        self.assertEqual(f['navDate'],'2026-10-08')
        self.assertEqual(len(f['dividends']),1)
        self.assertEqual(got['funds']['LU2023250330']['navStatus'],'unavailable')

    def test_date_handles_roc(self):
        self.assertEqual(mod.date_str('115/10/15'),'2026-10-15')
        self.assertEqual(mod.date_str('20261015'),'2026-10-15')

    def test_csv_parses_big5_and_utf8(self):
        raw='基金代碼,基金名稱,日期,基金淨值,ISINCODE,計價幣別\nF1,安聯,2026/11/07,8.20,LU0820561818,美元\n'
        for encoding in ('utf-8-sig','cp950'):
            rows=mod.parse_csv(raw.encode(encoding))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['ISINCODE'],'LU0820561818')


if __name__=='__main__':
    unittest.main()
