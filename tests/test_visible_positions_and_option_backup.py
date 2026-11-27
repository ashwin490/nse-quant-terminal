"""Regression tests for the main-page position feed and safe cloud option repair.

Extract the two pure helper functions from app.py so no Streamlit app/server starts.
"""
import ast
import html
import math
import re
import unittest
from datetime import datetime
from pathlib import Path
import pandas as pd

APP = Path(__file__).resolve().parents[1] / 'app.py'
TREE = ast.parse(APP.read_text(encoding='utf-8'))
NEEDED = {'lse_open_cards', 'verify_option_cloud_backup'}
CODE = compile(ast.Module(body=[n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in NEEDED], type_ignores=[]), str(APP), 'exec')


def short_money(number, d=2):
    try:
        return f'₹{float(number):,.{d}f}' if math.isfinite(float(number)) else '—'
    except (TypeError, ValueError):
        return '—'


def finite(number):
    try:
        x = float(number)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


class Query:
    def __init__(self, repo):
        self.repo = repo
        self._start, self._end = 0, 499
        self._payload = None
    def select(self, *_):
        return self
    def range(self, start, end):
        self._start, self._end = start, end
        return self
    def insert(self, payload):
        self._payload = payload
        return self
    def execute(self):
        if self._payload is not None:
            if self._payload['date_key'] in self.repo.rows:
                raise ValueError('duplicate')
            self.repo.rows[self._payload['date_key']] = self._payload
            return type('Response', (), {'data': [self._payload]})()
        keys = sorted(self.repo.rows)
        return type('Response', (), {'data': [{'date_key': k} for k in keys[self._start:self._end + 1]]})()


class Repo:
    def __init__(self, rows=None):
        self.rows = {x['date_key']: x for x in (rows or [])}
    def table(self, name):
        if name != 'options_journal':
            raise AssertionError(name)
        return Query(self)


class PositionAndBackupTests(unittest.TestCase):
    def setUp(self):
        self.ns = {'pd': pd, 'html': html, 're': re, 'datetime': datetime,
                   'short_money': short_money, '_lse_finite': finite,
                   '_num': lambda v: finite(v) or 0, 'HISTORY_EXPECTED_MIN_OPTIONS': 0, 'supabase': None, 'math': math}
        exec(CODE, self.ns)

    def test_all_shares_and_unpriced_ce_are_visible(self):
        shares = pd.DataFrame([dict(ticker=f'TEST{i}', date_str='2026-11-27', status='ACTIVE',
                         entry_price=100, target_price=106, stop_loss=96,
                         latest_price=102, pnl_pct=1.5) for i in range(14)])
        options = pd.DataFrame([dict(date_key='2026-11-27', status='ACTIVE',
                      option_contract='NIFTY 27000 CE', current_option_price=0,
                      entry_premium=100,target_premium=120,stop_loss_premium=80,pnl_pct=0)])
        text = self.ns['lse_open_cards'](shares, options)
        self.assertEqual(text.count('class="lx-position"'), 15)
        self.assertLess(text.index('NIFTY 27000 CE'), text.index('TEST0'))
        self.assertIn('PREMIUM UNVERIFIED',text)
        self.assertIn('P/L unverified',text)
        self.assertIn('TEST13',text)

    def test_inactive_and_html_injection_safely_hidden(self):
        shares = pd.DataFrame([
            {'ticker':'HIDDEN','status':'INACTIVE'},
            {'ticker':'PRESENT','status':'🟢 ACTIVE', 'entry_price':5, 'target_price':7, 'stop_loss':4, 'latest_price':6, 'pnl_pct':2},
            {'ticker':'<script>alert(1)</script>', 'status':'ACTIVE', 'entry_price':5, 'target_price':7, 'stop_loss':4, 'latest_price':6, 'pnl_pct':2},
        ])
        html_text = self.ns['lse_open_cards'](shares, pd.DataFrame())
        self.assertNotIn('HIDDEN',html_text)
        self.assertNotIn('<script>',html_text)
        self.assertIn('&lt;script&gt;',html_text)
        self.assertIn('PRESENT',html_text)

    def test_cloud_verify_without_writes_then_insert_missing_only(self):
        old = dict(date_key='2026-11-25', option_contract='OLD CE',entry_premium=100,status='CLOSED')
        local = pd.DataFrame([old, dict(date_key='2026-11-26', option_contract='NEW PE',entry_premium=95,status='ACTIVE',total_capital=9500)])
        repo = Repo([old.copy()])
        self.ns['supabase'] = repo
        read = self.ns['verify_option_cloud_backup'](local)
        self.assertFalse(read['ok'])
        self.assertEqual(read['missing'],['2026-11-26'])
        self.assertEqual(len(repo.rows),1)
        result = self.ns['verify_option_cloud_backup'](local, insert_missing=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['added'], 1)
        self.assertEqual(len(repo.rows), 2)
        self.assertEqual(repo.rows['2026-11-25'],old)
        self.assertEqual(self.ns['verify_option_cloud_backup'](local,insert_missing=True)['added'],0)

    def test_missing_cloud_no_false_success(self):
        local = pd.DataFrame([dict(date_key='2026-11-26',option_contract='NEW CE',entry_premium=50)])
        result = self.ns['verify_option_cloud_backup'](local)
        self.assertFalse(result['ok'])
        self.assertIn('not configured',result['error'])

    def test_historical_minimum_prevents_verification_after_data_loss(self):
        local = pd.DataFrame([dict(date_key='2026-11-26', option_contract='NEW CE', entry_premium=50)])
        repo = Repo()
        self.ns['supabase'] = repo
        self.ns['HISTORY_EXPECTED_MIN_OPTIONS'] = 21
        result = self.ns['verify_option_cloud_backup'](local, insert_missing=True)
        self.assertFalse(result['ok'])
        self.assertIn('below its protected minimum', result['error'])
        self.assertFalse(repo.rows)

    def test_bulk_missing_never_auto_inserted(self):
        local = pd.DataFrame([dict(date_key=f'2026-11-{i:02d}',option_contract=f'C{i} CE',entry_premium=50) for i in range(1, 8)])
        repo = Repo()
        self.ns['supabase'] = repo
        result = self.ns['verify_option_cloud_backup'](local,insert_missing=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['added'],0)
        self.assertEqual(len(repo.rows),0)

if __name__ == '__main__':
    unittest.main()
