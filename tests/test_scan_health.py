import unittest

from scan_health import new_option_scan_health, option_scan_summary, option_http_note


class TestScanHealth(unittest.TestCase):
    def test_distinguishes_selected_and_reviewed(self):
        s = new_option_scan_health(35, 218)
        s['directional'] = 12
        msg = option_scan_summary(s)
        self.assertIn('35 of 218 F&O underlyings selected', msg)
        self.assertIn('0 option chains received', msg)
        self.assertIn('NSE option chains were unavailable', msg)
        self.assertNotIn('35 contract reviews', msg)

    def test_freshness_note(self):
        s = new_option_scan_health(35, 218)
        s.update(directional=12, chains_received=4)
        self.assertIn('did not contain verified fresh quotes', option_scan_summary(s))

    def test_completed_reviews(self):
        s = new_option_scan_health(35, 218)
        s.update(directional=12, chains_received=4, fresh_chains=3,
                 expiry_matches=2, contract_reviews=4, consider=1, watch=2, avoid=1)
        self.assertIn('1 CONSIDER, 2 WATCH, 1 AVOID', option_scan_summary(s))

    def test_http_summary_does_not_expose_urls(self):
        s = new_option_scan_health()
        s['http_status_counts'] = {'403': 3, '200': 1}
        note = option_http_note(s)
        self.assertIn('403: 3', note)
        self.assertIn('200: 1', note)
        self.assertNotIn('https://', note)


if __name__ == '__main__':
    unittest.main()
