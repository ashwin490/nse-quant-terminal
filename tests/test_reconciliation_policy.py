import unittest
from reconciliation_policy import is_closed, preserve_local_row, closed_record_conflict


class ReconciliationPolicyTests(unittest.TestCase):
    def test_preserves_finalised_equity_even_if_cloud_timestamp_later(self):
        self.assertTrue(preserve_local_row('🛑 LOSS (STOPPED OUT)',
            '🛑 LOSS (STOPPED OUT)', '2026-11-23 14:15:04', '2026-11-23 14:47:26'))

    def test_preserves_finalised_equity_if_cloud_is_active(self):
        self.assertTrue(preserve_local_row('🎯 WIN (TARGET HIT)', 'ACTIVE',
            '2026-11-23 13:00:00', '2026-11-23 14:55:00'))

    def test_remote_close_wins_over_locally_active_record(self):
        self.assertFalse(preserve_local_row('ACTIVE', '🛑 LOSS (STOPPED OUT)',
            '2026-11-23 14:55:00', '2026-11-23 14:40:00'))

    def test_newer_local_active_checkpoint_preserved(self):
        self.assertTrue(preserve_local_row('ACTIVE', 'ACTIVE',
            '2026-11-23 14:55:00', '2026-11-23 14:30:00'))

    def test_newer_remote_active_checkpoint_can_restore(self):
        self.assertFalse(preserve_local_row('ACTIVE', 'ACTIVE',
            '2026-11-23 14:20:00', '2026-11-23 14:45:00'))

    def test_differing_closed_audit_timestamps_not_a_status_conflict(self):
        self.assertFalse(closed_record_conflict('🛑 LOSS (STOPPED OUT)',
            '🛑 LOSS (STOPPED OUT)', '2026-11-23 14:15:04', '2026-11-23 14:47:26'))

    def test_distinct_final_outcomes_are_flagged(self):
        self.assertTrue(closed_record_conflict('🎯 WIN (TARGET HIT)', '🛑 LOSS (STOPPED OUT)', '', ''))

    def test_matching_closed_records_not_flagged(self):
        self.assertFalse(closed_record_conflict('🎯 WIN (TARGET HIT)',
            '🎯 WIN (TARGET HIT)', '2026-11-23 14:15:04', '2026-11-23 14:15:04'))

    def test_open_not_flagged_as_closed(self):
        self.assertFalse(is_closed('ACTIVE'))
        self.assertFalse(closed_record_conflict('ACTIVE', 'ACTIVE', '', ''))


if __name__ == '__main__':
    unittest.main()
