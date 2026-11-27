import unittest
from reconciliation_policy import closed_record_conflict, is_closed

class ReconciliationStatusRegressionTests(unittest.TestCase):
    def test_legacy_active_with_icon_is_open(self):
        self.assertFalse(is_closed('🟢 ACTIVE'))
        self.assertTrue(is_closed('INACTIVE'))

    def test_equivalent_closed_outcomes_ignore_audit_timestamp_changes(self):
        self.assertFalse(closed_record_conflict('🎯 WIN (TARGET HIT)', 'WIN', '2026-11-23 13:00', '2026-11-27 09:00'))
        self.assertFalse(closed_record_conflict('⏱️ EXPIRED (TIME EXIT)', 'TIME EXIT', '2026-11-26', '2026-11-27'))
        self.assertFalse(closed_record_conflict('🛡️ BREAK-EVEN (PROTECTED EXIT)', 'BE', '', ''))

    def test_truly_different_outcomes_warn(self):
        self.assertTrue(closed_record_conflict('🛑 LOSS (STOPPED OUT)', 'WIN', '', ''))
