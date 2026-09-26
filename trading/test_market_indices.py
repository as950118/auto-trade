"""
주요 지수 요약 (TASK-0018 2단계, ADR-0005). 외부 호출은 모두 가짜로 대체한다(네트워크 없음).
"""
from datetime import date
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from .services import market_indices as mi

SPEC = {'code': 'KOSPI', 'name': '코스피', 'symbol': '^KS11'}


def pairs(*closes, start_day=1):
    return [(date(2026, 9, start_day + i), c) for i, c in enumerate(closes)]


class SummarizeTestCase(SimpleTestCase):
    def test_change_rate_and_as_of_from_value_row(self):
        s = mi.summarize(SPEC, pairs(100.0, 110.0))
        self.assertEqual((s['value'], s['change'], s['change_rate']), (110.0, 10.0, 10.0))
        self.assertEqual(s['as_of'], '2026-09-02')
        self.assertFalse(s['stale'])

    def test_nan_rows_dropped_before_picking_as_of(self):
        s = mi.summarize(SPEC, pairs(100.0, 101.0, float('nan')))
        self.assertEqual(s['value'], 101.0)
        self.assertEqual(s['as_of'], '2026-09-02')  # 값(101)이 나온 행의 날짜

    def test_too_short_is_none(self):
        self.assertIsNone(mi.summarize(SPEC, pairs(100.0)))
        self.assertIsNone(mi.summarize(SPEC, []))

    def test_series_limited(self):
        s = mi.summarize(SPEC, [(date(2026, 1, 1), float(i)) for i in range(50)])
        self.assertEqual(len(s['series']), mi.SERIES_POINTS)


class FetchYahooTestCase(SimpleTestCase):
    def _response(self):
        resp = MagicMock()
        resp.json.return_value = {'chart': {'result': [{
            'meta': {'gmtoffset': 32400},
            'timestamp': [1789948800, 1790035200, 1790121600],
            'indicators': {'quote': [{'close': [7007.72, None, 7080.92]}]},
        }]}}
        return resp

    def test_passes_timeout_and_parses_local_trade_dates(self):
        with patch.object(mi.requests, 'get', return_value=self._response()) as get:
            result = mi.fetch_yahoo_daily_closes('^KS11')
        self.assertEqual(get.call_args.kwargs['timeout'], mi.REQUEST_TIMEOUT)
        self.assertEqual(get.call_args.args[0], 'https://query1.finance.yahoo.com/v8/finance/chart/^KS11')
        # 종가 없는 행은 빠지고, 날짜는 거래소 시차(gmtoffset)를 반영한 현지 거래일이다
        self.assertEqual(result, [(date(2026, 9, 21), 7007.72), (date(2026, 9, 23), 7080.92)])

    def test_http_error_raises(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = mi.requests.HTTPError('503')
        with patch.object(mi.requests, 'get', return_value=resp):
            with self.assertRaises(mi.requests.HTTPError):
                mi.fetch_yahoo_daily_closes('^KS11')


class RefreshTestCase(SimpleTestCase):
    def setUp(self):
        cache.delete(mi.CACHE_KEY)

    def tearDown(self):
        cache.delete(mi.CACHE_KEY)

    def test_refresh_fills_cache(self):
        result = mi.refresh_market_indices(lambda symbol: pairs(10.0, 11.0))
        self.assertEqual([i['code'] for i in result], ['KOSPI', 'KOSDAQ', 'NASDAQ', 'SP500'])
        self.assertEqual(mi.get_market_indices(), result)

    def test_failed_index_keeps_previous_value_as_stale(self):
        mi.refresh_market_indices(lambda symbol: pairs(10.0, 11.0))

        def flaky(symbol):
            if symbol == '^KQ11':
                raise mi.requests.Timeout('read timeout')
            return pairs(20.0, 22.0)

        result = {i['code']: i for i in mi.refresh_market_indices(flaky)}
        self.assertEqual(result['KOSPI']['value'], 22.0)
        self.assertFalse(result['KOSPI']['stale'])
        self.assertEqual(result['KOSDAQ']['value'], 11.0)
        self.assertTrue(result['KOSDAQ']['stale'])

    def test_never_fetched_index_is_omitted(self):
        def flaky(symbol):
            if symbol == '^GSPC':
                raise ValueError('down')
            return pairs(1.0, 2.0)

        codes = [i['code'] for i in mi.refresh_market_indices(flaky)]
        self.assertEqual(codes, ['KOSPI', 'KOSDAQ', 'NASDAQ'])

    def test_get_is_empty_before_first_refresh(self):
        self.assertEqual(mi.get_market_indices(), [])


class MarketIndicesApiTestCase(TestCase):
    def setUp(self):
        cache.delete(mi.CACHE_KEY)
        self.client = APIClient()

    def tearDown(self):
        cache.delete(mi.CACHE_KEY)

    def test_requires_authentication(self):
        self.assertEqual(self.client.get('/api/market/indices/').status_code, 401)

    def test_reads_cache_without_external_calls(self):
        mi.refresh_market_indices(lambda symbol: pairs(100.0, 101.0))
        self.client.force_authenticate(User.objects.create_user(username='idx', password='pass123'))
        with patch.object(mi, 'fetch_yahoo_daily_closes', side_effect=AssertionError('요청 경로에서 외부 호출')):
            res = self.client.get('/api/market/indices/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual([i['code'] for i in res.json()['indices']], ['KOSPI', 'KOSDAQ', 'NASDAQ', 'SP500'])
        self.assertEqual(res.json()['indices'][0]['change_rate'], 1.0)


class SchedulerJobTestCase(SimpleTestCase):
    def test_job_calls_refresh_and_swallows_errors(self):
        from .scheduler import refresh_market_indices_job

        with patch.object(mi, 'refresh_market_indices', side_effect=RuntimeError('boom')) as refresh:
            refresh_market_indices_job()  # 예외가 스케줄러로 새지 않는다
        refresh.assert_called_once()

    def test_refresh_job_registered_to_run_at_startup_even_if_late(self):
        """재시작 직후 첫 갱신이 misfire로 버려지지 않아야 한다(기본 유예 1초)."""
        from unittest.mock import MagicMock as MM

        from . import scheduler as sched

        fake = MM()
        with patch.object(sched, 'BackgroundScheduler', return_value=fake), \
                patch.object(sched, 'DjangoJobStore'), patch.object(sched, 'register_events'):
            try:
                sched.start_scheduler()
            except Exception:
                pass  # 이 테스트는 add_job 인자만 본다
        kwargs = next(c.kwargs for c in fake.add_job.call_args_list if c.kwargs.get('id') == 'refresh_market_indices')
        self.assertIsNone(kwargs['misfire_grace_time'])
        self.assertTrue(kwargs['coalesce'])
        self.assertIsNotNone(kwargs['next_run_time'])
