"""Latest closed trade of each symbol should appear without a stock selector."""
import sys
import unittest
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from learning_feed import latest_completed_by_stock

class LatestLearningFeedTests(unittest.TestCase):
    def test_latest_finished_record_per_distinct_stock(self):
        data = pd.DataFrame([
            {'ticker': 'AAA', 'status': 'WIN', 'date_str': '2026-11-01', 'exit_timestamp': '2026-11-03 11:00:00', 'pnl_pct': 4},
            {'ticker': 'BBB', 'status': 'LOSS', 'date_str': '2026-11-04', 'exit_timestamp': '2026-11-05 12:00:00', 'pnl_pct': -2},
            {'ticker': 'AAA', 'status': 'LOSS', 'date_str': '2026-11-07', 'exit_timestamp': '2026-11-10 09:20:00', 'pnl_pct': -3},
            {'ticker': 'AAA', 'status': 'ACTIVE', 'date_str': '2026-11-22', 'pnl_pct': 1},
            {'ticker': 'CCC', 'status': 'ACTIVE', 'date_str': '2026-11-22', 'pnl_pct': 1},
        ])
        before = data.copy(deep=True)
        result = latest_completed_by_stock(data)
        self.assertEqual([r['ticker'] for r in result], ['AAA', 'BBB'])
        self.assertEqual(result[0]['pnl_pct'], -3)
        pd.testing.assert_frame_equal(data, before)

    def test_returns_at_least_15_distinct_stocks_when_available(self):
        data = pd.DataFrame([{'ticker': f'STOCK{i:02d}', 'date_str': f'2026-11-{i+1:02d}',
                              'status': '🎯 WIN (TARGET HIT)' if i % 2 else '🛑 LOSS (STOPPED OUT)',
                              'pnl_pct': float(i)} for i in range(20)])
        result = latest_completed_by_stock(data)
        self.assertEqual(len(result), 20)
        self.assertEqual(len({r['ticker'] for r in result[:15]}), 15)
        self.assertEqual(result[0]['ticker'], 'STOCK19')

    def test_missing_and_unknown_status_are_not_fabricated_outcomes(self):
        df = pd.DataFrame([
            {'ticker': 'AAA', 'status':'ACTIVE', 'date_str':'2026-11-23'},
            {'ticker':'BBB', 'status':'UNCLASSIFIED','date_str':'2026-11-22'},
            {'ticker':'CCC','status':'BREAK-EVEN','date_str':'2026-11-21'},
            {'ticker':'DDD','status':'EXPIRED','date_str':'2026-11-20'},
        ])
        self.assertEqual([r['ticker'] for r in latest_completed_by_stock(df)], ['CCC','DDD'])

    def test_missing_date_and_empty_input_safe(self):
        df = pd.DataFrame([{'ticker':' aaa ', 'status':'WIN'},
                           {'ticker':'bbb','status':'LOSS'},
                           {'ticker':'', 'status':'WIN'}])
        result = latest_completed_by_stock(df)
        self.assertEqual(len(result), 2)
        self.assertEqual(latest_completed_by_stock(pd.DataFrame()), [])
        self.assertEqual(latest_completed_by_stock(None), [])

    def test_actual_exit_date_beats_entry_date(self):
        df = pd.DataFrame([
            {'ticker':'A','status':'LOSS','date_str':'2026-11-12','exit_timestamp':'2026-11-14 12:00'},
            {'ticker':'B','status':'WIN','date_str':'2026-11-13','exit_timestamp':'2026-11-15 10:00'},
        ])
        self.assertEqual([r['ticker'] for r in latest_completed_by_stock(df)], ['B','A'])

    def test_ui_has_no_stock_dropdown_or_count_dropdown(self):
        app = (Path(__file__).resolve().parents[1]/'app.py').read_text(encoding='utf-8')
        ui_section = app.split('with page_learning:', 1)[1].split('with page_ledger:', 1)[0]
        self.assertNotIn('dark_learning_stock', ui_section)
        self.assertNotIn('dark_learning_filter', ui_section)
        self.assertNotIn('dark_learning_count', ui_section)
        self.assertIn("'learning_feed_visible'] = 15", ui_section)
        self.assertIn('Show 15 more stocks', ui_section)

if __name__ == '__main__': unittest.main()
