"""
ProfitCalculator(실현 손익, FIFO) 동작 고정 테스트 (TASK-0009).

순수 계산 함수(calculations.py)로 추출하기 전의 동작을 그대로 고정한다. 리팩터링 전후 모두
같은 결과가 나와야 한다.
"""
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .calculations import (
    Lot,
    consume_fifo,
    fifo_realized_profit,
    profit_rate_percent,
    rebalance_quantity,
    target_value_for_weight,
)
from .models import Account, Broker, Country, Currency, Order, OrderStatus, Symbol
from .profit_calculator import ProfitCalculator

DAY = date(2026, 9, 1)


def _at(hour: int) -> datetime:
    return timezone.make_aware(datetime(DAY.year, DAY.month, DAY.day, hour, 0))


class ProfitCalculatorTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='trader', password='pass123')
        self.broker = Broker.objects.create(
            code='UPBIT3', name='Upbit3', country=Country.KOREA, is_crypto_exchange=True
        )
        self.account = Account.objects.create(
            user=self.user, broker=self.broker, api_key='k', api_secret='s',
        )
        self.btc = Symbol.objects.create(
            ticker='BTC-KRW', name='Bitcoin', currency=Currency.KRW, broker=self.broker, is_crypto=True,
        )

    def _filled(self, side: str, quantity: str, price: str, hour: int) -> Order:
        return Order.objects.create(
            account=self.account,
            symbol=self.btc,
            side=side,
            order_type='MARKET',
            quantity=Decimal(quantity),
            status=OrderStatus.FILLED,
            filled_quantity=Decimal(quantity),
            average_filled_price=Decimal(price),
            filled_at=_at(hour),
        )

    def test_second_sell_uses_next_fifo_lot(self):
        self._filled('BUY', '1', '100', 1)
        self._filled('BUY', '1', '200', 2)
        first = self._filled('SELL', '1', '300', 3)
        second = self._filled('SELL', '1', '300', 4)

        self.assertEqual(ProfitCalculator.calculate_realized_profit_for_order(first), Decimal('200'))
        self.assertEqual(ProfitCalculator.calculate_realized_profit_for_order(second), Decimal('100'))

    def test_partial_lot_consumption(self):
        self._filled('BUY', '2', '100', 1)
        sell = self._filled('SELL', '1.5', '150', 2)

        self.assertEqual(ProfitCalculator.calculate_realized_profit_for_order(sell), Decimal('75'))

    def test_sell_beyond_bought_quantity_has_zero_cost_for_excess(self):
        self._filled('BUY', '1', '100', 1)
        sell = self._filled('SELL', '2', '150', 2)

        self.assertEqual(ProfitCalculator.calculate_realized_profit_for_order(sell), Decimal('200'))

    def test_buy_after_sell_is_not_matched(self):
        self._filled('BUY', '1', '100', 1)
        sell = self._filled('SELL', '1', '150', 2)
        self._filled('BUY', '1', '50', 3)

        self.assertEqual(ProfitCalculator.calculate_realized_profit_for_order(sell), Decimal('50'))

    def test_unfilled_or_buy_order_returns_zero(self):
        buy = self._filled('BUY', '1', '100', 1)
        pending = Order.objects.create(
            account=self.account, symbol=self.btc, side='SELL', order_type='MARKET',
            quantity=Decimal('1'), status=OrderStatus.PENDING,
        )

        self.assertEqual(ProfitCalculator.calculate_realized_profit_for_order(buy), Decimal('0'))
        self.assertEqual(ProfitCalculator.calculate_realized_profit_for_order(pending), Decimal('0'))

    def test_daily_realized_profit_aggregates_sells(self):
        self._filled('BUY', '1', '100', 1)
        self._filled('BUY', '1', '200', 2)
        self._filled('SELL', '1', '300', 3)
        self._filled('SELL', '1', '300', 4)

        result = ProfitCalculator.calculate_daily_realized_profit(self.account, DAY)

        self.assertEqual(result['realized_profit'], Decimal('300'))
        self.assertEqual(result['total_sell_amount'], Decimal('600'))
        self.assertEqual(result['realized_profit_rate'], Decimal('50'))
        # 현재 동작: total_buy_amount는 어디서도 누적되지 않아 항상 0이다(TASK-0009에서 발견,
        # 동작 변경은 별도 결정 사항이라 여기서는 현 상태를 고정만 한다).
        self.assertEqual(result['total_buy_amount'], Decimal('0'))

    def test_daily_realized_profit_rate_is_zero_without_sells(self):
        self._filled('BUY', '1', '100', 1)

        result = ProfitCalculator.calculate_daily_realized_profit(self.account, DAY)

        self.assertEqual(result['realized_profit'], Decimal('0'))
        self.assertEqual(result['realized_profit_rate'], Decimal('0'))


class CalculationsTestCase(SimpleTestCase):
    """calculations.py 순수 함수 단위 테스트 — DB 없이 실행된다."""

    def test_consume_fifo_returns_cost_and_remaining_without_mutating_input(self):
        lots = [Lot(Decimal('1'), Decimal('100')), Lot(Decimal('2'), Decimal('200'))]

        cost, remaining = consume_fifo(lots, Decimal('2'))

        self.assertEqual(cost, Decimal('300'))
        self.assertEqual(remaining, [Lot(Decimal('0'), Decimal('100')), Lot(Decimal('1'), Decimal('200'))])
        self.assertEqual(lots[0].quantity, Decimal('1'))

    def test_fifo_realized_profit_skips_lots_consumed_by_prior_sells(self):
        lots = [Lot(Decimal('1'), Decimal('100')), Lot(Decimal('1'), Decimal('200'))]

        profit = fifo_realized_profit(lots, [Decimal('1')], Decimal('1'), Decimal('300'))

        self.assertEqual(profit, Decimal('100'))

    def test_profit_rate_percent(self):
        self.assertEqual(profit_rate_percent(Decimal('50'), Decimal('200')), Decimal('25'))
        self.assertEqual(profit_rate_percent(Decimal('50'), Decimal('0')), Decimal('0'))

    def test_target_value_for_weight_rounds_down(self):
        self.assertEqual(
            target_value_for_weight(Decimal('1000'), Decimal('33.333'), Decimal('0.01')),
            Decimal('333.33'),
        )

    def test_rebalance_quantity_buy_and_capped_sell(self):
        quant = Decimal('0.00000001')
        self.assertEqual(rebalance_quantity(Decimal('300'), Decimal('100'), Decimal('0'), quant), Decimal('3'))
        self.assertEqual(rebalance_quantity(Decimal('-500'), Decimal('100'), Decimal('2'), quant), Decimal('2'))
        self.assertEqual(rebalance_quantity(Decimal('-100'), Decimal('100'), Decimal('0'), quant), Decimal('0'))
