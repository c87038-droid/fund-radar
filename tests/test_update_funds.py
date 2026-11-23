"""Ten fund update: offline regression tests (no internet)."""
import importlib.util
from pathlib import Path
import unittest

p=Path(__file__).resolve().parents[1]/'scripts'/'update_funds.py'
spec=importlib.util.spec_from_file_location('fund_updater',p)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FundUpdaterTests(unittest.TestCase):
    def sources(self):
        return {name:{'ok':False,'rows':[]} for name in mod.SOURCES}
    def test_ten_exact_funds(self):
        self.assertEqual(len(mod.FUNDS),10)
        self.assertEqual(sum(s['currency']=='ZAR' for s in mod.FUNDS.values()),2)
        self.assertEqual(sum(s['currency']=='USD' for s in mod.FUNDS.values()),8)
        self.assertEqual(mod.FUNDS['LU1075101847']['currency'],'ZAR')
        self.assertEqual(mod.FUNDS['LU1075101847']['type'],'overseas')
        for isin in ('LU1900986214','LU3047255321'):
            self.assertEqual(mod.FUNDS[isin]['currency'],'USD')
    def test_exact_class_nav_and_currency(self):
        rows=[
          {'ISINCODE':'LU1075101847','日期':'2026/11/20','基金淨值':'703.2','計價幣別':'南非幣','基金代碼':'GS-ZAR-X'},
          {'ISINCODE':'LU1075101847','日期':'2026/11/21','基金淨值':'9.9','計價幣別':'美元','基金代碼':'BAD-USD'},
          {'ISINCODE':'LU1883841964','日期':'2026/11/20','基金淨值':'104.2','計價幣別':'南非幣','基金代碼':'AMUNDI-ZAR'},
          {'ISINCODE':'LU0820561818','日期':'2026/11/20','基金淨值':'8.4212','計價幣別':'美元','基金代碼':'ALLIANZ-USD'},
        ]
        best,codes=mod.latest_overseas_nav(rows)
        self.assertEqual(best['LU1075101847']['nav'],703.2)
        self.assertEqual(best['LU1075101847']['navDate'],'2026-11-20')
        self.assertNotIn('BADUSD',codes)
        self.assertEqual(best['LU1883841964']['nav'],104.2)
    def test_overseas_distributions_match_specific_code(self):
        rows=[
          {'基金代碼':'GS-ZAR-X','基金幣別':'南非幣','除息日':'2026/11/03','配息發放日':'2026/11/06','單位數':'15.7'},
          {'基金代碼':'GS-ZAR-X','基金幣別':'美元','除息日':'2026/11/03','配息發放日':'2026/11/06','單位數':'100'},
          {'基金代碼':'OTHER','基金幣別':'南非幣','除息日':'2026/11/03','單位數':'999'},
        ]
        a=mod.overseas_dividends(rows,{'GSZARX':'LU1075101847'})
        self.assertEqual(len(a['LU1075101847']),1)
        self.assertEqual(a['LU1075101847'][0]['perUnit'],15.7)
    def test_new_fund_data_populates_without_cash_guess(self):
        s=self.sources()
        s['overseas_nav']['rows']=[{'ISINCODE':'LU1075101847','日期':'2026/11/20','基金淨值':'703.2','計價幣別':'南非幣','基金代碼':'GS-ZAR-X'}]
        s['overseas_info']['rows']=[{'基金ISIN_CODE':'LU1075101847','基金代碼':'GS-ZAR-X'}]
        s['overseas_dividends']['rows']=[{'基金代碼':'GS-ZAR-X','基金幣別':'南非幣','除息日':'2026/11/03','配息發放日':'2026/11/06','單位數':'15.7'}]
        out=mod.update_data({'funds':{}},s,at='2026-11-23T09:00:00+08:00')
        d=out['funds']['LU1075101847']
        self.assertEqual(len(out['funds']),10)
        self.assertEqual(d['nav'],703.2)
        self.assertEqual(d['navSource'],'集保境外基金公開資料')
        self.assertEqual(d['dividends'][0]['perUnit'],15.7)
    def test_missing_new_fund_is_marked_unavailable(self):
        out=mod.update_data({'funds':{}},self.sources(),at='2026-11-23T09:00:00+08:00')
        self.assertEqual(out['funds']['LU1075101847']['navStatus'],'unavailable')
        self.assertIsNone(out['funds']['LU1075101847']['nav'])
    def test_existing_nav_cached_and_new_not_faked(self):
        prev={'funds':{'LU0820561818':{'nav':8.0888,'navDate':'2026-11-15','dividends':[]}}}
        out=mod.update_data(prev,self.sources(),at='2026-11-23T09:00:00+08:00')
        self.assertEqual(out['funds']['LU0820561818']['navStatus'],'cached')
        self.assertEqual(out['funds']['LU1075101847']['navStatus'],'unavailable')
    def test_other_class_isin_not_accepted(self):
        rows=[{'ISINCODE':'LU0577846529','日期':'2026/11/20','基金淨值':'99','計價幣別':'美元'}]
        n,_=mod.latest_overseas_nav(rows)
        self.assertNotIn('LU1075101847',n)
    def test_local_currency_and_id(self):
        rows=[{'基金統編':'73637204D','基金淨值':'8.001','日期':'2026-11-07','幣別':'USD'},
              {'基金統編':'73637204C','基金淨值':'18','日期':'2026-11-07','幣別':'USD'}]
        self.assertEqual(mod.latest_local_nav(rows)['TW000T0759D2']['nav'],8.001)
    def test_open_data_basis_restores_current_annual_rate(self):
        s=self.sources()
        s['overseas_nav']['rows']=[
            {'ISINCODE':'LU1075101847','日期':'2026/11/18','基金淨值(金額)':'700.00','計價幣別':'南非幣','基金代碼':'GS-ZAR-X'},
            {'ISINCODE':'LU1075101847','日期':'2026/11/19','基金淨值(金額)':'684.30','計價幣別':'南非幣','基金代碼':'GS-ZAR-X'},
            {'ISINCODE':'LU1075101847','日期':'2026/11/18','基金淨值(金額)':'9','計價幣別':'美元','基金代碼':'BAD'},
        ]
        s['overseas_dividends']['rows']=[{'基金代碼':'GS-ZAR-X','基金幣別':'南非幣','除息日':'2026/11/19','配息發放日':'2026/11/23','單位數':'15.7'}]
        doc=mod.update_data({'funds':{}},s,at='2026-11-23T09:00:00+08:00')
        d=doc['funds']['LU1075101847']
        self.assertEqual(len(d['navHistory']),2)
        mod.attach_authorized_basis(dict(d,kind='overseas'))
        self.assertEqual(d['dividends'][0]['preExNav'],700.0)
        self.assertEqual(d['dividends'][0]['preExNavOrigin'],'open-data')
        self.assertAlmostEqual(d['dividends'][0]['perUnit']/d['dividends'][0]['preExNav']*1200,26.9142857,places=4)
    def test_missing_previous_day_does_not_fake_rate(self):
        s=self.sources()
        s['overseas_nav']['rows']=[{'ISINCODE':'LU1075101847','日期':'2026/11/20','基金淨值':'705.08','計價幣別':'南非幣','基金代碼':'GS-ZAR-X'}]
        s['overseas_dividends']['rows']=[{'基金代碼':'GS-ZAR-X','基金幣別':'南非幣','除息日':'2026/11/03','單位數':'15.7'}]
        d=mod.update_data({'funds':{}},s,at='2026-11-23T09:00:00+08:00')['funds']['LU1075101847']
        mod.attach_authorized_basis(dict(d,kind='overseas'))
        self.assertNotIn('preExNav',d['dividends'][0])
    def test_history_is_merged_across_public_updates(self):
        old={'navHistory':[{'navDate':'2026-11-18','nav':715,'dataOrigin':'open-data'},
                           {'navDate':'2026-11-19','nav':1,'dataOrigin':'MoneyDJ'}]}
        fresh={'2026-11-20':{'navDate':'2026-11-20','nav':707,'dataOrigin':'open-data'}}
        h=mod.merge_authorized_history(old,fresh)
        self.assertEqual([r['navDate'] for r in h],['2026-11-20','2026-11-18'])
    def test_sparse_weekday_history_does_not_mislabel_basis(self):
        self.assertTrue(mod._consecutive_valuation_day('2026-11-20','2026-11-23')) # 週五到週一
        self.assertFalse(mod._consecutive_valuation_day('2026-11-16','2026-11-19')) # 缺週二週三
    def test_roc_date_and_csv_encoding(self):
        self.assertEqual(mod.date_str('115/10/15'),'2026-10-15')
        raw='基金代碼,基金名稱,日期,基金淨值,ISINCODE,計價幣別\nGS-ZAR-X,高盛,2026/11/20,703.2,LU1075101847,南非幣\n'
        for encoding in ('utf-8-sig','cp950'):
            rows=mod.parse_csv(raw.encode(encoding))
            self.assertEqual(rows[0]['ISINCODE'],'LU1075101847')

if __name__=='__main__':unittest.main()
