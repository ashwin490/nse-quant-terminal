import unittest
from unittest.mock import patch
from nse_ai_private_reader import private_research_snapshot, PrivateResearchReadError


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
    def json(self):
        return self.data


class PrivateReaderTests(unittest.TestCase):
    def test_new_secret_uses_apikey_not_bearer_and_get_only(self):
        paths = [Response([{'value': {'model_id': 'm1'}}]),
                 Response([{'run_id': 'r1', 'status': 'SUCCESS'}]),
                 Response([{'forecast_for':'2026-11-26', 'ticker': 'BPCL'}])]
        with patch('nse_ai_private_reader.requests.get', side_effect=paths) as get:
            d = private_research_snapshot('https://nse-project.supabase.co/', 'sb_secret_dummy')
        self.assertEqual(d['champion']['model_id'], 'm1')
        self.assertEqual(d['run']['status'], 'SUCCESS')
        self.assertEqual(len(d['forecasts']), 1)
        self.assertEqual(get.call_count, 3)
        for call in get.call_args_list:
            self.assertEqual(call.kwargs['headers']['apikey'], 'sb_secret_dummy')
            self.assertNotIn('Authorization',call.kwargs['headers'])
            self.assertEqual(call.kwargs['timeout'], 12)
        self.assertIn('/rest/v1/nse_ai_settings',get.call_args_list[0].args[0])
    def test_legacy_key_bearer_kept(self):
        with patch('nse_ai_private_reader.requests.get', return_value=Response([])) as get:
            private_research_snapshot('https://nse-project.supabase.co', 'legacy_jwt')
        self.assertEqual(get.call_args_list[0].kwargs['headers']['Authorization'], 'Bearer legacy_jwt')
    def test_permission_denial_fails_closed_without_exposing_details(self):
        with patch('nse_ai_private_reader.requests.get', return_value=Response({'message':'secret info'},403)):
            with self.assertRaises(PrivateResearchReadError) as e:
                private_research_snapshot('https://nse-project.supabase.co', 'sb_secret_dummy')
        self.assertEqual(str(e.exception), 'permission_denied')
    def test_missing_key_fails_without_queries(self):
        with patch('nse_ai_private_reader.requests.get') as get:
            with self.assertRaises(PrivateResearchReadError):
                private_research_snapshot('https://nse-project.supabase.co','')
        get.assert_not_called()
    def test_worker_no_models_can_still_show_configured(self):
        with patch('nse_ai_private_reader.requests.get', return_value=Response([])):
            d=private_research_snapshot('https://nse-project.supabase.co', 'sb_secret_dummy')
        self.assertEqual(d, {'champion':{},'run':None,'forecasts':[]})

if __name__ == '__main__': unittest.main()
