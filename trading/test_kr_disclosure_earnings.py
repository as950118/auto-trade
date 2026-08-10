"""
국내(KR) 종목 공시(KrDisclosure)/실적(KrFinancialFact) 크롤링·API 테스트.
DART OpenAPI 호출은 모두 mock 처리한다 (PRD-0004, ARCH-0002, ADR-0003).
"""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from . import crawlers_kr_disclosure, crawlers_kr_earnings, dart_client
from .crawlers_kr_earnings import _extract_facts, _recent_report_periods
from .models import Broker, Country, Currency, KrDisclosure, KrFinancialFact, Symbol


def _make_kr_symbol(ticker='005930', name='삼성전자', dart_corp_code=None):
    broker, _ = Broker.objects.get_or_create(
        code='KRX', defaults={'name': 'KRX', 'country': Country.KOREA}
    )
    return Symbol.objects.create(
        ticker=ticker, name=name, currency=Currency.KRW, broker=broker,
        dart_corp_code=dart_corp_code,
    )


class RecentReportPeriodsTests(TestCase):
    def test_march_before_annual_deadline_excludes_prev_year_annual(self):
        periods = crawlers_kr_earnings._recent_report_periods(date(2026, 3, 1), count=5)
        self.assertNotIn(('2025', '11011'), periods)
        self.assertEqual(periods[0], ('2025', '11014'))

    def test_after_annual_deadline_includes_prev_year_annual(self):
        periods = crawlers_kr_earnings._recent_report_periods(date(2026, 4, 1), count=5)
        self.assertEqual(periods[0], ('2025', '11011'))

    def test_returns_requested_count_without_duplicates(self):
        periods = crawlers_kr_earnings._recent_report_periods(date(2026, 8, 10), count=5)
        self.assertEqual(len(periods), 5)
        self.assertEqual(len(set(periods)), 5)


class ExtractFactsTests(TestCase):
    def test_matches_quarterly_net_profit_variant(self):
        items = [
            {'sj_nm': '손익계산서', 'account_nm': '매출액', 'thstrm_amount': '1,000'},
            {'sj_nm': '손익계산서', 'account_nm': '영업이익', 'thstrm_amount': '200'},
            {'sj_nm': '손익계산서', 'account_nm': '분기순이익', 'thstrm_amount': '150'},
            {'sj_nm': '자본변동표', 'account_nm': '분기순이익', 'thstrm_amount': '999999'},
        ]
        facts = _extract_facts(items)
        self.assertEqual(facts['revenue'], Decimal('1000'))
        self.assertEqual(facts['operating_profit'], Decimal('200'))
        self.assertEqual(facts['net_profit'], Decimal('150'))

    def test_prefers_cumulative_add_amount_over_single_quarter_amount(self):
        # 실측(2026-08-10, 삼성전자 2025 반기보고서): thstrm_amount는 2분기 단독(74.57조),
        # thstrm_add_amount가 상반기 누적(153.7조) — "반기 실적"은 누적을 의미해야 한다.
        items = [
            {
                'sj_nm': '손익계산서', 'account_nm': '매출액',
                'thstrm_amount': '74566317000000', 'thstrm_add_amount': '153706820000000',
            },
        ]
        facts = _extract_facts(items)
        self.assertEqual(facts['revenue'], Decimal('153706820000000'))

    def test_falls_back_to_thstrm_amount_when_add_amount_absent(self):
        # 사업보고서(연간)/1분기보고서는 thstrm_add_amount가 비어있거나 thstrm_amount와 동일
        items = [{'sj_nm': '손익계산서', 'account_nm': '매출액', 'thstrm_amount': '1000'}]
        facts = _extract_facts(items)
        self.assertEqual(facts['revenue'], Decimal('1000'))

    def test_unmatched_account_names_return_none(self):
        items = [{'sj_nm': '손익계산서', 'account_nm': '알수없는계정', 'thstrm_amount': '1'}]
        facts = _extract_facts(items)
        self.assertIsNone(facts['revenue'])
        self.assertIsNone(facts['operating_profit'])
        self.assertIsNone(facts['net_profit'])


