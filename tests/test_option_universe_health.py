import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from option_universe_health import validate_fno_universe


class OptionUniverseHealthTests(unittest.TestCase):
    def test_one_symbol_is_incomplete_not_full_universe(self):
        result = validate_fno_universe({'RELIANCE': {'DEC-26': 500}}, ['RELIANCE'])
        self.assertFalse(result['plausible'])
        self.assertEqual(result['matched'], 1)
        self.assertIn('Only 1 matching', result['problem'])

    def test_well_formed_larger_overlap(self):
        names = [f'S{i}' for i in range(62)]
        result = validate_fno_universe({s:{'DEC-26':100} for s in names}, names)
        self.assertTrue(result['plausible'])
        self.assertEqual(result['matched'], 62)
        self.assertEqual(len(result['matched_symbols']), 62)

    def test_mismatch_causes_fail_closed(self):
        result = validate_fno_universe({'A': {'DEC-26': 100}, 'B': {'DEC-26': 90}}, ['C', 'D'])
        self.assertFalse(result['plausible'])
        self.assertEqual(result['matched'], 0)

    def test_does_not_guess_lots(self):
        result = validate_fno_universe({}, ['RELIANCE'])
        self.assertFalse(result['plausible'])
        self.assertEqual(result['matched_symbols'], [])

if __name__ == '__main__':
    unittest.main()
