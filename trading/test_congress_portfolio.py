"""
의회의원 공시 거래(CongressTrade) 크롤링 파싱/중복방지 및 포트폴리오 동기화(services/congress_portfolio) 테스트.
네트워크(House Clerk 사이트) 호출은 모두 mock 처리한다.
"""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from . import crawlers_congress
from .models import (
    Broker,
    CongressChamber,
    CongressMember,
    CongressTrade,
    Country,
    Currency,
    Order,
    OrderSide,
    Portfolio,
    PortfolioLink,
    PortfolioVisibility,
    Symbol,
)
from .services.congress_portfolio import (
    _compute_net_positions,
    _normalize_weights,
    get_or_create_system_owner,
    sync_member_portfolio,
)
from .services.portfolio import rebalance_link


class FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class FakePdf:
    def __init__(self, text):
        self.pages = [FakePage(text)]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# 실제 하원 PTR PDF에서 확인된 텍스트 추출 패턴 (줄바꿈 위치가 문서마다 다름을 재현)
SINGLE_ROW_TEXT = (
    "ID Owner Asset Transaction Date Notification Amount Cap.\n"
    "GSK plc American Depositary Shares S 07/28/2025 08/11/2025 $1,001 - $15,000\n"
    "(GSK) [ST]\n"
)
MULTI_ROW_TEXT = (
    "ID Owner Asset Transaction Date Notification Amount Cap.\n"
    "$200?\n"
    "SP Broadcom Inc. - Common Stock P 06/20/2025 06/20/2025 $1,000,001 -\n"
    "(AVGO) [ST] $5,000,000\n"
    "Filing Status: New\n"
    "Description: Exercised 200 call options purchased 6/24/24 (20,000 shares) at a strike price of $80"
    " with an expiration date of 6/20/25.\n"
    "SP Matthews International Mutual Fund S 06/20/2025 06/20/2025 $15,001 - $50,000\n"
    "[OT]\n"
)


class ParsePtrPdfTestCase(TestCase):
    def test_single_row_with_ticker_after_amount(self):
        with mock.patch("pdfplumber.open", return_value=FakePdf(SINGLE_ROW_TEXT)):
            results = crawlers_congress.parse_ptr_pdf(b"fake-pdf-bytes")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["raw_ticker"], "GSK")
        self.assertEqual(results[0]["transaction_type"], "SELL")
        self.assertEqual(results[0]["transaction_date"], "07/28/2025")
        self.assertEqual(results[0]["amount_min"], Decimal("1001"))
        self.assertEqual(results[0]["amount_max"], Decimal("15000"))

    def test_multi_row_ticker_between_amount_and_untickered_asset_skipped(self):
        with mock.patch("pdfplumber.open", return_value=FakePdf(MULTI_ROW_TEXT)):
            results = crawlers_congress.parse_ptr_pdf(b"fake-pdf-bytes")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["raw_ticker"], "AVGO")
        self.assertEqual(results[0]["transaction_type"], "BUY")
        self.assertEqual(results[0]["amount_min"], Decimal("1000001"))
        self.assertEqual(results[0]["amount_max"], Decimal("5000000"))
        # 뮤추얼펀드는 티커가 없어 raw_ticker가 비어 있어야 함(가장 가까운 티커였던 AVGO를 잘못 물지 않아야 함)
        self.assertEqual(results[1]["raw_ticker"], "")
        self.assertEqual(results[1]["transaction_type"], "SELL")


class IngestHouseTradesTestCase(TestCase):
    def setUp(self):
        self.broker = Broker.objects.create(
            code="TESTUS", name="Test US Broker", country=Country.USA, is_crypto_exchange=False
        )
        self.gsk = Symbol.objects.create(
            ticker="GSK", name="GSK plc", currency=Currency.USD, broker=self.broker,
        )
        self.index_text = (
            "Prefix\tLast\tFirst\tSuffix\tFilingType\tStateDst\tYear\tFilingDate\tDocID\n"
            "Hon.\tAderholt\tRobert B.\t\tP\tAL04\t2025\t9/10/2025\t20032062\n"
        )
        self.parsed_tx = [{
            "raw_ticker": "GSK",
            "transaction_type": "SELL",
            "transaction_date": "07/28/2025",
            "amount_min": Decimal("1001"),
            "amount_max": Decimal("15000"),
        }]

    def _run_ingest(self):
        with mock.patch.object(crawlers_congress, "_fetch_text", return_value=self.index_text), \
             mock.patch.object(crawlers_congress, "_fetch_bytes", return_value=b"fake-pdf") as fetch_bytes_mock, \
             mock.patch.object(crawlers_congress, "parse_ptr_pdf", return_value=self.parsed_tx):
            result = crawlers_congress.ingest_house_trades(years=[2025])
        return result, fetch_bytes_mock

    def test_first_run_creates_trade_and_member(self):
        (new_count, touched_ids), fetch_bytes_mock = self._run_ingest()
        self.assertEqual(new_count, 1)
        self.assertEqual(fetch_bytes_mock.call_count, 1)
        member = CongressMember.objects.get(name="Robert B. Aderholt", chamber=CongressChamber.HOUSE)
        self.assertIn(member.id, touched_ids)
        trade = CongressTrade.objects.get(member=member)
        self.assertEqual(trade.symbol, self.gsk)
        self.assertEqual(trade.transaction_date, date(2025, 7, 28))
        self.assertEqual(trade.disclosure_date, date(2025, 9, 10))

    def test_second_run_skips_already_ingested_filing(self):
        self._run_ingest()
        (new_count, touched_ids), fetch_bytes_mock = self._run_ingest()
        self.assertEqual(new_count, 0)
        self.assertEqual(touched_ids, set())
        # source_url로 이미 처리된 필링이라 PDF 재다운로드 자체가 스킵되어야 함
        self.assertEqual(fetch_bytes_mock.call_count, 0)
        self.assertEqual(CongressTrade.objects.count(), 1)


