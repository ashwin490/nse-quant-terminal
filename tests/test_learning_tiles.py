"""Per-trade, no-causal-claim cards and escaped presentation."""
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from learning_tiles import build_learning_tile
from premium_dark import learning_tile, theme_css


class LearningTileTests(unittest.TestCase):
    def test_win_message_does_not_claim_model_retrained(self):
        tile=build_learning_tile({'ticker':'AAA','date_str':'2026-11-20','status':'🎯 WIN',
                                  'pnl_pct':5.3,'features_json':'{}'})
        self.assertIn('later, unseen periods',tile['next_test'])
        self.assertFalse(tile['training_candidate'])
        self.assertEqual(tile['status'],'WIN')
        self.assertIn('AAA',learning_tile(tile))

    def test_loss_with_known_features_describes_next_test(self):
        features='{"raw_features":{"atr_pct":3.2,"rsi_14":61.0,"rvol":1.4}}'
        t=build_learning_tile({'status':'LOSS','features_json':features,'pnl_pct':-4.2})
        self.assertIn('3.2%',t['entry_context'])
        self.assertIn('RSI 61.0',t['entry_context'])
        self.assertIn('against winning setups',t['next_test'])
        self.assertIn('-4.20%',learning_tile(t))

    def test_open_never_train_claim(self):
        t=build_learning_tile({'status':'ACTIVE','pnl_pct':1.1})
        self.assertFalse(t['training_candidate'])
        self.assertIn('Wait for a confirmed exit',t['next_test'])
        self.assertIn('OPEN / PENDING',learning_tile(t))

    def test_xss_escaped(self):
        html=learning_tile({'symbol':'<script>', 'status':'WIN', 'date':'<img>',
                            'observation':'<b>hello</b>', 'next_test':'<iframe>',
                            'entry_context':'<svg onload=alert(1)>', 'pnl':1.2})
        self.assertNotIn('<script>',html)
        self.assertNotIn('<iframe>',html)
        self.assertNotIn('<b>hello</b>',html)
        self.assertNotIn('<svg',html)
        self.assertIn('&lt;script&gt;',html)

    def test_no_pnl_does_not_show_nan(self):
        html=learning_tile({'status':'ACTIVE', 'pnl':float('nan')})
        self.assertIn('P/L unavailable',html)
        self.assertNotIn('nan%',html)

    def test_each_theme_has_learning_tiles(self):
        for theme in ('Midnight','Nebula','Deep Ocean'):
            self.assertIn('.learning-tiles',theme_css(theme))

if __name__=='__main__':unittest.main()
