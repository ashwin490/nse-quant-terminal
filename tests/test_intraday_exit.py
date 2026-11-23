import unittest
from datetime import datetime
import pandas as pd
from intraday_exit import scan_intraday_minutes


class IntradayExitTests(unittest.TestCase):
    def bars(self, rows, start='2026-11-23 10:00:00'):
        return pd.DataFrame(rows, columns=['Open','High','Low','Close'],
            index=pd.date_range(start, periods=len(rows), freq='min'))

    def scan(self, rows, start='2026-11-23 10:00:00', entry_at='2026-11-23 10:00:00', now='2026-11-23 10:15:00', **kw):
        return scan_intraday_minutes(self.bars(rows, start), entry=100, target=105, stop=97,
            from_time=datetime.fromisoformat(entry_at), as_of=datetime.fromisoformat(now), **kw)

    def test_target_touch_then_retrace_is_still_win(self):
        rows = [[100,101,99,100]] * 15
        rows[6] = [101,105.4,100.6,102]
        self.assertEqual(self.scan(rows).outcome, 'WIN')

    def test_stop_touch_then_retrace_is_still_stop(self):
        rows = [[100,101,99,100]] * 15
        rows[6] = [99,100,96.5,99.5]
        self.assertEqual(self.scan(rows).outcome, 'STOP')

    def test_stop_first_when_both_within_same_minute(self):
        rows = [[100,101,99,100]] * 15
        rows[2] = [100,105.2,96.8,102]
        x = self.scan(rows)
        self.assertEqual(x.outcome,'STOP')
        self.assertTrue(x.ambiguous)

    def test_preentry_minute_is_ignored(self):
        rows = [[100,101,99,100]] * 15
        rows[0] = [100,108,99,103]
        x = self.scan(rows, entry_at='2026-11-23 10:00:40')
        self.assertIsNone(x.outcome)
        self.assertTrue(x.covered)

    def test_incomplete_last_minute_is_ignored(self):
        rows = [[100,101,99,100]] * 15
        rows[14] = [100,107,99,106]
        x = self.scan(rows, now='2026-11-23 10:14:40')
        self.assertIsNone(x.outcome)

    def test_missing_gap_means_unverified(self):
        rows = self.bars([[100,101,99,100]] * 4)
        later = self.bars([[100,106,99,101]], start='2026-11-23 10:10:00')
        bars = pd.concat([rows, later])
        x = scan_intraday_minutes(bars, entry=100,target=105,stop=97,
            from_time=datetime(2026,11,23,10),as_of=datetime(2026,11,23,10,15))
        self.assertIsNone(x.outcome)
        self.assertFalse(x.covered)

    def test_delayed_feed_does_not_claim_full_coverage(self):
        x = self.scan([[100,101,99,100]]*7)
        self.assertIsNone(x.outcome)
        self.assertFalse(x.covered)
        self.assertIn('latest interval', x.note)

    def test_breakeven_only_next_minute(self):
        rows = [[100,101,99,100]] * 15
        rows[5] = [100,104,99,103]
        rows[6] = [103,103,99.7,100.5]
        x = self.scan(rows,be_floor=100.2,be_trigger=103)
        self.assertEqual(x.outcome,'STOP')
        self.assertEqual(x.fill,100.2)

if __name__ == '__main__':
    unittest.main()
