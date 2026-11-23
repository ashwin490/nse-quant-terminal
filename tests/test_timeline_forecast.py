import unittest
import pandas as pd
from forecast_timeline import estimate_target_timeline, completed_bars_only


class TimelineForecastTests(unittest.TestCase):
    def test_rejects_invalid_price_relationship(self):
        bars = pd.DataFrame({'Close':[100.0]*70,'High':[101.0]*70,'Low':[99.0]*70})
        self.assertFalse(estimate_target_timeline(bars,100,97,105).credible)

    def test_too_short_is_not_confident(self):
        bars = pd.DataFrame({'Close':[100.0]*10,'High':[105.0]*10,'Low':[95.0]*10})
        x = estimate_target_timeline(bars,100,104,97)
        self.assertIsNone(x.target_rate_pct)
        self.assertFalse(x.credible)

    def test_both_daily_barriers_use_conservative_stop_first(self):
        bars = pd.DataFrame({'Close':[100.0]*100,'High':[106.0]*100,'Low':[96.0]*100})
        x = estimate_target_timeline(bars,100,105,97,max_sessions=3)
        self.assertEqual(x.target_hits,0)
        self.assertGreater(x.stop_hits,0)

    def test_open_market_discards_today_partial_bar(self):
        idx = pd.to_datetime(['2026-11-20','2026-11-23'])
        bars = pd.DataFrame({'Close':[100.0,101.0],'High':[101.0,102.0],'Low':[99.0,100.0]},index=idx)
        self.assertEqual(len(completed_bars_only(bars, idx[-1].date(), True)),1)
        self.assertEqual(len(completed_bars_only(bars, idx[-1].date(), False)),2)

if __name__=='__main__': unittest.main()
