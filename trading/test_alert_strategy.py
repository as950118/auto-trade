"""
TradingView Alert 전략: 사이징, 가드, webhook, 분할 Leg 테스트
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .alert_sizing import AlertSizingError, size_alert_trade, split_amounts
from .models import (
    Account,
    AlertEvent,
    AlertEventStatus,
    AlertStrategy,
    AlertTradeLeg,
    Broker,
    Country,
    Currency,
    Holding,
    Order,
    OrderSide,
    Symbol,
)
from .services.alert_strategy import AlertStrategyService, process_due_alert_trade_legs


class AlertSizingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='alertuser', password='pass123')
        self.broker = Broker.objects.create(
            code='UPBIT', name='Upbit', country=Country.KOREA, is_crypto_exchange=True
        )
        self.account = Account.objects.create(
            user=self.user,
            broker=self.broker,
            api_key='k',
            api_secret='s',
            cash_balance_krw=Decimal('5000000'),
            buy_enabled=True,
            sell_enabled=True,
        )
        self.symbol = Symbol.objects.create(
            ticker='BTC-KRW',
            name='Bitcoin',
            currency=Currency.KRW,
            broker=self.broker,
            is_crypto=True,
        )
        self.strategy = AlertStrategy.objects.create(
            account=self.account,
            name='Seed Strategy',
            seed_amount=Decimal('10000000'),
            seed_currency=Currency.KRW,
            buy_seed_percent=Decimal('10'),
            sell_seed_percent=Decimal('10'),
            max_position_weight_percent=Decimal('20'),
            split_count=1,
            split_interval_seconds=0,
            cooldown_seconds=0,
        )

    def test_buy_seed_percent(self):
        Holding.objects.create(
            account=self.account,
            symbol=self.symbol,
            quantity=Decimal('0'),
            average_price=Decimal('100000000'),
            current_price=Decimal('100000000'),
        )
        result = size_alert_trade(
            self.strategy, self.account, self.symbol, OrderSide.BUY,
            reference_price=Decimal('100000000'),
        )
        # 10% of 10_000_000 = 1_000_000
        self.assertEqual(result.notional, Decimal('1000000.00'))
        self.assertGreater(result.quantity, 0)

    def test_buy_max_weight_cap(self):
        # Already at 15% of seed (1.5M), max 20% → room 500k, buy wants 1M → capped to 500k
        Holding.objects.create(
            account=self.account,
            symbol=self.symbol,
            quantity=Decimal('0.015'),
            average_price=Decimal('100000000'),
            current_price=Decimal('100000000'),
        )
        result = size_alert_trade(
            self.strategy, self.account, self.symbol, OrderSide.BUY,
            reference_price=Decimal('100000000'),
        )
        self.assertEqual(result.notional, Decimal('500000.00'))

    def test_buy_max_weight_reached(self):
        Holding.objects.create(
            account=self.account,
            symbol=self.symbol,
            quantity=Decimal('0.025'),
            average_price=Decimal('100000000'),
            current_price=Decimal('100000000'),
        )
        with self.assertRaises(AlertSizingError) as ctx:
            size_alert_trade(
                self.strategy, self.account, self.symbol, OrderSide.BUY,
                reference_price=Decimal('100000000'),
            )
        self.assertEqual(ctx.exception.code, 'MAX_WEIGHT_REACHED')

    def test_sell_no_holding(self):
        with self.assertRaises(AlertSizingError) as ctx:
            size_alert_trade(
                self.strategy, self.account, self.symbol, OrderSide.SELL,
                reference_price=Decimal('100000000'),
            )
        self.assertEqual(ctx.exception.code, 'NO_HOLDING')

    def test_sell_capped_by_holding(self):
        Holding.objects.create(
            account=self.account,
            symbol=self.symbol,
            quantity=Decimal('0.005'),  # 500k value
            average_price=Decimal('100000000'),
            current_price=Decimal('100000000'),
        )
        result = size_alert_trade(
            self.strategy, self.account, self.symbol, OrderSide.SELL,
            reference_price=Decimal('100000000'),
        )
        # sell wants 1M notional but only 0.005 qty available
        self.assertEqual(result.quantity, Decimal('0.005'))

    def test_split_amounts_remainder(self):
        parts = split_amounts(Decimal('100.00'), Decimal('3'), 3)
        self.assertEqual(len(parts), 3)
        total_qty = sum(p[1] for p in parts)
        total_notional = sum(p[0] for p in parts)
        self.assertEqual(total_qty, Decimal('3'))
        self.assertEqual(total_notional, Decimal('100.00'))


class AlertWebhookServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='whuser', password='pass123')
        self.broker = Broker.objects.create(
            code='UPBIT', name='Upbit', country=Country.KOREA, is_crypto_exchange=True
        )
        self.account = Account.objects.create(
            user=self.user,
            broker=self.broker,
            api_key='k',
            api_secret='s',
            cash_balance_krw=Decimal('5000000'),
            buy_enabled=True,
            sell_enabled=True,
        )
        self.symbol = Symbol.objects.create(
            ticker='BTC-KRW',
            name='Bitcoin',
            currency=Currency.KRW,
            broker=self.broker,
            is_crypto=True,
        )
        self.strategy = AlertStrategy.objects.create(
            account=self.account,
            name='WH Strategy',
            seed_amount=Decimal('10000000'),
            buy_seed_percent=Decimal('10'),
            sell_seed_percent=Decimal('10'),
            max_position_weight_percent=Decimal('50'),
            split_count=3,
            split_interval_seconds=60,
            cooldown_seconds=0,
            webhook_passphrase='tv-secret',
        )
        Holding.objects.create(
            account=self.account,
            symbol=self.symbol,
            quantity=Decimal('0'),
            average_price=Decimal('100000000'),
            current_price=Decimal('100000000'),
        )

    def test_idempotency(self):
        payload = {
            'ticker': 'BTC-KRW',
            'action': 'BUY',
            'secret': 'tv-secret',
            'alert_id': 'same-id-1',
        }
        e1 = AlertStrategyService.handle_webhook(self.strategy.webhook_token, payload)
        e2 = AlertStrategyService.handle_webhook(self.strategy.webhook_token, payload)
        self.assertEqual(e1.id, e2.id)
        self.assertEqual(AlertEvent.objects.filter(strategy=self.strategy).count(), 1)

    def test_reject_sell_without_holding(self):
        payload = {
            'ticker': 'BTC-KRW',
            'action': 'SELL',
            'secret': 'tv-secret',
            'alert_id': 'sell-1',
        }
        event = AlertStrategyService.handle_webhook(self.strategy.webhook_token, payload)
        self.assertEqual(event.status, AlertEventStatus.REJECTED)
        self.assertIn('NO_HOLDING', event.reject_reason or '')

    def test_accept_buy_creates_split_legs(self):
        payload = {
            'ticker': 'BTC-KRW',
            'action': 'BUY',
            'secret': 'tv-secret',
            'alert_id': 'buy-split-1',
        }
        event = AlertStrategyService.handle_webhook(self.strategy.webhook_token, payload)
        self.assertEqual(event.status, AlertEventStatus.ACCEPTED)
        plan = event.trade_plan
        self.assertEqual(plan.split_count, 3)
        self.assertEqual(plan.legs.count(), 3)
        legs = list(plan.legs.order_by('seq'))
        self.assertEqual(legs[0].scheduled_at, legs[1].scheduled_at - timedelta(seconds=60))

    def test_process_due_legs_creates_orders(self):
        payload = {
            'ticker': 'BTC-KRW',
            'action': 'BUY',
            'secret': 'tv-secret',
            'alert_id': 'buy-due-1',
        }
        event = AlertStrategyService.handle_webhook(self.strategy.webhook_token, payload)
        # Make all legs due now
        AlertTradeLeg.objects.filter(plan=event.trade_plan).update(
            scheduled_at=timezone.now() - timedelta(seconds=1)
        )
        created = process_due_alert_trade_legs()
        self.assertEqual(created, 3)
        self.assertEqual(Order.objects.filter(account=self.account).count(), 3)
        event.refresh_from_db()
        self.assertEqual(event.status, AlertEventStatus.COMPLETED)

    def test_webhook_http_endpoint(self):
        client = APIClient()
        url = reverse('tradingview-webhook', kwargs={'webhook_token': self.strategy.webhook_token})
        resp = client.post(
            url,
            {'ticker': 'BTC-KRW', 'action': 'BUY', 'secret': 'tv-secret', 'alert_id': 'http-1'},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], AlertEventStatus.ACCEPTED)

    def test_invalid_passphrase(self):
        payload = {
            'ticker': 'BTC-KRW',
            'action': 'BUY',
            'secret': 'wrong',
            'alert_id': 'bad-secret',
        }
        event = AlertStrategyService.handle_webhook(self.strategy.webhook_token, payload)
        self.assertEqual(event.status, AlertEventStatus.REJECTED)
        self.assertIn('INVALID_PASSPHRASE', event.reject_reason or '')
