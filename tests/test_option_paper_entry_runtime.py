"""Paper option entry function under controlled fake database and exchange data.

Requires no credentials, NSE calls, or Streamlit import. This is NOT a live test.
"""
import ast
from datetime import datetime
from pathlib import Path
import sys
import threading
import types
import unittest
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from quant_guardrails import review_long_option, can_allocate_option

APP=Path(__file__).resolve().parents[1]/'app.py'


def load_entry(ns):
    tree=ast.parse(APP.read_text(encoding='utf-8'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='generate_daily_options_alpha')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(APP),'exec'),ns)
    return ns['generate_daily_options_alpha']


class FakeDb:
    saved=[]
    def execute(self,sql,args=None):
        if 'INSERT' in sql:
            self.saved.append(args)
        return self
    def df(self):
        return pd.DataFrame()
    def close(self):pass


class TestEntry(unittest.TestCase):
    def setUp(self):
        self.persisted=[]
        FakeDb.saved=[]
        now=datetime(2026,11,23,13,15)
        self.row={
            'Stock':'RELIANCE','Type':'CE','Decision':'CONSIDER','Fresh quote':True,
            'Checked at':'2026-11-23 13:15:00','Expiry':'29 Dec 2026','Days left':36,
            'Strike (₹)':1010.0,'Share price (₹)':1000.0,
            'Buy premium (₹)':12.0,'Sell premium (₹)':11.9,
            'Lot':250,'Cost (₹)':3000.0,'Score':99.1,'IV (%)':23.0,
            'Historical volatility (%)':24.0,'5-day share move (%)':2.5,'RSI':60.0,
        }
        self.st=types.SimpleNamespace(session_state={'option_candidates':pd.DataFrame([self.row])})
        self.quote={'bid':12.0,'ask':12.2,'oi':12000,'iv':24.0}
        self.ns={
            'st':self.st,'pd':pd,'datetime':datetime,'ist_now':lambda:now,
            'is_entry_window_open':lambda:True,
            'get_history_integrity':lambda:{'allow_new_paper_entries':True},
            'get_open_exposures':lambda:(0,0),'can_allocate_option':can_allocate_option,
            'get_nse_option_quote':lambda *args:self.quote,
            'review_long_option':review_long_option,
            'evaluate_nse_filing_nlp':lambda symbol:{'is_blocked':False},
            'DB_LOCK':threading.RLock(),'DB_PATH':'unused',
            'duckdb':types.SimpleNamespace(connect=lambda *a,**kw:FakeDb()),
            'persist_daily_option_to_supabase':self.persisted.append,
            'OPTION_TARGET_MULT':1.65,'OPTION_STOP_MULT':0.5,
        }

    def test_requotes_then_saves_new_premium_and_stop(self):
        signal=load_entry(self.ns)()
        self.assertEqual(signal['entry_premium'],12.2)
        self.assertEqual(signal['current_option_price'],12)
        self.assertEqual(signal['total_capital'],3050)
        self.assertEqual(signal['stop_loss_premium'],6.1)
        self.assertEqual(signal['target_premium'],20.13)
        self.assertEqual(len(FakeDb.saved),1)
        self.assertEqual(len(self.persisted),1)

    def test_stale_shortlist_cannot_open_new_paper(self):
        self.st.session_state['option_candidates'].at[0,'Checked at']='2026-11-23 13:00:00'
        self.assertEqual(load_entry(self.ns)(),{})
        self.assertFalse(FakeDb.saved)

    def test_missing_live_quote_cannot_open_new_paper(self):
        self.quote=None
        self.assertEqual(load_entry(self.ns)(),{})
        self.assertFalse(FakeDb.saved)

    def test_premium_jump_cannot_open_new_paper(self):
        self.quote={'bid':14.9,'ask':15.0,'oi':12000,'iv':24}
        self.assertEqual(load_entry(self.ns)(),{})
        self.assertFalse(FakeDb.saved)

    def test_wide_spread_cannot_open_new_paper(self):
        self.quote={'bid':3.5,'ask':12.1,'oi':12000,'iv':24}
        self.assertEqual(load_entry(self.ns)(),{})
        self.assertFalse(FakeDb.saved)


if __name__=='__main__':unittest.main()
