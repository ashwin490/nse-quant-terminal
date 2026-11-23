import unittest

from scan_health import new_option_scan_health, option_scan_summary, option_http_note


class TestScanHealth(unittest.TestCase):
    def test_distinguishes_selected_and_actually_checked(self):
        s = new_option_scan_health(35, 218)
        s['underlyings_attempted'] = 20
        s['directional'] = 12
        msg = option_scan_summary(s)
        self.assertIn('20/35 selected from 218 F&O shares actually attempted', msg)
        self.assertIn('0 fresh chains', msg)
        self.assertIn('0 risk reviews', msg)
        self.assertNotIn('35 contract reviews', msg)

    def test_freshness_note(self):
        s = new_option_scan_health(35, 218)
        s.update(directional=12, chains_received=4, stale_or_untimed=4)
        msg = option_scan_summary(s)
        self.assertIn('stale/missing', msg)
        self.assertIn('Stale or timestamp-free chain: 4', msg)

    def test_completed_reviews(self):
        s = new_option_scan_health(35, 218)
        s.update(directional=12, chains_received=4, fresh_chains=3,
                 expiry_matches=2, contract_reviews=4, consider=1, watch=2, avoid=1)
        self.assertIn('1 CONSIDER · 2 WATCH · 1 AVOID', option_scan_summary(s))

    def test_quote_rejection_breakdown(self):
        s = new_option_scan_health(5, 218)
        s.update(directional=2, chains_received=2, fresh_chains=2,
                 strikes_examined=14, missing_bid_ask=10, valid_bid_ask=4,
                 contract_reviews=4, guardrail_rejections=3)
        msg = option_scan_summary(s)
        self.assertIn('14 nearby CE/PE strikes', msg)
        self.assertIn('Missing/crossed bid-ask or empty displayed depth: 10', msg)
        self.assertIn('Failed liquidity/cost/other risk guardrails: 3', msg)

    def test_http_summary_does_not_expose_urls(self):
        s = new_option_scan_health()
        s['http_status_counts'] = {'v3 403': 3, 'legacy 200': 1}
        note = option_http_note(s)
        self.assertIn('v3 403: 3', note)
        self.assertIn('legacy 200: 1', note)
        self.assertNotIn('https://', note)


if __name__ == '__main__':
    unittest.main()
