import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history_integrity import history_state


class HistoryIntegrityTests(unittest.TestCase):
    def test_old_share_records_missing_blocks_allocations(self):
        state = history_state(0, 20, True, cloud_shares=0)
        self.assertFalse(state['allow_new_paper_entries'])
        self.assertEqual(state['missing'], 20)
        self.assertIn('returned no shares', state['reason'])

    def test_restored_20_records_allows_paper_entries(self):
        state = history_state(20, 20, True, cloud_shares=20)
        self.assertTrue(state['allow_new_paper_entries'])
        self.assertEqual(state['missing'], 0)

    def test_missing_cloud_diagnostic(self):
        state = history_state(3, 20, False)
        self.assertIn('no Supabase backup', state['reason'])

    def test_cloud_error_is_explicit(self):
        state = history_state(2, 20, True, cloud_failed=True)
        self.assertIn('error', state['reason'])

    def test_option_history_floor_is_independent(self):
        state = history_state(0, 21, True, cloud_shares=0, record_label='option')
        self.assertFalse(state['allow_new_paper_entries'])
        self.assertEqual(state['missing'], 21)
        self.assertIn('no options', state['reason'])

    def test_zero_baseline_explicit_fresh_start(self):
        state = history_state(0, 0, False)
        self.assertTrue(state['allow_new_paper_entries'])


if __name__ == '__main__':
    unittest.main()
