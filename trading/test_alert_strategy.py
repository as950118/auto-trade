"""
Strategy + StrategyLink: default 상속, override, fan-out, 멱등 테스트
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .alert_sizing import AlertSizingError, config_from_link, size_alert_trade, split_amounts
from .models import (
    Account,
    AlertEvent,
    AlertEventStatus,
    AlertTradeLeg,
    Broker,
    Country,
    Currency,
    Holding,
    Order,
    OrderSide,
    Strategy,
    StrategyLink,
    StrategyVisibility,
    Symbol,
)
from .services.alert_strategy import AlertStrategyService, process_due_alert_trade_legs


class StrategySizingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='suser', password='pass123')
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
        self.strategy = Strategy.objects.create(
            owner=self.user,
            title='Seed Strategy',
            default_trade_percent=Decimal('10'),
            default_max_position_weight_percent=Decimal('20'),
            default_split_count=1,
            default_split_interval_seconds=0,
            cooldown_seconds=0,
        )
        self.link = StrategyLink.objects.create(
            strategy=self.strategy,
            account=self.account,
            seed_amount=Decimal('10000000'),
            seed_currency=Currency.KRW,
        )

    def test_default_inheritance(self):
        cfg = config_from_link(self.link)
        self.assertEqual(cfg.trade_percent, Decimal('10'))
        self.assertEqual(cfg.max_position_weight_percent, Decimal('20'))
        self.assertEqual(cfg.split_count, 1)

    def test_override_priority(self):
        self.link.trade_percent = Decimal('5')
        self.link.max_position_weight_percent = Decimal('50')
        self.link.split_count = 3
        self.link.save()
        cfg = config_from_link(self.link)
        self.assertEqual(cfg.trade_percent, Decimal('5'))
        self.assertEqual(cfg.max_position_weight_percent, Decimal('50'))
        self.assertEqual(cfg.split_count, 3)

    def test_buy_seed_percent(self):
        Holding.objects.create(
            account=self.account,
            symbol=self.symbol,
            quantity=Decimal('0'),
            average_price=Decimal('100000000'),
            current_price=Decimal('100000000'),
        )
        result = size_alert_trade(
            self.link, self.account, self.symbol, OrderSide.BUY,
            reference_price=Decimal('100000000'),
        )
        self.assertEqual(result.notional, Decimal('1000000.00'))

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
                self.link, self.account, self.symbol, OrderSide.BUY,
                reference_price=Decimal('100000000'),
            )
        self.assertEqual(ctx.exception.code, 'MAX_WEIGHT_REACHED')

    def test_sell_no_holding(self):
        with self.assertRaises(AlertSizingError) as ctx:
            size_alert_trade(
                self.link, self.account, self.symbol, OrderSide.SELL,
                reference_price=Decimal('100000000'),
            )
        self.assertEqual(ctx.exception.code, 'NO_HOLDING')

    def test_split_amounts_remainder(self):
        parts = split_amounts(Decimal('100.00'), Decimal('3'), 3)
        self.assertEqual(sum(p[1] for p in parts), Decimal('3'))
        self.assertEqual(sum(p[0] for p in parts), Decimal('100.00'))


class StrategyWebhookFanoutTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='whuser', password='pass123')
        self.user2 = User.objects.create_user(username='whuser2', password='pass123')
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
        self.account2 = Account.objects.create(
            user=self.user2,
            broker=self.broker,
            api_key='k2',
            api_secret='s2',
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
        self.strategy = Strategy.objects.create(
            owner=self.user,
            title='Public Fanout',
            visibility=StrategyVisibility.PUBLIC,
            default_trade_percent=Decimal('10'),
            default_max_position_weight_percent=Decimal('50'),
            default_split_count=3,
            default_split_interval_seconds=60,
            cooldown_seconds=0,
            webhook_passphrase='tv-secret',
        )
        self.link1 = StrategyLink.objects.create(
            strategy=self.strategy,
            account=self.account,
            seed_amount=Decimal('10000000'),
        )
        self.link2 = StrategyLink.objects.create(
            strategy=self.strategy,
            account=self.account2,
            seed_amount=Decimal('10000000'),
        )
        for acc in (self.account, self.account2):
            Holding.objects.create(
                account=acc,
                symbol=self.symbol,
                quantity=Decimal('0'),
                average_price=Decimal('100000000'),
                current_price=Decimal('100000000'),
            )

    def test_fanout_creates_plans_per_link(self):
        payload = {
            'ticker': 'BTC-KRW',
            'action': 'BUY',
            'secret': 'tv-secret',
            'alert_id': 'fanout-1',
            'price': '100000000',
        }
        event = AlertStrategyService.handle_webhook(self.strategy.webhook_token, payload)
        self.assertEqual(event.status, AlertEventStatus.ACCEPTED)
        self.assertEqual(event.trade_plans.count(), 2)
        plan = event.trade_plans.first()
        self.assertEqual(plan.split_count, 3)
        self.assertEqual(plan.legs.count(), 3)

    def test_idempotency(self):
        payload = {
            'ticker': 'BTC-KRW',
            'action': 'BUY',
            'secret': 'tv-secret',
            'alert_id': 'same-id',
            'price': '100000000',
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
            'price': '100000000',
        }
        event = AlertStrategyService.handle_webhook(self.strategy.webhook_token, payload)
        self.assertEqual(event.status, AlertEventStatus.REJECTED)
        self.assertIn('NO_HOLDING', event.reject_reason or '')

    def test_process_due_legs_creates_orders(self):
        payload = {
            'ticker': 'BTC-KRW',
            'action': 'BUY',
            'secret': 'tv-secret',
            'alert_id': 'buy-due-1',
            'price': '100000000',
        }
        event = AlertStrategyService.handle_webhook(self.strategy.webhook_token, payload)
        AlertTradeLeg.objects.filter(plan__event=event).update(
            scheduled_at=timezone.now() - timedelta(seconds=1)
        )
        created = process_due_alert_trade_legs()
        self.assertEqual(created, 6)  # 2 links * 3 legs
        self.assertEqual(Order.objects.count(), 6)

    def test_webhook_http_endpoint(self):
        client = APIClient()
        url = reverse('tradingview-webhook', kwargs={'webhook_token': self.strategy.webhook_token})
        resp = client.post(
            url,
            {
                'ticker': 'BTC-KRW',
                'action': 'BUY',
                'secret': 'tv-secret',
                'alert_id': 'http-1',
                'price': '100000000',
            },
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], AlertEventStatus.ACCEPTED)

    def test_private_strategy_list_permission(self):
        private = Strategy.objects.create(
            owner=self.user,
            title='Private Only',
            visibility=StrategyVisibility.PRIVATE,
            default_trade_percent=Decimal('10'),
        )
        client = APIClient()
        client.force_authenticate(user=self.user2)
        resp = client.get('/api/strategies/')
        ids = [s['id'] for s in (resp.data.get('results') or resp.data)]
        self.assertNotIn(private.id, ids)
        self.assertIn(self.strategy.id, ids)  # PUBLIC

        client.force_authenticate(user=self.user)
        resp = client.get('/api/strategies/')
        ids = [s['id'] for s in (resp.data.get('results') or resp.data)]
        self.assertIn(private.id, ids)
