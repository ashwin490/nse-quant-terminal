import sys, unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nse_ai_core import FEATURES, FEATURE_VERSION
from nse_autonomous_worker import attempt_retrain


class Store:
    def __init__(self):
        self.values={}
        self.models=[]
    def setting(self,key,default=None):
        return self.values.get(key,default)
    def put_setting(self,key,value):
        self.values[key]=value
    def upload_model(self,mid,raw):
        self.models.append((mid,raw))
        return 'champion/'+mid+'.joblib','abc123'
    def insert_immutable(self,table,rows,conflict):
        self.models.append((table,rows))


def synthetic_samples():
    rng=np.random.default_rng(4)
    rows=[]
    for i, dt in enumerate(__import__('pandas').bdate_range('2025-01-02',periods=180)):
        for j in range(5):
            v=float(rng.normal())
            label=bool(v+float(rng.normal(scale=.12)) > 0)
            f={k:float(rng.normal()) for k in FEATURES}
            f['ret5']=v
            rows.append({'ticker':f'S{j}', 'session_date':str(dt.date()),
                         'feature_version':FEATURE_VERSION, 'features':f,
                         'target_first':label,'outcome':'TARGET' if label else 'STOP',
                         'days_to_exit':2})
    return rows

class TestScheduledLearning(unittest.TestCase):
    def test_real_challenger_can_promote_to_private_storage(self):
        store=Store()
        now=datetime(2026,11,25,18,0,tzinfo=ZoneInfo('Asia/Kolkata'))
        result=attempt_retrain(store,synthetic_samples(),now)
        self.assertEqual(result['status'],'PROMOTED',result)
        self.assertEqual(len([p for p in store.models if p[0]=='nse_ai_models']),1)
        self.assertIn('champion',store.values)
        self.assertTrue(store.values['champion']['model_id'].startswith('nse-'))
        self.assertEqual(attempt_retrain(store,synthetic_samples(),now)['status'],'UNCHANGED')

    def test_no_training_on_sparse_data(self):
        self.assertEqual(attempt_retrain(Store(),[],datetime(2026,11,25,18,0,tzinfo=ZoneInfo('Asia/Kolkata')))['status'], 'COLLECTING')

if __name__=='__main__':unittest.main()
