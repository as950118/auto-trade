"""
StrategyLink 기반 사이징: 고정 시드 금액의 N% + 보유 비중 캡.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import List, Optional, Tuple, Union

from .models import Account, Holding, OrderSide, StrategyLink, Symbol


ZERO = Decimal('0')
QTY_QUANT = Decimal('0.00000001')
MONEY_QUANT = Decimal('0.01')


class AlertSizingError(Exception):
    """사이징/비중 검증 실패"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass
class EffectiveTradeConfig:
    """사이징에 사용할 최종 설정 (Link override ?? Strategy default)"""
    seed_amount: Decimal
    seed_currency: str
    trade_percent: Decimal
    max_position_weight_percent: Decimal
    split_count: int
    split_interval_seconds: int
    order_type: str
    min_sell_holding_percent: Optional[Decimal] = None


def config_from_link(link: StrategyLink) -> EffectiveTradeConfig:
    return EffectiveTradeConfig(
        seed_amount=link.seed_amount,
        seed_currency=link.seed_currency,
        trade_percent=link.effective_trade_percent,
        max_position_weight_percent=link.effective_max_position_weight_percent,
        split_count=link.effective_split_count,
        split_interval_seconds=link.effective_split_interval_seconds,
        order_type=link.strategy.order_type,
        min_sell_holding_percent=None,
    )


@dataclass
class SizingResult:
    side: str
    notional: Decimal
    quantity: Optional[Decimal]
    reference_price: Decimal
    position_value: Decimal
    current_weight_percent: Decimal


def _get_cash_balance(account: Account, currency: str) -> Decimal:
    if currency == 'KRW':
        return account.cash_balance_krw or ZERO
    if currency in ('USD', 'USDT'):
        return account.cash_balance_usd or ZERO
    return account.cash_balance_krw or ZERO


def get_holding(account: Account, symbol: Symbol) -> Optional[Holding]:
    try:
        return Holding.objects.get(account=account, symbol=symbol)
    except Holding.DoesNotExist:
        return None


def resolve_reference_price(holding: Optional[Holding], fallback: Optional[Decimal] = None) -> Decimal:
    if holding and holding.current_price and holding.current_price > 0:
        return Decimal(str(holding.current_price))
    if holding and holding.average_price and holding.average_price > 0:
        return Decimal(str(holding.average_price))
    if fallback is not None and fallback > 0:
        return Decimal(str(fallback))
    raise AlertSizingError('NO_PRICE', '기준 가격을 확인할 수 없습니다.')


def position_value_of(holding: Optional[Holding], price: Decimal) -> Decimal:
    if not holding or holding.quantity <= 0:
        return ZERO
    return (holding.quantity * price).quantize(MONEY_QUANT, rounding=ROUND_DOWN)


def size_alert_trade(
    config: Union[EffectiveTradeConfig, StrategyLink],
    account: Account,
    symbol: Symbol,
    side: str,
    reference_price: Optional[Decimal] = None,
) -> SizingResult:
    """시드% 기준으로 매수/매도 notional·quantity를 산정하고 비중 가드를 적용한다.

    실계좌(Account/Holding) 조회 담당. 순수 계산은 `size_trade()`에 위임한다
    (백테스트 등 DB 없이 재사용하려면 `size_trade()`를 직접 호출).
    """
    if isinstance(config, StrategyLink):
        config = config_from_link(config)

    holding = get_holding(account, symbol)
    price = resolve_reference_price(holding, reference_price)
    position_value = position_value_of(holding, price)
    cash_balance = _get_cash_balance(account, config.seed_currency or symbol.currency)
    holding_quantity = holding.quantity if holding else ZERO

    return size_trade(
        config,
        side,
        reference_price=price,
        position_value=position_value,
        holding_quantity=holding_quantity,
        cash_balance=cash_balance,
        investment_limit=account.investment_limit,
    )


def size_trade(
    config: EffectiveTradeConfig,
    side: str,
    *,
    reference_price: Decimal,
    position_value: Decimal,
    holding_quantity: Decimal,
    cash_balance: Decimal,
    investment_limit: Optional[Decimal] = None,
) -> SizingResult:
    """계좌/보유 ORM 없이 Decimal 값만으로 사이징을 계산하는 순수 함수.

    라이브 호출은 `size_alert_trade()`(Account/Holding에서 값을 뽑아 이 함수로 위임)를
    쓰고, 백테스트처럼 시뮬레이션된 값으로 재계산하고 싶을 때는 이 함수를 직접 쓴다.
    """
    if config.seed_amount <= 0:
        raise AlertSizingError('INVALID_SEED', '시드 금액이 올바르지 않습니다.')

    current_weight = (position_value / config.seed_amount * 100).quantize(
        Decimal('0.0001'), rounding=ROUND_DOWN
    )

    if side == OrderSide.BUY:
        return _size_buy(
            config, reference_price, position_value, current_weight, cash_balance, investment_limit
        )
    if side == OrderSide.SELL:
        return _size_sell(config, reference_price, position_value, current_weight, holding_quantity)
    raise AlertSizingError('INVALID_ACTION', f'지원하지 않는 액션: {side}')