class CongressPortfolioSyncTestCase(TestCase):
    def setUp(self):
        self.broker = Broker.objects.create(
            code="TESTUS2", name="Test US Broker 2", country=Country.USA, is_crypto_exchange=False
        )
        self.krw_broker = Broker.objects.create(
            code="TESTKR", name="Test KR Broker", country=Country.KOREA, is_crypto_exchange=False
        )
        self.buy_symbol = Symbol.objects.create(
            ticker="BUYME", name="Net Buy Co", currency=Currency.USD, broker=self.broker,
        )
        self.sell_symbol = Symbol.objects.create(
            ticker="SELLME", name="Net Sell Co", currency=Currency.USD, broker=self.broker,
        )
        self.krw_symbol = Symbol.objects.create(
            ticker="KRWSTOCK", name="KRW Co", currency=Currency.KRW, broker=self.krw_broker,
        )
        self.member = CongressMember.objects.create(
            name="Test Member", chamber=CongressChamber.HOUSE, state="TX",
        )

    def _make_trade(self, symbol, tx_type, tx_date, amount_min, amount_max):
        return CongressTrade.objects.create(
            member=self.member,
            symbol=symbol,
            transaction_type=tx_type,
            transaction_date=tx_date,
            amount_min=Decimal(amount_min),
            amount_max=Decimal(amount_max),
            source_url=f"test://{symbol.ticker if symbol else 'none'}/{tx_date}/{tx_type}",
            crawled_at=timezone.now(),
        )

    def test_get_or_create_system_owner_is_staff(self):
        owner = get_or_create_system_owner()
        self.assertTrue(owner.is_staff)
        # 재호출 시 동일 계정 재사용
        owner2 = get_or_create_system_owner()
        self.assertEqual(owner.id, owner2.id)

    def test_compute_net_positions_excludes_negative_and_krw(self):
        self._make_trade(self.buy_symbol, "BUY", date(2025, 1, 1), "10000", "20000")
        self._make_trade(self.sell_symbol, "BUY", date(2025, 1, 1), "1000", "2000")
        self._make_trade(self.sell_symbol, "SELL", date(2025, 2, 1), "5000", "9000")
        self._make_trade(self.krw_symbol, "BUY", date(2025, 1, 1), "100000", "200000")

        net = _compute_net_positions(self.member, max_holdings=20)
        self.assertIn(self.buy_symbol.id, net)
        self.assertNotIn(self.sell_symbol.id, net)  # net matue: bought 1500 - sold 7000 = negative
        self.assertNotIn(self.krw_symbol.id, net)  # 단일 통화(USD) 제약으로 제외

    def test_normalize_weights_sum_within_100(self):
        net = {1: Decimal("300"), 2: Decimal("200"), 3: Decimal("500")}
        weights = _normalize_weights(net)
        self.assertLessEqual(sum(weights.values()), Decimal("100"))
        self.assertEqual(set(weights.keys()), {1, 2, 3})

    def test_sync_member_portfolio_creates_public_portfolio_with_single_currency(self):
        self._make_trade(self.buy_symbol, "BUY", date(2025, 1, 1), "10000", "20000")
        self._make_trade(self.krw_symbol, "BUY", date(2025, 1, 1), "100000", "200000")

        portfolio = sync_member_portfolio(self.member)

        self.assertEqual(portfolio.visibility, PortfolioVisibility.PUBLIC)
        self.assertTrue(portfolio.owner.is_staff)
        currencies = {h.symbol.currency for h in portfolio.holdings.all()}
        self.assertEqual(currencies, {Currency.USD})
        weight_sum = sum(h.target_weight_percent for h in portfolio.holdings.all())
        self.assertLessEqual(weight_sum, Decimal("100"))

        self.member.refresh_from_db()
        self.assertEqual(self.member.portfolio_id, portfolio.id)
        self.assertIsNotNone(self.member.last_synced_at)

    def test_sync_member_portfolio_reuses_rebalance_engine(self):
        self._make_trade(self.buy_symbol, "BUY", date(2025, 1, 1), "10000", "20000")
        portfolio = sync_member_portfolio(self.member)

        subscriber = User.objects.create_user(username="congress_sub", password="pass123")
        from .models import Account
        account = Account.objects.create(
            user=subscriber, broker=self.broker, api_key="k", api_secret="s",
            buy_enabled=True, sell_enabled=True,
        )
        from .models import Holding
        Holding.objects.create(
            account=account, symbol=self.buy_symbol, quantity=Decimal("0"), current_price=Decimal("100"),
        )
        link = PortfolioLink.objects.create(
            portfolio=portfolio, account=account, seed_amount=Decimal("1000"), seed_currency=Currency.USD,
        )

        created = rebalance_link(link)
        self.assertEqual(created, 1)
        order = Order.objects.get(account=account, symbol=self.buy_symbol)
        self.assertEqual(order.side, OrderSide.BUY)

    def test_sync_all_portfolios_skips_members_without_trades(self):
        from .services.congress_portfolio import sync_all_portfolios

        CongressMember.objects.create(name="No Trades Member", chamber=CongressChamber.HOUSE)
        self._make_trade(self.buy_symbol, "BUY", date(2025, 1, 1), "10000", "20000")

        synced = sync_all_portfolios()
        self.assertEqual(synced, 1)
        self.assertIsNone(Portfolio.objects.filter(congress_member__name="No Trades Member").first())
