"""
TASK-0007: alert_sizing 백테스트 하네스 테스트
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from .alert_sizing import size_trade, AlertSizingError, EffectiveTradeConfig
from .models import (
    Account,
    AlertEvent,
    AlertTradePlan,
    Broker,
    Country,
    Currency,
    OrderSide,
    Strategy,
    StrategyLink,
    Symbol,
)
from .services.backtest import ConfigOverride, run_alert_sizing_backtest


class SizeTradePureFunctionTestCase(TestCase):
    """size_trade()가 ORM 없이도 size_alert_trade()와 동일한 계산을 하는지 확인"""

    def test_buy_matches_live_calculation(self):
        config = EffectiveTradeConfig(
            seed_amount=Decimal('1000000'),
            seed_currency=Currency.KRW,
            trade_percent=Decimal('10'),
            max_position_weight_percent=Decimal('50'),
            split_count=1,
            split_interval_seconds=0,
            order_type='MARKET',
        )
        result = size_trade(
            config,
            OrderSide.BUY,
            reference_price=Decimal('10000'),
            position_value=Decimal('0'),
            holding_quantity=Decimal('0'),
            cash_balance=Decimal('1000000'),
        )
        self.assertEqual(result.notional, Decimal('100000.00'))
        self.assertEqual(result.quantity, Decimal('10'))

    def test_sell_without_holding_raises(self):
        config = EffectiveTradeConfig(
            seed_amount=Decimal('1000000'),
            seed_currency=Currency.KRW,
            trade_percent=Decimal('10'),
            max_position_weight_percent=Decimal('50'),
            split_count=1,
            split_interval_seconds=0,
            order_type='MARKET',
        )
        with self.assertRaises(AlertSizingError) as ctx:
            size_trade(
                config,
                OrderSide.SELL,
                reference_price=Decimal('10000'),
                position_value=Decimal('0'),
                holding_quantity=Decimal('0'),
                cash_balance=Decimal('1000000'),
            )
        self.assertEqual(ctx.exception.code, 'NO_HOLDING')


class AlertSizingBacktestTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='btuser', password='pass123')
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
            ticker='BTC-KRW', name='Bitcoin', currency=Currency.KRW,
            broker=self.broker, is_crypto=True,
        )
        self.strategy = Strategy.objects.create(
            owner=self.user,
            title='Backtest Strategy',
            default_trade_percent=Decimal('10'),
            default_max_position_weight_percent=Decimal('50'),
            default_split_count=1,
            default_split_interval_seconds=0,
            cooldown_seconds=0,
        )
        self.link = StrategyLink.objects.create(
            strategy=self.strategy,
            account=self.account,
            seed_amount=Decimal('1000000'),
            seed_currency=Currency.KRW,
        )
        self.event = AlertEvent.objects.create(
            strategy=self.strategy, raw_payload={}, ticker=self.symbol.ticker, action=OrderSide.BUY,
        )

    def _create_plan(self, *, notional, quantity, price, created_at, side=OrderSide.BUY):
        plan = AlertTradePlan.objects.create(
            event=self.event,
            strategy_link=self.link,
            account=self.account,
            symbol=self.symbol,
            side=side,
            total_notional=notional,
            total_quantity=quantity,
            reference_price=price,
            split_count=1,
        )
        AlertTradePlan.objects.filter(pk=plan.pk).update(created_at=created_at)
        plan.refresh_from_db()
        return plan

    def test_override_doubles_sim_notional_vs_actual(self):
        now = timezone.now()
        # 실제로는 default_trade_percent=10%로 두 번 매수(각 100,000/10개)했다고 가정
        self._create_plan(
            notional=Decimal('100000.00'), quantity=Decimal('10'),
            price=Decimal('10000'), created_at=now - timedelta(days=2),
        )
        self._create_plan(
            notional=Decimal('100000.00'), quantity=Decimal('10'),
            price=Decimal('10000'), created_at=now - timedelta(days=1),
        )

        report = run_alert_sizing_backtest(
            self.link,
            start=now - timedelta(days=3),
            end=now,
            initial_cash=Decimal('1000000'),
            override=ConfigOverride(trade_percent=Decimal('20')),
            fetch_mark_prices=False,
        )

        self.assertEqual(len(report.replays), 2)
        for replay in report.replays:
            self.assertIsNone(replay.sim_error)

        # 실제 경로: 100,000 * 2 = 200,000 지출, 20개 보유
        self.assertEqual(report.actual_cash, Decimal('800000.00'))
        self.assertEqual(report.actual_holdings[self.symbol.ticker], Decimal('20'))

        # 시뮬 경로: trade_percent 20%로 재사이징 -> 200,000 * 2 = 400,000 지출, 40개 보유
        self.assertEqual(report.sim_cash, Decimal('600000.00'))
        self.assertEqual(report.sim_holdings[self.symbol.ticker], Decimal('40'))

        # mark price 조회를 껐으므로 최종 평가액은 현금과 동일
        self.assertEqual(report.actual_final_value, report.actual_cash)
        self.assertEqual(report.sim_final_value, report.sim_cash)

    def test_lower_max_weight_override_triggers_guard_error(self):
        now = timezone.now()
        self._create_plan(
            notional=Decimal('100000.00'), quantity=Decimal('10'),
            price=Decimal('10000'), created_at=now - timedelta(days=2),
        )
        self._create_plan(
            notional=Decimal('100000.00'), quantity=Decimal('10'),
            price=Decimal('10000'), created_at=now - timedelta(days=1),
        )

        report = run_alert_sizing_backtest(
            self.link,
            start=now - timedelta(days=3),
            end=now,
            initial_cash=Decimal('1000000'),
            override=ConfigOverride(max_position_weight_percent=Decimal('5')),
            fetch_mark_prices=False,
        )

        # 1개월 매수 후 이미 시드 대비 10% 보유 -> max_position_weight_percent=5% 초과라 2번째부터 거부
        self.assertIsNone(report.replays[0].sim_error)
        self.assertTrue(report.replays[1].sim_error.startswith('MAX_WEIGHT_REACHED'))
