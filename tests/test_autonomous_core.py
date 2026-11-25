import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from nse_ai_core import (clean_ohlc, first_touch, features_for_daily, labelled_samples,
                         latest_features, samples_frame, purged_holdout, fit_challenger,
                         evaluate, constant_baseline, qualifies, forecast_from_model)


def candles(n=140):
    dates=pd.bdate_range('2026-01-02',periods=n)
    x=np.arange(n,dtype=float)
    close=100+x*.08+np.sin(x/6)*2
    return pd.DataFrame({'open':close*.999,'high':close*1.015,'low':close*.985,
                         'close':close,'volume':100000+((x*317).astype(int)%20000)}, index=dates)


class TestAutonomousCore(unittest.TestCase):
    def test_stop_has_priority_on_same_daily_bar(self):
        c = pd.DataFrame([{'open':101,'high':110,'low':95,'close':101,'volume':20}],index=pd.to_datetime(['2026-06-01']))
        self.assertEqual(first_touch(c,100)[0], 'STOP')

    def test_target_first(self):
        c = pd.DataFrame([{'open':102,'high':106,'low':101,'close':105,'volume':20}],index=pd.to_datetime(['2026-06-01']))
        self.assertEqual(first_touch(c,100)[0], 'TARGET')

    def test_horizon_not_future_leaked(self):
        d=candles(110)
        a=labelled_samples('AAA',d)
        self.assertTrue(a)
        self.assertLessEqual(pd.Timestamp(max(x['session_date'] for x in a)),d.index[-6])
        self.assertTrue(all(x['horizon']==5 for x in a))
        self.assertEqual(a[-1]['session_date'],str(d.index[-6].date()))

    def test_last_features_are_current_session(self):
        d=candles()
        r=latest_features('AAA',d)
        self.assertEqual(r['session_date'],str(d.index[-1].date()))
        self.assertEqual(set(r['features']),set(features_for_daily(d).columns))

    def test_bad_candles_fail_closed(self):
        d=candles()
        d.loc[d.index[-1],'low']=d.loc[d.index[-1],'high']+1
        with self.assertRaises(ValueError): clean_ohlc(d)

    def test_all_samples_use_historical_features_not_future(self):
        d=candles()
        a=labelled_samples('AAA',d)
        original=a[-1]['features']
        d.loc[d.index[-1],'close'] *=1.05
        d.loc[d.index[-1],'high'] *=1.1
        # Last labelled entry is six sessions prior, so future bars cannot change its features.
        self.assertEqual(labelled_samples('AAA',d)[-1]['features'],original)

    def test_chronological_split_purges_session_overlap(self):
        d=candles(190)
        rows=[]
        for j in range(4):
            rows.extend(labelled_samples(f'AA{j}', d))
        frame=samples_frame(rows)
        frame['target_first'] = (np.arange(len(frame)) % 3 == 0).astype(int)
        train,test=purged_holdout(frame)
        self.assertLess(train.session_date.max(),test.session_date.min())
        unique=sorted(frame.session_date.unique())
        self.assertGreaterEqual(unique.index(test.session_date.min()) - unique.index(train.session_date.max()),7)
        self.assertFalse(set(train.session_date).intersection(set(test.session_date)))

    def test_model_validation_never_promotes_worse(self):
        m={'n':120,'brier':0.249,'log_loss':0.69}
        b={'brier':0.22,'log_loss':0.64}
        self.assertFalse(qualifies(m,b))
        self.assertFalse(qualifies({'n':120,'brier':0.215,'log_loss':0.63},b,{'brier':0.2,'log_loss':0.6}))
        self.assertTrue(qualifies({'n':120,'brier':0.18,'log_loss':0.5},b))

    def test_forecast_needs_confirmed_model(self):
        d=candles()
        rows=labelled_samples('AAA',d)
        # Simple fake classifier to exercise the UI payload without network.
        class FixedModel:
            def predict_proba(self,x):
                return np.array([[.3,.7]])
        f=forecast_from_model(FixedModel(),latest_features('AAA',d),rows)
        self.assertLess(f['stop_loss'],f['entry_price'])
        self.assertGreater(f['target_price'],f['entry_price'])
        self.assertEqual(f['horizon_sessions'],5)
        self.assertEqual(f['forecast_for'],str(d.index[-1].date()))

    def test_train_and_metric_shapes(self):
        from sklearn.datasets import make_classification
        x,y=make_classification(n_samples=350,n_features=8,n_informative=5,random_state=123)
        train=pd.DataFrame(x[:250],columns=list(__import__('nse_ai_core').FEATURES))
        train['target_first']=y[:250]
        test=pd.DataFrame(x[250:],columns=list(__import__('nse_ai_core').FEATURES))
        test['target_first']=y[250:]
        test['session_date']='2026-10-01'
        m=fit_challenger(train)
        self.assertEqual(evaluate(m,test)['n'],100)
        self.assertIn('brier',constant_baseline(train,test))


if __name__=='__main__':unittest.main()