class CrawlKrDisclosuresTests(TestCase):
    def setUp(self):
        self.symbol = _make_kr_symbol()

    @mock.patch.object(dart_client, 'get_disclosures')
    def test_matches_registered_symbol_by_stock_code(self, mock_get):
        mock_get.return_value = {
            'status': '000',
            'total_page': 1,
            'list': [
                {
                    'corp_code': '00126380',
                    'corp_name': '삼성전자',
                    'stock_code': '005930',
                    'report_nm': '분기보고서',
                    'rcept_no': '20260810000001',
                    'rcept_dt': '20260810',
                },
                {
                    'corp_code': '00999999',
                    'corp_name': '미등록회사',
                    'stock_code': '999999',
                    'report_nm': '주요사항보고서',
                    'rcept_no': '20260810000002',
                    'rcept_dt': '20260810',
                },
            ],
        }
        result = crawlers_kr_disclosure.crawl_kr_disclosures(target_date=date(2026, 8, 10))
        self.assertEqual(result['new_disclosures'], 1)
        self.assertEqual(result['earnings_trigger_symbols'], [self.symbol.id])
        disclosure = KrDisclosure.objects.get(rcept_no='20260810000001')
        self.assertEqual(disclosure.symbol_id, self.symbol.id)
        self.assertEqual(disclosure.rcept_dt, date(2026, 8, 10))
        self.assertNotEqual(KrDisclosure.objects.filter(rcept_no='20260810000002').count(), 1)

    @mock.patch.object(dart_client, 'get_disclosures')
    def test_no_data_status_returns_zero_without_error(self, mock_get):
        mock_get.return_value = {'status': '013', 'list': []}
        result = crawlers_kr_disclosure.crawl_kr_disclosures(target_date=date(2026, 8, 10))
        self.assertEqual(result['new_disclosures'], 0)

    @mock.patch.object(dart_client, 'get_disclosures')
    def test_rerun_is_idempotent_via_rcept_no(self, mock_get):
        mock_get.return_value = {
            'status': '000',
            'total_page': 1,
            'list': [{
                'corp_code': '00126380', 'corp_name': '삼성전자', 'stock_code': '005930',
                'report_nm': '분기보고서', 'rcept_no': '20260810000001', 'rcept_dt': '20260810',
            }],
        }
        crawlers_kr_disclosure.crawl_kr_disclosures(target_date=date(2026, 8, 10))
        crawlers_kr_disclosure.crawl_kr_disclosures(target_date=date(2026, 8, 10))
        self.assertEqual(KrDisclosure.objects.count(), 1)


