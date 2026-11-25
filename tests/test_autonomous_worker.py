import sys
import unittest
from pathlib import Path
from datetime import date, datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nse_autonomous_worker import parse_symbols, research_batch, monitor_window, daily, monitor, backup_history
from nse_ai_dashboard import worker_status, valid_forecasts
from nse_ai_store import ResearchStore
import pandas as pd
import numpy as np

class FakeStore:
    def __init__(self):
        self.writes=[]; self.positions=[]
    def insert_immutable(self,table,rows,key,batch_size=100):
        self.writes.append((table, rows))
        return len(rows)
    def all_samples(self,maximum=60000):return []
    def select(self,table,**kw):
        return self.positions if table=='predictions' else []
    def upsert(self,table,rows,key):self.writes.append((table,rows))


class TestWorker(unittest.TestCase):
    def test_batch_rotation_deterministic(self):
        s=['AAA','BBB','CCC','DDD']
        self.assertEqual(research_batch(s,date(2026,11,25),2),research_batch(s,date(2026,11,25),2))
        self.assertEqual(len(set(research_batch(s,date(2026,11,25),2))),2)

    def test_symbol_validation(self):
        self.assertEqual(parse_symbols('AA.NS,AA, B-B, ,$'),['AA','B-B'])

    def test_market_window(self):
        tz=ZoneInfo('Asia/Kolkata')
        self.assertTrue(monitor_window(datetime(2026,11,25,14,0,tzinfo=tz)))
        self.assertFalse(monitor_window(datetime(2026,11,25,15,45,tzinfo=tz)))
        self.assertFalse(monitor_window(datetime(2026,11,28,14,0,tzinfo=tz)))

    def test_monitor_never_writes_original_history(self):
        store=FakeStore()
        now=datetime(2026,11,25,14,0,tzinfo=ZoneInfo('Asia/Kolkata'))
        store.positions=[{'ticker':'AAA','predicted_date':'2026-11-20',
                          'entry_price':100,'target_price':105,'stop_loss':97,'status':'ACTIVE'}]
        ix=pd.date_range('2026-11-25 13:55',periods=5,freq='min',tz='Asia/Kolkata')
        price=pd.DataFrame({'open':100,'high':[101,102,106,102,102],
                            'low':[99,99,98,99,99], 'close':101,'volume':120},index=ix)
        with patch('nse_autonomous_worker.send_alert',return_value=True):
            result=monitor(store,now,fetcher=lambda *a,**kw: price)
        self.assertEqual(result['paper_ledger_modified'],False)
        self.assertIn('AAA:TARGET_TOUCH',result['possible_touches'])
        self.assertTrue(store.writes)
        self.assertFalse(any(x[0]=='predictions' for x in store.writes))

    def test_monitor_missing_prices_no_fills(self):
        store=FakeStore()
        now=datetime(2026,11,25,14,0,tzinfo=ZoneInfo('Asia/Kolkata'))
        store.positions=[{'ticker':'AAA','predicted_date':'2026-11-20','status':'ACTIVE',
                          'entry_price':100,'target_price':105,'stop_loss':97}]
        def broken(*a,**kw):raise ValueError('feed down')
        result=monitor(store,now,fetcher=broken)
        self.assertEqual(result['coverage_incomplete'],1)
        self.assertEqual(len(store.writes),0)

    def test_daily_refuses_weak_feed_without_persistence(self):
        store=FakeStore()
        now=datetime(2026,11,25,18,0,tzinfo=ZoneInfo('Asia/Kolkata'))
        with patch('nse_autonomous_worker.parse_symbols',return_value=['A','B','C','D','E']):
            with self.assertRaises(RuntimeError):
                daily(store,now,fetcher=lambda *x,**kw:pd.DataFrame())
        self.assertFalse(store.writes)

    def test_ui_data_filters_unmatched_model(self):
        x={'model_id':'new','entry_price':100,'target_price':105,'stop_loss':97,
           'horizon_sessions':5,'target_first_probability':.6}
        self.assertEqual(len(valid_forecasts([x], 'old')),0)
        self.assertEqual(len(valid_forecasts([x], 'new')),1)
        self.assertEqual(len(valid_forecasts([{**x,'stop_loss':110}], 'new')),0)

    def test_worker_health(self):
        now=datetime(2026,11,25,12,0,tzinfo=ZoneInfo('UTC'))
        self.assertEqual(worker_status(None,now)['state'],'NOT_CONFIGURED')
        r={'status':'SUCCESS','finished_at':'2026-11-24T12:00:00+00:00'}
        self.assertEqual(worker_status(r,now)['state'],'HEALTHY')
        r['finished_at']='2026-11-20T12:00:00+00:00'
        self.assertEqual(worker_status(r,now)['state'],'STALE')

    def test_history_backup_requires_complete_both_journals(self):
        class BackupStore(FakeStore):
            def all_rows(self,table,maximum=10000):
                return [{"id":i} for i in range(106 if table=="predictions" else 20)]
            def upload_private(self,*args,**kwargs):
                raise AssertionError('Never back up incomplete journal as verified')
        now=datetime(2026,11,23,18,0,tzinfo=ZoneInfo('Asia/Kolkata'))
        self.assertEqual(backup_history(BackupStore(),now)['status'],'BLOCKED_INCOMPLETE_HISTORY')

    def test_history_backup_writes_versioned_private_snapshot(self):
        import gzip, json
        class BackupStore(FakeStore):
            def all_rows(self,table,maximum=10000):
                return [{"id":i} for i in range(106 if table=="predictions" else 21)]
            def upload_private(self,bucket,path,raw,content_type):
                self.bucket=bucket
                self.path=path
                self.payload=json.loads(gzip.decompress(raw))
                return "checksum"
        store=BackupStore()
        now=datetime(2026,11,23,18,0,tzinfo=ZoneInfo('Asia/Kolkata'))
        result=backup_history(store,now)
        self.assertEqual(result['status'],'SAVED')
        self.assertEqual(store.bucket,'nse-ai-backups')
        self.assertEqual(store.payload['counts']['options'],21)
        self.assertIn('2026-11-23',store.path)

    def test_store_fails_without_private_credentials(self):
        with patch.dict('os.environ', {'SUPABASE_SERVICE_ROLE_KEY':'','SUPABASE_URL':'https://example.supabase.co'}):
            with self.assertRaises(RuntimeError):ResearchStore()

if __name__=='__main__':unittest.main()
