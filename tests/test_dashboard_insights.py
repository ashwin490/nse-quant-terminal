import unittest
from pathlib import Path
import sys
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_insights import outcome_of, summarize_share_journal


class DashboardInsightsTests(unittest.TestCase):
    def test_consistent_completed_and_recent_counts(self):
        rows = []
        for i in range(20):
            rows.append({'ticker':f'OPEN{i}', 'status':'ACTIVE', 'pnl_pct':0, 'date_str':'2026-11-20'})
        for i in range(45):
            rows.append({'ticker':f'WIN{i}', 'status':'🎯 WIN (TARGET HIT)', 'pnl_pct':3.2,
                         'date_str':'2026-11-17', 'exit_timestamp':'2026-11-19 15:31:00'})
        for i in range(41):
            rows.append({'ticker':f'LOSS{i}', 'status':'🛑 LOSS (STOPPED OUT)', 'pnl_pct':-1.4,
                         'date_str':'2026-11-14', 'exit_timestamp':'2026-11-18 15:31:00'})
        summary = summarize_share_journal(pd.DataFrame(rows), recent_limit=4)
        self.assertEqual(summary['settled'], 86)
        self.assertEqual(summary['open'], 20)
        self.assertEqual(len(summary['recent']), 4)
        self.assertAlmostEqual(summary['win_rate'], 100*45/86)
        self.assertEqual(summary['recent'].iloc[0]['ticker'], 'WIN0')

    def test_empty_returns_empty_preview_and_no_win_rate(self):
        summary = summarize_share_journal(pd.DataFrame())
        self.assertEqual(summary['settled'], 0)
        self.assertTrue(summary['recent'].empty)
        self.assertIsNone(summary['win_rate'])

    def test_old_schemas_and_unknowns_are_preserved_not_completed(self):
        rows = pd.DataFrame([
            {'ticker':'ONE', 'status':'OPEN', 'pnl_pct':0, 'last_audited':'2026-11-01'},
            {'ticker':'TWO', 'status':'⏱️ EXPIRED (NO PRICE DATA)', 'pnl_pct':0, 'last_audited':'2026-11-02'},
            {'ticker':'THREE', 'status':'🎯 WIN (TARGET HIT)', 'pnl_pct':1, 'last_audited':'2026-11-03'},
        ])
        summary = summarize_share_journal(rows)
        self.assertEqual(summary['settled'], 2)
        self.assertEqual(summary['decided'], 1)
        self.assertEqual(len(summary['recent']), 2)
        self.assertEqual(summary['recent'].iloc[0]['ticker'], 'THREE')

    def test_failed_status_is_never_inferred(self):
        self.assertEqual(outcome_of('UNKNOWN'), 'OTHER')
        self.assertEqual(outcome_of(None), 'OTHER')
        self.assertEqual(outcome_of('🎯 WIN (TARGET HIT)'), 'WIN')


if __name__ == '__main__':
    unittest.main()
