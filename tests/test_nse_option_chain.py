"""Synthetic exchange payloads: tests cannot establish live NSE accessibility."""
from datetime import date, datetime, timedelta
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from nse_option_chain import (expiry_labels, supported_expiries, matching_rows,
    nearby_strikes, exact_contract, quote_is_fresh, exchange_timestamp,
    bid_ask, underlying_value, positive_number)
from quant_guardrails import review_long_option

NOW = datetime(2026,11,23,13,15,0)


def leg(side='CE', bid=12.0, ask=12.1, expiry='29-Dec-2026'):
    return {'bidprice':bid,'askPrice':ask,'bidQty':30,'askQty':30,
            'openInterest':12000,'impliedVolatility':24,
            'expiryDate':expiry,'underlyingValue':1000}


def chain(expiry='29-Dec-2026', minute='13:10:00'):
    return {'records':{
        'timestamp':f'23-Nov-2026 {minute}',
        'expiryDates':['24-Nov-2026',expiry],
        'underlyingValue':1000,
        'data':[{'strikePrice':x,'expiryDate':expiry,'CE':leg('CE'), 'PE':leg('PE')}
                for x in (950,975,1000,1010,1020,1050,1100)]}}


class NseChainTests(unittest.TestCase):
    def test_expiry_uses_real_exchange_and_month_lot(self):
        chosen = supported_expiries(chain(), {'NOV-26':100,'DEC-26':250}, NOW.date())
        self.assertEqual(chosen, [('29-Dec-2026',36,250)])

    def test_no_inferred_lot_from_wrong_month(self):
        self.assertEqual(supported_expiries(chain(), {'NOV-26':100}, NOW.date()),[])

    def test_contract_info_top_level_expiries(self):
        data={'expiryDates':['24-Nov-2026','29-Dec-2026'],'strikePrice':[990,1000]}
        self.assertEqual(supported_expiries(data,{'DEC-26':500},NOW.date()),[('29-Dec-2026',36,500)])

    def test_expiry_labels_no_guessed_calendar(self):
        self.assertEqual(expiry_labels({'records':{}}),[])
        self.assertEqual(expiry_labels({'expiryDates':['bad','29-dec-2026','29-Dec-2026']}),['29-Dec-2026'])

    def test_v3_exact_expiry_rows(self):
        p=chain()
        p['records']['data'].append({'strikePrice':990,'expiryDate':'28-Jan-2027', 'CE':leg()})
        self.assertEqual(len(matching_rows(p,'29-Dec-2026')),7)
        self.assertEqual(matching_rows(p,'28-Jan-2027')[0]['strikePrice'],990)

    def test_filtered_v3_rows(self):
        p={'filtered':{'data':[{'strikePrice':1000,'CE':leg('CE'),'PE':leg('PE')}]}}
        self.assertEqual(len(matching_rows(p,'29-Dec-2026')),1)

    def test_no_expiry_cross_mix(self):
        self.assertEqual(matching_rows(chain(),'30-Dec-2026'),[])

    def test_malformed_leg_does_not_crash_chain(self):
        self.assertEqual(matching_rows({'records':{'data':[{'strikePrice':1000,'CE':'bad'}]}},'29-Dec-2026'),[])

    def test_quote_is_fresh_only_from_exchange_clock(self):
        self.assertTrue(quote_is_fresh(chain(),NOW))
        self.assertFalse(quote_is_fresh(chain(minute='12:40:00'),NOW))
        self.assertFalse(quote_is_fresh({'records':{'data':chain()['records']['data']}},NOW))

    def test_future_timestamp_rejected(self):
        self.assertFalse(quote_is_fresh(chain(minute='13:30:00'),NOW))

    def test_invalid_timestamp_rejected(self):
        self.assertIsNone(exchange_timestamp({'records':{'timestamp':'yesterday'}}))

    def test_both_ce_and_pe_nearby(self):
        rows=matching_rows(chain(),'29-Dec-2026')
        self.assertEqual(nearby_strikes(rows,'CE',1000)[0]['strikePrice'],1010)
        self.assertEqual(nearby_strikes(rows,'PE',1000)[0]['strikePrice'],1000)
        self.assertEqual(len(nearby_strikes(rows,'CE',1000)),6)
        self.assertIsNotNone(exact_contract(rows,'PE',975))

    def test_invalid_first_strike_can_use_next(self):
        rows=matching_rows(chain(),'29-Dec-2026')
        rows[3]['CE']['askPrice']=0
        nearby=nearby_strikes(rows,'CE',1000)
        self.assertEqual(bid_ask(nearby[0]['CE']),(0,0))
        self.assertGreater(bid_ask(nearby[1]['CE'])[0],0)

    def test_missing_or_crossed_bid_ask_disallowed(self):
        self.assertEqual(bid_ask({'bidprice':10,'askPrice':9}),(0,0))
        self.assertEqual(bid_ask({'lastPrice':10}),(0,0))
        self.assertEqual(bid_ask({'bidprice':9,'askPrice':10,'askQty':0}),(0,0))
        self.assertEqual(bid_ask({'bidPrice':9,'askprice':10}),(9,10))

    def test_nan_or_infinite_numeric_fields_disallowed(self):
        self.assertEqual(positive_number('NaN'),0)
        self.assertEqual(positive_number('Infinity'),0)
        self.assertEqual(positive_number(None),0)

    def test_valid_v3_leg_passes_unchanged_guardrails(self):
        p=chain()
        rows=matching_rows(p,'29-Dec-2026')
        for side, move, rsi in [('CE',2.5,60),('PE',-2.5,42)]:
            data=nearby_strikes(rows,side,1000)[0][side]
            bid, ask=bid_ask(data)
            review=review_long_option(kind=side,spot=1000,strike=1010 if side=='CE' else 1000,
                bid=bid,ask=ask,lot_size=250,days_left=36,
                open_interest=data['openInterest'],iv_pct=24,realized_vol_pct=25,
                ret_5d_pct=move,rsi=rsi,recent_filing_risk=False,
                equity_exposure=0,option_exposure=0,quote_fresh=quote_is_fresh(p,NOW))
            self.assertEqual(review.decision,'CONSIDER')

    def test_invalid_bid_ask_cannot_generate_recommendation(self):
        data=leg(bid=0,ask=12)
        bid,ask=bid_ask(data)
        self.assertEqual((bid,ask),(0,0))

    def test_underlying_value_from_leg_fallback(self):
        p={'records':{'data':[{'CE':leg()}]}}
        self.assertEqual(underlying_value(p),1000)


if __name__ == '__main__': unittest.main()