def _size_buy(
    config: EffectiveTradeConfig,
    price: Decimal,
    position_value: Decimal,
    current_weight: Decimal,
    cash_balance: Decimal,
    investment_limit: Optional[Decimal],
) -> SizingResult:
    buy_notional = (
        config.seed_amount * config.trade_percent / Decimal('100')
    ).quantize(MONEY_QUANT, rounding=ROUND_DOWN)

    max_value = (
        config.seed_amount * config.max_position_weight_percent / Decimal('100')
    ).quantize(MONEY_QUANT, rounding=ROUND_DOWN)
    remaining_capacity = max(ZERO, max_value - position_value)

    if remaining_capacity <= 0:
        raise AlertSizingError('MAX_WEIGHT_REACHED', '최대 포지션 비중에 도달했습니다.')

    if buy_notional > remaining_capacity:
        buy_notional = remaining_capacity

    if cash_balance <= 0:
        raise AlertSizingError('INSUFFICIENT_CASH', '매수 가능한 예수금이 없습니다.')
    if buy_notional > cash_balance:
        buy_notional = cash_balance.quantize(MONEY_QUANT, rounding=ROUND_DOWN)

    if investment_limit is not None and buy_notional > investment_limit:
        buy_notional = Decimal(str(investment_limit)).quantize(
            MONEY_QUANT, rounding=ROUND_DOWN
        )

    if buy_notional <= 0:
        raise AlertSizingError('ZERO_NOTIONAL', '매수 금액이 0입니다.')

    quantity = (buy_notional / price).quantize(QTY_QUANT, rounding=ROUND_DOWN)
    if quantity <= 0:
        raise AlertSizingError('ZERO_QUANTITY', '계산된 매수 수량이 0입니다.')

    return SizingResult(
        side=OrderSide.BUY,
        notional=buy_notional,
        quantity=quantity,
        reference_price=price,
        position_value=position_value,
        current_weight_percent=current_weight,
    )


def _size_sell(
    config: EffectiveTradeConfig,
    price: Decimal,
    position_value: Decimal,
    current_weight: Decimal,
    holding_quantity: Decimal,
) -> SizingResult:
    if not holding_quantity or holding_quantity <= 0:
        raise AlertSizingError('NO_HOLDING', '해당 종목을 보유하고 있지 않습니다.')

    if config.min_sell_holding_percent is not None:
        min_value = (
            config.seed_amount * config.min_sell_holding_percent / Decimal('100')
        )
        if position_value < min_value:
            raise AlertSizingError(
                'BELOW_MIN_HOLDING_WEIGHT',
                f'보유 비중({current_weight}%)이 최소 매도 비중 '
                f'({config.min_sell_holding_percent}%) 미만입니다.',
            )

    sell_notional = (
        config.seed_amount * config.trade_percent / Decimal('100')
    ).quantize(MONEY_QUANT, rounding=ROUND_DOWN)

    sell_qty = (sell_notional / price).quantize(QTY_QUANT, rounding=ROUND_DOWN)
    sell_qty = min(sell_qty, holding_quantity)

    if sell_qty <= 0:
        raise AlertSizingError('NOTHING_TO_SELL', '매도 가능한 수량이 없습니다.')

    actual_notional = (sell_qty * price).quantize(MONEY_QUANT, rounding=ROUND_DOWN)

    return SizingResult(
        side=OrderSide.SELL,
        notional=actual_notional,
        quantity=sell_qty,
        reference_price=price,
        position_value=position_value,
        current_weight_percent=current_weight,
    )


def split_amounts(
    total_notional: Decimal,
    total_quantity: Optional[Decimal],
    split_count: int,
) -> List[Tuple[Decimal, Optional[Decimal]]]:
    """N등분. 마지막 Leg에 잔여 보정."""
    if split_count < 1:
        raise AlertSizingError('INVALID_SPLIT', '분할 횟수는 1 이상이어야 합니다.')

    parts: List[Tuple[Decimal, Optional[Decimal]]] = []
    if total_quantity is not None:
        base_qty = (total_quantity / split_count).quantize(QTY_QUANT, rounding=ROUND_DOWN)
        allocated_qty = ZERO
        base_notional = (total_notional / split_count).quantize(MONEY_QUANT, rounding=ROUND_DOWN)
        allocated_notional = ZERO
        for i in range(split_count):
            if i == split_count - 1:
                qty = total_quantity - allocated_qty
                notional = total_notional - allocated_notional
            else:
                qty = base_qty
                notional = base_notional
                allocated_qty += qty
                allocated_notional += notional
            parts.append((notional, qty))
        return parts

    base_notional = (total_notional / split_count).quantize(MONEY_QUANT, rounding=ROUND_DOWN)
    allocated = ZERO
    for i in range(split_count):
        if i == split_count - 1:
            notional = total_notional - allocated
        else:
            notional = base_notional
            allocated += notional
        parts.append((notional, None))
    return parts
