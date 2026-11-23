"""Open-only, combined CE/PE/stock accounting; missing marks must not look like zero."""
import sys
from pathlib import Path
import unittest
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from portfolio_overview import portfolio_open_snapshot

class PortfolioOverviewTests(unittest.TestCase):
    def test_empty_ledgers(self):
        s=portfolio_open_snapshot(pd.DataFrame(),pd.DataFrame())
        self.assertEqual(s['open_total'],0)
        self.assertTrue(s['complete'])
        self.assertEqual(s['paper_pnl_inr'],0)

    def test_combines_open_stocks_and_both_option_sides(self):
        eq=pd.DataFrame([
            {'ticker':'AAA','status':'ACTIVE','capital_allocated':10000,'pnl_pct':3,
             'entry_price':100,'latest_price':103},
            {'ticker':'BBB','status':'LOSS','capital_allocated':10000,'pnl_pct':-10,
             'entry_price':100,'latest_price':90},
        ])
        opt=pd.DataFrame([
            {'status':'ACTIVE','option_contract':'XYZ 100 CE (live quote)','total_capital':3000,
             'pnl_pct':-20,'entry_premium':30,'current_option_price':24},
            {'status':'ACTIVE','option_contract':'XYZ 90 PE (live quote)','total_capital':2000,
             'pnl_pct':25,'entry_premium':20,'current_option_price':25},
            {'status':'EXPIRED','option_contract':'OLD 50 CE','total_capital':1000,
             'pnl_pct':-100,'entry_premium':10,'current_option_price':0},
        ])
        s=portfolio_open_snapshot(eq,opt)
        self.assertEqual((s['open_total'],s['shares'],s['calls'],s['puts']),(3,1,1,1))
        self.assertEqual(s['paper_pnl_inr'],200) # 300-600+500
        self.assertTrue(s['complete'])

    def test_missing_price_is_partial_not_zero(self):
        eq=pd.DataFrame([{'ticker':'XYZ','status':'ACTIVE','capital_allocated':20000,
                          'pnl_pct':3,'entry_price':100,'latest_price':None}])
        s=portfolio_open_snapshot(eq,pd.DataFrame())
        self.assertEqual(s['open_total'],1)
        self.assertEqual(s['missing_count'],1)
        self.assertFalse(s['complete'])
        self.assertEqual(s['valued_count'],0)

    def test_nan_mark_is_unverified(self):
        eq=pd.DataFrame([{'ticker':'XYZ','status':'ACTIVE','capital_allocated':20000,
                          'pnl_pct':float('nan'),'entry_price':100,'latest_price':110}])
        self.assertEqual(portfolio_open_snapshot(eq,pd.DataFrame())['missing_count'],1)

    def test_inactive_word_does_not_count_as_active(self):
        eq=pd.DataFrame([{'ticker':'ZZZ','status':'INACTIVE','capital_allocated':100,
                          'pnl_pct':99,'entry_price':1,'latest_price':2}])
        s=portfolio_open_snapshot(eq,pd.DataFrame())
        self.assertEqual(s['open_total'],0)

    def test_unknown_option_type_still_counted(self):
        opt=pd.DataFrame([{'status':'ACTIVE','option_contract':'UNKNOWN','total_capital':2000,
                           'pnl_pct':5,'entry_premium':20,'current_option_price':21}])
        s=portfolio_open_snapshot(pd.DataFrame(),opt)
        self.assertEqual(s['open_total'],1)
        self.assertEqual(s['other_options'],1)

if __name__=='__main__':unittest.main()
