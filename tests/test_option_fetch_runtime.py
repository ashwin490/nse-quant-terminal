"""Real NSE-adapter functions executed with mocked exchange JSON.

This proves endpoint selection/fallback logic only. No real NSE connection.
"""
import ast
from datetime import datetime
from pathlib import Path
import sys
import types
import urllib.parse
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nse_option_chain import (expiry_labels,supported_expiries,matching_rows,quote_is_fresh,
    exact_contract,bid_ask)

APP=Path(__file__).resolve().parents[1]/'app.py'


def _funcs(ns, names):
    tree=ast.parse(APP.read_text(encoding='utf-8'))
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),str(APP),'exec'),ns)
    return [ns[k] for k in names]


def chain():
    return {'records':{'timestamp':'23-Nov-2026 13:10:00',
        'expiryDates':['24-Nov-2026','29-Dec-2026'],
        'data':[{'strikePrice':1000,'expiryDate':'29-Dec-2026',
                 'CE':{'bidprice':14,'askPrice':14.2,'bidQty':10,'askQty':10,'openInterest':1000,'impliedVolatility':21},
                 'PE':{'bidprice':11,'askPrice':11.2,'bidQty':10,'askQty':10,'openInterest':1000,'impliedVolatility':20}}]}}


class FakeEndpointTests(unittest.TestCase):
    def _build(self, returns):
        self.paths=[]
        state={}
        def fake_json(url):
            self.paths.append(url)
            # emulate HTTP response reporting without reading public credentials
            state['_nse_option_http_status']='200'
            if 'contract-info' in url:return returns.get('info')
            if 'option-chain-v3' in url:return returns.get('v3')
            if 'option-chain-equities' in url:return returns.get('legacy')
        st=types.SimpleNamespace(session_state=state)
        ns={'st':st,'datetime':datetime,'urllib':urllib,'ist_now':lambda:datetime(2026,11,23,13,15),
            'clean_sym_name':lambda x:x.upper(), '_nse_get_json':fake_json,
            'expiry_labels':expiry_labels,'supported_expiries':supported_expiries,
            'matching_rows':matching_rows,'quote_is_fresh':quote_is_fresh,
            'exact_contract':exact_contract, 'bid_ask':bid_ask,'_num':lambda x:float(x or 0)}
        names=['_track_option_request','_exchange_equity_option_chain','_quote_age_is_valid','get_nse_option_quote']
        _funcs(ns,names)
        return ns,st

    def test_v3_is_preferred_after_contract_info(self):
        ns,st=self._build({'info':{'expiryDates':['24-Nov-2026','29-Dec-2026']},'v3':chain()})
        result=ns['_exchange_equity_option_chain']('reliance',lots_for_symbol={'DEC-26':250},health={
            'http_status_counts':{},'contract_info_received':0,'expiry_matches':0,'v3_rows_received':0})
        self.assertIsNotNone(result)
        self.assertEqual(result[1:],('29-Dec-2026',36,250))
        self.assertEqual(len(self.paths),2)
        self.assertTrue(any('option-chain-v3' in x and 'expiry=29-Dec-2026' in x for x in self.paths))

    def test_empty_v3_falls_back_to_legacy(self):
        ns,st=self._build({'info':{'expiryDates':['29-Dec-2026']},'v3':{'records':{'data':[]}},'legacy':chain()})
        stats={'http_status_counts':{},'contract_info_received':0,'expiry_matches':0,
               'v3_rows_received':0,'legacy_rows_received':0}
        result=ns['_exchange_equity_option_chain']('RELIANCE',lots_for_symbol={'DEC-26':250},health=stats)
        self.assertIsNotNone(result)
        self.assertEqual(stats['legacy_rows_received'],1)
        self.assertEqual(len(self.paths),3)

    def test_bad_expiry_lot_pauses_without_guessing(self):
        ns,st=self._build({'info':{'expiryDates':['29-Dec-2026']},'legacy':chain()})
        stats={'http_status_counts':{},'contract_info_received':0,'expiry_matches':0,
               'no_supported_expiry_lot':0}
        self.assertIsNone(ns['_exchange_equity_option_chain']('RELIANCE',lots_for_symbol={'NOV-26':250},health=stats))
        self.assertEqual(stats['no_supported_expiry_lot'],1)

    def test_exact_contract_requote(self):
        ns,st=self._build({'v3':chain()})
        q=ns['get_nse_option_quote']('RELIANCE',datetime(2026,12,29),1000,'CE')
        self.assertEqual(q['bid'],14)
        self.assertEqual(q['ask'],14.2)
        self.assertEqual(len(self.paths),1)
        self.assertIsNone(ns['get_nse_option_quote']('RELIANCE',datetime(2026,12,29),1110,'CE'))

    def test_untimed_chain_is_never_live_quote(self):
        c=chain(); del c['records']['timestamp']
        ns,st=self._build({'v3':c,'legacy':c})
        self.assertIsNone(ns['get_nse_option_quote']('RELIANCE',datetime(2026,12,29),1000,'PE'))

if __name__=='__main__':unittest.main()
