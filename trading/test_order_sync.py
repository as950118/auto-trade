"""
주문 상태 정규화(ADR-0004) + 미확정 주문 재조회(TASK-0017) + ccxt 기반 BingXClient(TASK-0015) 테스트.

거래소 호출은 모두 mock이다(네트워크·실주문 없음).
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .clients import (
    ORDER_STATUS_CANCELED,
    ORDER_STATUS_FILLED,
    ORDER_STATUS_OPEN,
    ORDER_STATUS_UNKNOWN,
    BingXClient,
    UpbitClient,
    order_status_result,
)
from .models import Account, Broker, Country, Currency, DailyRealizedProfit, Order, OrderStatus, Symbol
from .tasks import check_order_status, sync_open_orders


class UpbitNormalizeTestCase(SimpleTestCase):
    """기존 tasks.check_order_status()의 Upbit 해석과 같은 결과여야 한다."""

    def test_done_is_filled(self):
        r = UpbitClient._normalize_order({'state': 'done', 'executed_volume': '0.5', 'avg_price': '100', 'uuid': 'u1'})
        self.assertEqual(r['status'], ORDER_STATUS_FILLED)
        self.assertEqual(r['filled_quantity'], Decimal('0.5'))
        self.assertEqual(r['average_price'], Decimal('100.0'))
        self.assertEqual(r['external_order_id'], 'u1')

    def test_cancel_is_canceled(self):
        r = UpbitClient._normalize_order({'state': 'cancel', 'executed_volume': '0'})
        self.assertEqual(r['status'], ORDER_STATUS_CANCELED)

    def test_wait_is_open_and_missing_avg_price_is_none(self):
        r = UpbitClient._normalize_order({'state': 'wait', 'executed_volume': '0.1'})
        self.assertEqual(r['status'], ORDER_STATUS_OPEN)
        self.assertEqual(r['filled_quantity'], Decimal('0.1'))
        self.assertIsNone(r['average_price'])


class _BingXFixture(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='bx', password='pass123')
        self.broker, _ = Broker.objects.get_or_create(
            code='BINGX', defaults={'name': 'BingX', 'country': Country.USA, 'is_crypto_exchange': True},
        )
        self.account = Account.objects.create(user=self.user, broker=self.broker, api_key='k', api_secret='s')
        self.symbol = Symbol.objects.create(
            ticker='BTC-USDT', name='Bitcoin', currency=Currency.USD, broker=self.broker, is_crypto=True,
        )
        BingXClient._markets = None
        BingXClient._markets_loaded_at = 0.0

    def tearDown(self):
        BingXClient._markets = None
        BingXClient._markets_loaded_at = 0.0

    def _client(self) -> BingXClient:
        client = BingXClient(self.account)
        client.exchange = MagicMock()
        client.exchange.markets = {}
        client.exchange.load_markets.return_value = {'BTC/USDT': {'id': 'BTC-USDT'}}
        return client

    def _order(self, **kwargs) -> Order:
        defaults = dict(
            account=self.account, symbol=self.symbol, side='BUY', order_type='MARKET',
            quantity=Decimal('0.01'), status=OrderStatus.PARTIALLY_FILLED, external_order_id='ex-1',
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)


class BingXClientTestCase(_BingXFixture):
    def test_to_ccxt_symbol(self):
        self.assertEqual(BingXClient.to_ccxt_symbol('BTC-USDT'), 'BTC/USDT')
        self.assertEqual(BingXClient.to_ccxt_symbol('ETH'), 'ETH/USDT')
        self.assertEqual(BingXClient.to_ccxt_symbol('BTC/USDT'), 'BTC/USDT')

    def test_market_order_sends_base_quantity_without_price(self):
        client = self._client()
        client.exchange.create_order.return_value = {'id': '123'}
        order = self._order(status=OrderStatus.SUBMITTING, external_order_id=None)

        result = client.place_order(order)

        self.assertEqual(result, {'success': True, 'order_id': '123', 'data': {'id': '123'}})
        client.exchange.create_order.assert_called_once_with('BTC/USDT', 'market', 'buy', 0.01, None)

    def test_limit_order_requires_price(self):
        client = self._client()
        # 모델 save()가 가격 없는 지정가를 거부하므로 저장하지 않은 인스턴스로 방어 로직만 확인한다
        order = Order(account=self.account, symbol=self.symbol, side='BUY', order_type='LIMIT',
                      quantity=Decimal('0.01'), price=None)

        result = client.place_order(order)

        self.assertFalse(result['success'])
        client.exchange.create_order.assert_not_called()

    def test_limit_order_passes_price(self):
        client = self._client()
        client.exchange.create_order.return_value = {'id': '9'}
        order = self._order(order_type='LIMIT', side='SELL', price=Decimal('65000.5'))

        client.place_order(order)

        client.exchange.create_order.assert_called_once_with('BTC/USDT', 'limit', 'sell', 0.01, 65000.5)

    def test_exchange_error_is_returned_not_raised(self):
        client = self._client()
        client.exchange.create_order.side_effect = Exception('insufficient balance')

        result = client.place_order(self._order())

        self.assertEqual(result, {'success': False, 'error': 'insufficient balance'})

    def test_get_order_status_normalizes_closed_order(self):
        client = self._client()
        client.exchange.fetch_order.return_value = {'id': 'ex-1', 'status': 'closed', 'filled': 0.01, 'average': 65000.0}

        result = client.get_order_status(self._order())

        self.assertEqual(result['status'], ORDER_STATUS_FILLED)
        self.assertEqual(result['filled_quantity'], Decimal('0.01'))
        self.assertEqual(result['average_price'], Decimal('65000.0'))
        client.exchange.fetch_order.assert_called_once_with('ex-1', 'BTC/USDT')

    def test_get_order_status_unknown_ccxt_status(self):
        client = self._client()
        client.exchange.fetch_order.return_value = {'id': 'ex-1', 'status': None, 'filled': None, 'average': None}

        result = client.get_order_status(self._order())

        self.assertEqual(result['status'], ORDER_STATUS_UNKNOWN)
        self.assertEqual(result['filled_quantity'], Decimal('0'))

    def test_get_order_status_without_external_id_fails(self):
        client = self._client()

        result = client.get_order_status(self._order(external_order_id=None))

        self.assertFalse(result['success'])
        client.exchange.fetch_order.assert_not_called()

    def test_markets_loaded_once_across_clients(self):
        first = self._client()
        first.place_order(self._order())
        second = self._client()
        second.place_order(self._order())

        first.exchange.load_markets.assert_called_once()
        second.exchange.load_markets.assert_not_called()
        second.exchange.set_markets.assert_called_once_with({'BTC/USDT': {'id': 'BTC-USDT'}})

    def test_account_info_maps_balances(self):
        client = self._client()
        client.exchange.fetch_balance.return_value = {
            'info': {'data': {'balances': [{'asset': 'BTC', 'disPlayName': 'Bitcoin'}]}},
            'total': {'USDT': 100.0, 'BTC': 0.5, 'ETH': 0.0},
        }
        client.exchange.fetch_ticker.return_value = {'last': 60000.0}

        info = client.get_account_info()

        self.assertTrue(info['success'])
        self.assertEqual(info['cash_balance_usd'], Decimal('100.0'))
        self.assertEqual(info['stock_value_usd'], Decimal('30000.00'))
        self.assertEqual(info['total_assets_usd'], Decimal('30100.00'))
        self.assertEqual(len(info['holdings']), 1)
        self.assertEqual(info['holdings'][0]['ticker'], 'BTC-USDT')
        self.assertEqual(info['holdings'][0]['name'], 'Bitcoin')


class CheckOrderStatusTestCase(_BingXFixture):
    def _fake_client(self, result):
        client = MagicMock()
        client.get_order_status.return_value = result
        return client

    def test_bingx_order_is_finalized_as_filled(self):
        """TASK-0017 회귀: 예전에는 BingX 주문이 'pass' 분기로 빠져 PARTIALLY_FILLED에 멈췄다."""
        order = self._order()
        client = self._fake_client(order_status_result(
            ORDER_STATUS_FILLED, filled_quantity=Decimal('0.01'), average_price=Decimal('65000'),
        ))

        with patch('trading.strategy_fees.on_strategy_order_filled') as fee_hook:
            check_order_status(order, client)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.filled_quantity, Decimal('0.01'))
        self.assertEqual(order.average_filled_price, Decimal('65000'))
        self.assertIsNotNone(order.filled_at)
        fee_hook.assert_called_once()
        daily = DailyRealizedProfit.objects.get(account=self.account, date=order.filled_at.date())
        self.assertEqual(daily.total_buy_amount, Decimal('650.00'))

    def test_already_filled_order_does_not_rerun_hooks(self):
        filled_at = timezone.now() - timedelta(hours=1)
        order = self._order(status=OrderStatus.FILLED, filled_at=filled_at)
        client = self._fake_client(order_status_result(ORDER_STATUS_FILLED, filled_quantity=Decimal('0.01')))

        with patch('trading.strategy_fees.on_strategy_order_filled') as fee_hook:
            check_order_status(order, client)

        order.refresh_from_db()
        self.assertEqual(order.filled_at, filled_at)
        fee_hook.assert_not_called()

    def test_open_with_partial_fill(self):
        order = self._order(status=OrderStatus.SUBMITTING)
        client = self._fake_client(order_status_result(ORDER_STATUS_OPEN, filled_quantity=Decimal('0.004')))

        check_order_status(order, client)

        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
        self.assertEqual(order.filled_quantity, Decimal('0.004'))

    def test_canceled(self):
        order = self._order()
        check_order_status(order, self._fake_client(order_status_result(ORDER_STATUS_CANCELED)))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.CANCELLED)

    def test_unknown_leaves_status_unchanged(self):
        order = self._order()
        check_order_status(order, self._fake_client(order_status_result(ORDER_STATUS_UNKNOWN)))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)

    def test_failed_lookup_leaves_status_unchanged(self):
        order = self._order()
        check_order_status(order, self._fake_client({'success': False, 'error': 'timeout'}))
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)

    def test_saves_external_order_id_when_missing(self):
        order = self._order(external_order_id=None, status=OrderStatus.SUBMITTING)
        check_order_status(order, self._fake_client(order_status_result(ORDER_STATUS_OPEN, external_order_id='new-id')))
        order.refresh_from_db()
        self.assertEqual(order.external_order_id, 'new-id')


class SyncOpenOrdersTestCase(_BingXFixture):
    def test_only_recent_unfinalized_orders_with_external_id_are_polled(self):
        target = self._order()
        no_ext = self._order(external_order_id=None)
        empty_ext = self._order(external_order_id='')
        filled = self._order(status=OrderStatus.FILLED)
        old = self._order()
        Order.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=8))

        client = MagicMock()
        client.get_order_status.return_value = order_status_result(
            ORDER_STATUS_FILLED, filled_quantity=Decimal('0.01'), average_price=Decimal('1'),
        )
        with patch('trading.tasks.get_broker_client', return_value=client) as factory:
            checked = sync_open_orders(lookback_days=7)

        self.assertEqual(checked, 1)
        factory.assert_called_once()
        target.refresh_from_db()
        self.assertEqual(target.status, OrderStatus.FILLED)
        for untouched in (no_ext, empty_ext, old):
            untouched.refresh_from_db()
            self.assertEqual(untouched.status, OrderStatus.PARTIALLY_FILLED)
        filled.refresh_from_db()
        self.assertEqual(filled.status, OrderStatus.FILLED)

    def test_client_creation_failure_skips_order(self):
        order = self._order()
        with patch('trading.tasks.get_broker_client', side_effect=ValueError('bad broker')):
            checked = sync_open_orders()

        self.assertEqual(checked, 0)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.PARTIALLY_FILLED)
