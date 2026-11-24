"""Eleven fund update: offline regression tests (no internet)."""
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
    def test_eleven_exact_funds(self):
        self.assertEqual(len(mod.FUNDS),11)
        self.assertEqual(sum(s['currency']=='ZAR' for s in mod.FUNDS.values()),2)
        self.assertEqual(sum(s['currency']=='USD' for s in mod.FUNDS.values()),9)
        self.assertEqual(mod.FUNDS['LU1075101847']['currency'],'ZAR')
        self.assertEqual(mod.FUNDS['LU1075101847']['type'],'overseas')
        for isin in ('LU1900986214','LU3047255321','LU2110300469'):
            self.assertEqual(mod.FUNDS[isin]['currency'],'USD')
    def test_invesco_e_stable_monthly_share_class(self):
        self.assertEqual(mod.FUNDS['LU2110300469']['type'], 'overseas')
        self.assertEqual(mod.FUNDS['LU2110300469']['currency'], 'USD')
        rows=[
           {'ISINCODE':'LU2110300469','日期':'2026/11/20','基金淨值':'6.6710','計價幣別':'美元','基金代碼':'INV-E-MD1'},
           {'ISINCODE':'LU2110300469','日期':'2026/11/21','基金淨值':'7.00','計價幣別':'南非幣','基金代碼':'INV-WRONG-ZAR'},
           {'ISINCODE':'LU2110300477','日期':'2026/11/21','基金淨值':'10.01','計價幣別':'美元','基金代碼':'INV-E-ACC'},
        ]
        navs,codes=mod.latest_overseas_nav(rows)
        self.assertEqual(navs['LU2110300469']['nav'],6.671)
        self.assertEqual(navs['LU2110300469']['navDate'],'2026-11-20')
        self.assertNotIn('LU2110300477',navs)
        self.assertNotIn('INVWRONGZAR',codes)
        d=mod.update_data({'funds':{}},{name:{'ok':False,'rows':rows if name=='overseas_nav' else []} for name in mod.SOURCES},at='2026-11-24T12:50:00+08:00')['funds']['LU2110300469']
        self.assertEqual(d['navSource'],'集保境外基金公開資料')
        self.assertEqual(d['nav'],6.671)
    def test_invesco_dividend_matches_exact_class(self):
        rows=[
          {'基金代碼':'INV-E-MD1','基金幣別':'美元','除息日':'2026/11/05','配息發放日':'2026/11/09','單位數':'0.051'},
          {'基金代碼':'INV-E-MD1','基金幣別':'南非幣','除息日':'2026/11/06','配息發放日':'2026/11/09','單位數':'5'},
        ]
        result=mod.overseas_dividends(rows,{'INVEMD1':'LU2110300469'})
        self.assertEqual(len(result['LU2110300469']),1)
        self.assertAlmostEqual(result['LU2110300469'][0]['perUnit'],0.051)

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
        self.assertEqual(len(out['funds']),11)
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
    def test_goldman_nov_2026_current_and_annual_yield(self):
        # 人工核對值：除息 2026/11/03、基準淨值 2026/11/02。
        doc=mod.update_data({'funds':{}},self.sources(),at='2026-11-23T20:00:00+08:00')
        mod.enrich_distribution_rates(doc, {'funds':{}}, {})
        record=doc['funds']['LU1075101847']['dividends'][0]
        self.assertEqual(record['exDate'],'2026-11-03')
        self.assertEqual(record['payDate'],'2026-11-06')
        self.assertEqual(record['perUnit'],15.7)
        self.assertEqual(record['preExNavDate'],'2026-11-02')
        self.assertEqual(record['preExNav'],733.71)
        self.assertAlmostEqual(record['perUnit']/record['preExNav']*100,2.14,places=2)
        self.assertAlmostEqual(record['perUnit']/record['preExNav']*1200,25.68,places=2)
        self.assertEqual(len(doc['funds']),11)

    def test_goldman_basis_is_idempotent_across_updates(self):
        doc=mod.update_data({'funds':{}},self.sources(),at='2026-11-23T20:00:00+08:00')
        mod.enrich_distribution_rates(doc, {'funds':{}}, {})
        new=mod.update_data(doc,self.sources(),at='2026-11-23T21:00:00+08:00')
        mod.enrich_distribution_rates(new,doc,{})
        dates=[r['exDate'] for r in new['funds']['LU1075101847']['dividends']]
        self.assertEqual(dates.count('2026-11-03'),1)
        self.assertEqual(new['funds']['LU1075101847']['dividends'][0]['preExNav'],733.71)

    def test_goldman_official_same_day_data_is_not_overridden(self):
        entry={'dividends':[{'exDate':'2026-11-03','perUnit':15.7,
                   'payDate':'2026-11-06','preExNav':733.71,
                   'preExNavDate':'2026-11-02','preExNavOrigin':'open-data'}]}
        mod.add_verified_goldman_november('LU1075101847',entry)
        self.assertEqual(entry['dividends'][0]['preExNavOrigin'],'open-data')

    def test_goldman_conflicting_amount_keeps_source_and_warns(self):
        entry={'dividends':[{'exDate':'2026-11-03','perUnit':16.7,'payDate':'2026-11-06'}]}
        mod.add_verified_goldman_november('LU1075101847',entry)
        self.assertEqual(entry['dividends'][0]['perUnit'],16.7)
        self.assertNotIn('preExNav',entry['dividends'][0])
        self.assertIn('不一致',entry['dividendMessage'])

    def test_goldman_older_date_does_not_populate_december_yield(self):
        entry={'dividends':[{'exDate':'2026-12-03','perUnit':15.7,'payDate':'2026-12-08'}]}
        mod.add_verified_goldman_november('LU1075101847',entry)
        dec=next(r for r in entry['dividends'] if r['exDate']=='2026-12-03')
        self.assertNotIn('preExNav',dec)

    def test_invesco_nov_rate_verified_exact_class_and_period(self):
        doc = mod.update_data({'funds':{}}, self.sources(), at='2026-11-24T16:00:00+08:00')
        mod.enrich_distribution_rates(doc, {'funds':{}}, {})
        row = doc['funds']['LU2110300469']['dividends'][0]
        self.assertEqual(row['exDate'], '2026-11-02')
        self.assertEqual(row['payDate'], '2026-11-11')
        self.assertEqual(row['preExNavDate'], '2026-10-30')
        self.assertEqual(row['preExNav'], 6.734)
        self.assertEqual(row['perUnit'], 0.051)
        self.assertAlmostEqual(row['perUnit']/row['preExNav']*100, .75735075735, places=7)
        self.assertAlmostEqual(row['perUnit']/row['preExNav']*1200, 9.0882090882, places=7)
        self.assertEqual(len(doc['funds']), 11)

    def test_invesco_repeat_update_preserves_one_row(self):
        first = mod.update_data({'funds':{}},self.sources(),at='2026-11-24T16:00:00+08:00')
        mod.enrich_distribution_rates(first,{'funds':{}},{})
        second = mod.update_data(first,self.sources(),at='2026-11-24T16:20:00+08:00')
        mod.enrich_distribution_rates(second,first,{})
        rows = second['funds']['LU2110300469']['dividends']
        self.assertEqual(sum(r['exDate']=='2026-11-02' for r in rows), 1)
        self.assertEqual(rows[0]['preExNav'], 6.734)

    def test_invesco_conflicting_amount_does_not_force_rate(self):
        entry={'dividends':[{'exDate':'2026-11-02', 'perUnit':0.061}]}
        mod.add_verified_invesco_november('LU2110300469',entry)
        self.assertEqual(entry['dividends'][0]['perUnit'],0.061)
        self.assertNotIn('preExNav',entry['dividends'][0])
        self.assertIn('不一致',entry['dividendMessage'])

    def test_invesco_other_class_or_future_period_not_modified(self):
        entry={'dividends':[{'exDate':'2026-12-01','perUnit':.051}]}
        mod.add_verified_invesco_november('LU2110300469',entry)
        self.assertNotIn('preExNav',entry['dividends'][0])
        other={'dividends':[{'exDate':'2026-11-02','perUnit':.051}]}
        mod.add_verified_invesco_november('LU1900986214',other)
        self.assertNotIn('preExNav',other['dividends'][0])

    def test_invesco_open_data_basis_is_not_overwritten(self):
        entry={'dividends':[{'exDate':'2026-11-02','perUnit':.051,
              'preExNav':6.734,'preExNavDate':'2026-10-30','preExNavOrigin':'open-data'}]}
        mod.add_verified_invesco_november('LU2110300469',entry)
        self.assertEqual(entry['dividends'][0]['preExNavOrigin'],'open-data')

    def test_roc_date_and_csv_encoding(self):
        self.assertEqual(mod.date_str('115/10/15'),'2026-10-15')
        raw='基金代碼,基金名稱,日期,基金淨值,ISINCODE,計價幣別\nGS-ZAR-X,高盛,2026/11/20,703.2,LU1075101847,南非幣\n'
        for encoding in ('utf-8-sig','cp950'):
            rows=mod.parse_csv(raw.encode(encoding))
            self.assertEqual(rows[0]['ISINCODE'],'LU1075101847')

if __name__=='__main__':unittest.main()
