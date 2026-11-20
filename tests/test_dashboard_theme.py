"""Dependency-free UI tests for HTML escaping and graph-free rendering."""
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard_theme import hero, kpis, item_card, THEME_CSS

class DashboardThemeTests(unittest.TestCase):
    def test_html_escapes_user_strings(self):
        result=item_card('<script>alert(1)</script>', 'WATCH', '<img src=x>',
                         [('Cost', '₹50,000')], '<b>unsafe</b>')
        self.assertNotIn('<script>',result)
        self.assertNotIn('<img src=x>',result)
        self.assertIn('&lt;script&gt;',result)
        self.assertIn('&lt;b&gt;unsafe&lt;/b&gt;',result)
    def test_css_does_not_embed_price_charts(self):
        self.assertIn('.hero-new',THEME_CSS)
        self.assertIn('.research',THEME_CSS)
        self.assertIn('NSE market closed',hero('20 Nov',False))
        self.assertIn('₹10,00,000', kpis([('Budget','₹10,00,000','INR','🏦','teal')]))
if __name__=='__main__': unittest.main()
