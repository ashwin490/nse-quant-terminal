"""Execute the real option scanner under controlled synthetic dependency stubs.

This tests the app function without importing Streamlit or using NSE/network/DB.
Not a live NSE/Streamlit Cloud end-to-end test.
"""
import ast
from datetime import datetime
import math
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from quant_guardrails import review_long_option
from nse_option_chain import nearby_strikes,matching_rows,bid_ask,underlying_value,exchange_timestamp

APP = Path(__file__).resolve().parents[1] / 'app.py'


def _load_scanner(ns):
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='scan_option_candidates')
    sub = ast.Module(body=[fn], type_ignores=[])
    exec(compile(ast.fix_missing_locations(sub),str(APP),'exec'), ns)
    return ns['scan_option_candidates']


class FakeCon:
    writes=[]
    def execute(self,query,values=None):
        self.writes.append((query,values))
    def close(self):pass


def _chain(ask=12.1):
    legs=lambda: {'bidprice':12.0,'askPrice':ask,'bidQty':30,'askQty':30,
                  'openInterest':12000,'impliedVolatility':24,
                  'underlyingValue':1000,'expiryDate':'29-Dec-2026'}
    rows=[{'strikePrice':strike,'expiryDate':'29-Dec-2026','CE':legs(),'PE':legs()}
          for strike in (970,985,995,1000,1010,1020,1040)]
    return {'records':{'timestamp':'23-Nov-2026 13:10:00','expiryDates':['29-Dec-2026'],
                       'underlyingValue':1000,'data':rows}}


class ScannerIntegration(unittest.TestCase):
    def _build(self,ask=12.1,force_timeout=False):
        now=datetime(2026,11,23,13,15)
        payload=_chain(ask)
        names=['ALPHA','BETA']
        def historical(*args):
            x=np.arange(40,dtype=float)
            price=950 + 0.8*x + 7*np.sin(x*1.8)
            return pd.DataFrame({'Close':price})
        st=types.SimpleNamespace(session_state={})
        fno=types.ModuleType('option_universe_health')
        fno.validate_fno_universe=lambda lots, all_eq: {'plausible':True,'problem':'',
                                                       'matched_symbols':names}
        ns={'st':st,'pd':pd,'np':np,'math':math,'time':__import__('time'),
            'datetime':datetime,'ist_now':lambda:now,'is_entry_window_open':lambda:True,
            '_current_nse_lots':lambda:{n:{'DEC-26':250} for n in names},
            'get_all_nse_symbols':lambda:(names,None),
            'prefetch_histories':lambda *a,**kw:None,
            'get_open_exposures':lambda:(0,0), 'option_paper_feedback':lambda:{},
            'get_history_fast':historical,
            '_exchange_equity_option_chain':lambda n,**kw: (payload,'29-Dec-2026',36,250),
            'matching_rows':matching_rows,'nearby_strikes':nearby_strikes,
            'bid_ask':bid_ask,'underlying_value':underlying_value,
            'exchange_timestamp':exchange_timestamp,'_quote_age_is_valid':lambda p:True,
            'review_long_option':review_long_option,
            'evaluate_nse_filing_nlp':lambda n:{'is_blocked':False},
            '_num':lambda x:float(x or 0),
            'DB_LOCK':threading.RLock(),'duckdb':types.SimpleNamespace(connect=lambda *a,**kw:FakeCon()),
            'DB_PATH':'unused','SWEEP_TTL_SEC':900}
        return ns,st,fno

    def test_scans_both_ce_pe_with_valid_quotes(self):
        ns,st,mod=self._build()
        with patch.dict(sys.modules, {'option_universe_health':mod}):
            df=_load_scanner(ns)(max_underlyings=2)
        self.assertEqual(len(df),4)
        self.assertEqual(set(df['Type']),{'CE','PE'})
        health=st.session_state['option_scan_health']
        self.assertEqual(health['underlyings_attempted'],2)
        self.assertEqual(health['fresh_chains'],2)
        self.assertGreater(health['contract_reviews'],4)
        self.assertGreater(health['valid_bid_ask'],4)
        self.assertEqual(st.session_state['option_scan_cursor'],(datetime(2026,11,23).date().toordinal()*17)%2)
        self.assertTrue(all(df['Fresh quote']))
        self.assertEqual(set(df[df['Decision']=='CONSIDER']['Type']), {'CE'})
        self.assertEqual(set(df[df['Decision']=='WATCH']['Type']), {'PE'})

    def test_no_quotes_means_no_reported_options(self):
        ns,st,mod=self._build(ask=0)
        with patch.dict(sys.modules, {'option_universe_health':mod}):
            df=_load_scanner(ns)(max_underlyings=2)
        self.assertTrue(df.empty)
        health=st.session_state['option_scan_health']
        self.assertEqual(health['valid_bid_ask'],0)
        self.assertGreater(health['missing_bid_ask'],0)
        self.assertEqual(health['consider'],0)

    def test_outside_entry_window_issues_no_requests_and_keeps_prior_stats(self):
        ns,st,mod=self._build()
        ns['is_entry_window_open']=lambda:False
        prior={'valid_bid_ask':7}
        st.session_state['option_scan_health']=prior
        with patch.dict(sys.modules, {'option_universe_health':mod}):
            df=_load_scanner(ns)()
        self.assertTrue(df.empty)
        self.assertEqual(st.session_state['option_scan_health'],prior)
        self.assertIn('outside the verified entry window',st.session_state['option_scan_note'])

if __name__=='__main__': unittest.main()
