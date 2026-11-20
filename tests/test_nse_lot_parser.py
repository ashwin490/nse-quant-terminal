import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nse_lot_parser import parse_nse_lot_file


class NseLotParserTests(unittest.TestCase):
    def test_nse_underlying_before_symbol_uses_symbol_not_company_name(self):
        sample = '''NSE F&O Permitted Market Lots\nUNDERLYING,SYMBOL,NOV-26,DEC-26,JAN-27\nRELIANCE INDUSTRIES LIMITED,RELIANCE,500,500,500\nSTATE BANK OF INDIA,SBIN,750,750,900\nHDFC BANK LIMITED,HDFCBANK,650,650,750\n'''
        result = parse_nse_lot_file(sample)
        self.assertEqual(sorted(result), ['HDFCBANK', 'RELIANCE', 'SBIN'])
        self.assertEqual(result['RELIANCE']['NOV-26'], 500)
        self.assertEqual(result['HDFCBANK']['JAN-27'], 750)
        self.assertNotIn('RELIANCE INDUSTRIES LIMITED', result)

    def test_empty_and_invalid_data_never_guess_lot(self):
        self.assertEqual(parse_nse_lot_file(''), {})
        self.assertEqual(parse_nse_lot_file('<html>404</html>'), {})
        self.assertEqual(parse_nse_lot_file('UNDERLYING,SYMBOL,NOV-26\nX,ABC,-20'), {})

    def test_crlf_bom_and_month_names(self):
        self.assertEqual(parse_nse_lot_file('\ufeffUNDERLYING,SYMBOL,Nov-26,Dec-2026\r\nA,ABC,120,130\r\n'),
                         {'ABC': {'NOV-26': 120, 'DEC-26': 130}})

if __name__ == '__main__':
    unittest.main()
