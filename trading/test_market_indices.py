"""
주요 지수 요약 API (TASK-0018 2단계, ADR-0005). 외부 시세 호출은 모두 가짜 reader로 대체한다(네트워크 없음).
"""
from datetime import date
from unittest.mock import patch

import pandas as pd
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from .services import market_indices as mi


def frame(closes, last_day='2026-09-23'):
    idx = pd.date_range(end=last_day, periods=len(closes), freq='D')
    return pd.DataFrame({'Close': closes}, index=idx)


class SummarizeClosesTestCase(SimpleTestCase):
    def test_change_and_rate(self):
        s = mi.summarize_closes([100.0, 110.0])
        self.assertEqual(s['value'], 110.0)
        self.assertEqual(s['change'], 10.0)
        self.assertEqual(s['change_rate'], 10.0)
        self.assertEqual(s['series'], [100.0, 110.0])

    def test_nan_dropped_and_too_short_is_none(self):
        self.assertIsNone(mi.summarize_closes([float('nan'), 100.0]))
        self.assertIsNone(mi.summarize_closes([]))

    def test_series_limited_to_last_points(self):
        s = mi.summarize_closes(list(range(1, 51)))
        self.assertEqual(len(s['series']), mi.SERIES_POINTS)
        self.assertEqual(s['series'][-1], 50.0)


class FetchIndexTestCase(SimpleTestCase):
    SPEC = {'code': 'KOSPI', 'name': '코스피', 'sources': ['YAHOO:^KS11', 'NAVER:KOSPI']}

    def test_first_source_success(self):
        calls = []

        def reader(source, start):
            calls.append(source)
            return frame([2600.0, 2634.7])

        r = mi.fetch_index(self.SPEC, reader, today=date(2026, 9, 24))
        self.assertEqual(calls, ['YAHOO:^KS11'])
        self.assertEqual(r['code'], 'KOSPI')
        self.assertEqual(r['value'], 2634.7)
        self.assertEqual(r['as_of'], '2026-09-23')
        self.assertEqual(r['source'], 'YAHOO:^KS11')

    def test_falls_back_when_first_source_fails(self):
        def reader(source, start):
            if source.startswith('YAHOO'):
                raise ValueError('LOGOUT')
            return frame([100.0, 99.0])

        r = mi.fetch_index(self.SPEC, reader)
        self.assertEqual(r['source'], 'NAVER:KOSPI')
        self.assertEqual(r['change'], -1.0)

    def test_all_sources_fail_returns_none(self):
        def reader(source, start):
            raise ValueError('down')

        self.assertIsNone(mi.fetch_index(self.SPEC, reader))

    def test_empty_frame_tries_next_source(self):
        def reader(source, start):
            return pd.DataFrame() if source.startswith('YAHOO') else frame([1.0, 2.0])

        self.assertEqual(mi.fetch_index(self.SPEC, reader)['source'], 'NAVER:KOSPI')


class GetMarketIndicesTestCase(SimpleTestCase):
    def setUp(self):
        cache.delete(mi.CACHE_KEY)

    def tearDown(self):
        cache.delete(mi.CACHE_KEY)

    def test_failed_index_is_omitted_and_result_cached(self):
        calls = []

        def reader(source, start):
            calls.append(source)
            if 'KQ11' in source or source == 'NAVER:KOSDAQ':
                raise ValueError('down')
            return frame([10.0, 11.0])

        first = mi.get_market_indices(reader)
        self.assertEqual([i['code'] for i in first], ['KOSPI', 'NASDAQ', 'SP500'])
        n = len(calls)
        second = mi.get_market_indices(reader)
        self.assertEqual(second, first)
        self.assertEqual(len(calls), n)  # 캐시에서 반환, 외부 호출 없음


class MarketIndicesApiTestCase(TestCase):
    def setUp(self):
        cache.delete(mi.CACHE_KEY)
        self.client = APIClient()

    def tearDown(self):
        cache.delete(mi.CACHE_KEY)

    def test_requires_authentication(self):
        self.assertEqual(self.client.get('/api/market/indices/').status_code, 401)

    def test_returns_indices(self):
        user = User.objects.create_user(username='idx', password='pass123')
        self.client.force_authenticate(user)
        with patch.object(mi, '_default_reader', lambda source, start: frame([100.0, 101.0])):
            res = self.client.get('/api/market/indices/')
        self.assertEqual(res.status_code, 200)
        codes = [i['code'] for i in res.json()['indices']]
        self.assertEqual(codes, ['KOSPI', 'KOSDAQ', 'NASDAQ', 'SP500'])
        self.assertEqual(res.json()['indices'][0]['change_rate'], 1.0)
