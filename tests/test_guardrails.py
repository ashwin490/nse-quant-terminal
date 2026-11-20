import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quant_guardrails import (remaining_capital,allowable_equity_shares,can_allocate_option,
                              review_long_option,CAPITAL_LIMIT_INR)


def example(**override):
    params = dict(kind="CE",spot=1000,strike=1010,bid=11.9,ask=12,lot_size=1000,
                  days_left=25,open_interest=12000,iv_pct=23,realized_vol_pct=24,
                  ret_5d_pct=2.5,rsi=60,recent_filing_risk=False,
                  equity_exposure=0,option_exposure=0,quote_fresh=True)
    params.update(override)
    return review_long_option(**params)

class PortfolioTests(unittest.TestCase):
    def test_combined_cap(self):
        self.assertEqual(remaining_capital(900_000,100_000),0)
        self.assertEqual(remaining_capital(950_000,10_000),40_000)
    def test_more_than_cap(self):
        self.assertEqual(remaining_capital(900_000,200_000),0)
    def test_invalid_negative(self):
        with self.assertRaises(ValueError): remaining_capital(-1,0)
    def test_share_size_respects_remaining(self):
        qty=allowable_equity_shares(2000,1900,900_000,20_000,0)
        self.assertLessEqual(qty*2000,80_000)
    def test_risk_cap(self):
        self.assertEqual(allowable_equity_shares(100,90,0,0,30_000),0)
    def test_expensive_share_skipped(self):
        self.assertEqual(allowable_equity_shares(120_000,115_000,940_000,30_000),0)
    def test_option_total_cap(self):
        self.assertFalse(can_allocate_option(25_000,0,190_000))
        self.assertTrue(can_allocate_option(10_000,0,180_000))
    def test_lakh_ceiling(self):
        self.assertEqual(CAPITAL_LIMIT_INR,1_000_000)

class OptionTests(unittest.TestCase):
    def test_call_positive_reason(self):
        x=example()
        self.assertEqual(x.decision,"CONSIDER")
        self.assertEqual(x.breakeven_spot,1022)
        self.assertEqual(x.max_loss_inr,12_000)
    def test_put_breakeven(self):
        x=example(kind="PE",strike=990,ret_5d_pct=-2.5,rsi=42)
        self.assertEqual(x.decision,"CONSIDER")
        self.assertEqual(x.breakeven_spot,978)
    def test_stale_quote_rejected(self):
        self.assertEqual(example(quote_fresh=False).decision,"AVOID")
    def test_wide_spread_rejected(self):
        self.assertEqual(example(bid=5,ask=12).decision,"AVOID")
    def test_expiring_option_rejected(self):
        self.assertEqual(example(days_left=2).decision,"AVOID")
    def test_large_lot_rejected(self):
        self.assertEqual(example(lot_size=5000).decision,"AVOID")
    def test_no_trend_is_watch(self):
        self.assertEqual(example(ret_5d_pct=0,rsi=50).decision,"WATCH")
    def test_market_risk(self):
        self.assertEqual(example(recent_filing_risk=True).decision,"AVOID")
    def test_cap(self):
        self.assertEqual(example(equity_exposure=995_000).decision,"AVOID")
    def test_wide_volatility(self):
        self.assertEqual(example(iv_pct=60,realized_vol_pct=15).decision,"AVOID")

if __name__=='__main__': unittest.main()