class SyncKrEarningsTests(TestCase):
    def setUp(self):
        self.symbol = _make_kr_symbol(dart_corp_code='00126380')

    def test_skips_symbol_without_corp_code(self):
        symbol = _make_kr_symbol(ticker='000001', name='매핑안됨', dart_corp_code=None)
        count = crawlers_kr_earnings.sync_kr_earnings_for_symbol(symbol, periods=[('2026', '11013')])
        self.assertEqual(count, 0)

    @mock.patch.object(dart_client, 'get_financial_facts')
    def test_upserts_matched_facts(self, mock_get):
        mock_get.return_value = [
            {'sj_nm': '손익계산서', 'account_nm': '매출액', 'thstrm_amount': '74,566,317,000,000'},
            {'sj_nm': '손익계산서', 'account_nm': '영업이익', 'thstrm_amount': '4,676,057,000,000'},
            {'sj_nm': '손익계산서', 'account_nm': '반기순이익', 'thstrm_amount': '5,116,435,000,000'},
        ]
        count = crawlers_kr_earnings.sync_kr_earnings_for_symbol(
            self.symbol, periods=[('2025', '11012')]
        )
        self.assertEqual(count, 1)
        fact = KrFinancialFact.objects.get(symbol=self.symbol, bsns_year='2025', reprt_code='11012')
        self.assertEqual(fact.revenue, Decimal('74566317000000'))
        self.assertEqual(fact.fs_div, 'CFS')

    @mock.patch.object(dart_client, 'get_financial_facts')
    def test_falls_back_to_ofs_when_cfs_empty(self, mock_get):
        mock_get.side_effect = [
            [],  # CFS 조회 결과 없음
            [{'sj_nm': '손익계산서', 'account_nm': '매출액', 'thstrm_amount': '100'}],
        ]
        count = crawlers_kr_earnings.sync_kr_earnings_for_symbol(
            self.symbol, periods=[('2025', '11012')]
        )
        self.assertEqual(count, 1)
        fact = KrFinancialFact.objects.get(symbol=self.symbol, bsns_year='2025', reprt_code='11012')
        self.assertEqual(fact.fs_div, 'OFS')

    @mock.patch.object(dart_client, 'get_financial_facts')
    def test_unmatched_accounts_skip_without_raising(self, mock_get):
        mock_get.return_value = [
            {'sj_nm': '손익계산서', 'account_nm': '알수없는계정', 'thstrm_amount': '1'},
        ]
        count = crawlers_kr_earnings.sync_kr_earnings_for_symbol(
            self.symbol, periods=[('2025', '11012')]
        )
        self.assertEqual(count, 0)
        self.assertFalse(KrFinancialFact.objects.filter(symbol=self.symbol).exists())

    @mock.patch.object(dart_client, 'get_financial_facts')
    def test_dart_api_error_propagates_to_stop_batch(self, mock_get):
        mock_get.side_effect = dart_client.DartAPIError('020', '사용한도초과')
        with self.assertRaises(dart_client.DartAPIError):
            crawlers_kr_earnings.sync_kr_earnings_for_symbol(self.symbol, periods=[('2025', '11012')])


class KrDisclosureApiAuthTests(TestCase):
    def setUp(self):
        self.symbol = _make_kr_symbol()
        self.user = User.objects.create_user(username='tester', password='pw12345!')
        self.disclosure = KrDisclosure.objects.create(
            symbol=self.symbol, corp_code='00126380', corp_name='삼성전자',
            rcept_no='20260810000001', report_name='분기보고서',
            rcept_dt=date(2026, 8, 10),
            source_url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260810000001',
        )

    def test_requires_authentication(self):
        client = APIClient()
        resp = client.get('/api/kr-disclosures/')
        self.assertEqual(resp.status_code, 401)

    def test_authenticated_user_can_list(self):
        client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = client.get('/api/kr-disclosures/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['results'][0]['rcept_no'], '20260810000001')


class KrFinancialFactYoyTests(TestCase):
    def setUp(self):
        self.symbol = _make_kr_symbol(dart_corp_code='00126380')
        self.user = User.objects.create_user(username='tester2', password='pw12345!')
        KrFinancialFact.objects.create(
            symbol=self.symbol, bsns_year='2025', reprt_code='11012',
            revenue=Decimal('100'), operating_profit=Decimal('10'), net_profit=Decimal('5'),
        )
        self.current = KrFinancialFact.objects.create(
            symbol=self.symbol, bsns_year='2026', reprt_code='11012',
            revenue=Decimal('150'), operating_profit=Decimal('20'), net_profit=Decimal('10'),
        )

    def test_yoy_pct_computed_against_prior_year_same_reprt_code(self):
        client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = client.get(f'/api/kr-financial-facts/?symbol={self.symbol.id}&ordering=-bsns_year')
        self.assertEqual(resp.status_code, 200)
        current_row = resp.data['results'][0]
        self.assertEqual(current_row['bsns_year'], '2026')
        self.assertEqual(current_row['revenue_yoy_pct'], 50.0)
        self.assertEqual(current_row['operating_profit_yoy_pct'], 100.0)
        self.assertEqual(current_row['net_profit_yoy_pct'], 100.0)

    def test_yoy_pct_null_when_no_prior_year(self):
        oldest = KrFinancialFact.objects.get(bsns_year='2025')
        client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = client.get(f'/api/kr-financial-facts/?symbol={self.symbol.id}&ordering=bsns_year')
        first_row = resp.data['results'][0]
        self.assertEqual(first_row['bsns_year'], '2025')
        self.assertIsNone(first_row['revenue_yoy_pct'])
