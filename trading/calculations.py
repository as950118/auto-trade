"""
ORM에 의존하지 않는 순수 계산 함수 모음 (TASK-0009).

profit_calculator.py(실현 손익 FIFO)와 services/portfolio.py(리밸런싱 수량)에 인라인으로
있던 계산식을 값(Decimal)만 받는 함수로 뽑았다. 호출부가 ORM 조회를 끝낸 뒤 값만 넘기므로
백테스트나 리포트 계산에서 DB 없이 같은 로직을 재사용할 수 있다
(TASK-0007 alert_sizing.size_trade()와 같은 분리 패턴).
"""
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_DOWN
from typing import Iterable, List, Tuple

ZERO = Decimal('0')
HUNDRED = Decimal('100')


@dataclass(frozen=True)
class Lot:
    """아직 소진되지 않은 매수 로트."""
    quantity: Decimal
    price: Decimal


def consume_fifo(lots: Iterable[Lot], quantity: Decimal) -> Tuple[Decimal, List[Lot]]:
    """
    lots를 앞에서부터(FIFO) quantity만큼 소진한다.

    Returns:
        (소진한 수량의 매수 원가 합계, 소진 후 남은 로트 목록 — 입력 순서 유지)
        보유 로트보다 많이 소진하려 하면 초과분은 원가 0으로 취급한다(기존 동작).
    """
    remaining_to_consume = quantity
    cost = ZERO
    remaining: List[Lot] = []
    for lot in lots:
        if remaining_to_consume > 0 and lot.quantity > 0:
            consumed = min(lot.quantity, remaining_to_consume)
            cost += consumed * lot.price
            remaining_to_consume -= consumed
            lot = replace(lot, quantity=lot.quantity - consumed)
        remaining.append(lot)
    return cost, remaining


def fifo_realized_profit(
    lots: Iterable[Lot],
    prior_sell_quantities: Iterable[Decimal],
    sell_quantity: Decimal,
    sell_price: Decimal,
) -> Decimal:
    """앞선 매도들이 소진한 로트를 뺀 뒤, 이번 매도의 FIFO 실현 손익(매도 금액 - 매수 원가)을 계산한다."""
    remaining = list(lots)
    for prior_quantity in prior_sell_quantities:
        _, remaining = consume_fifo(remaining, prior_quantity)
    cost, _ = consume_fifo(remaining, sell_quantity)
    return sell_quantity * sell_price - cost


def profit_rate_percent(profit: Decimal, base_amount: Decimal) -> Decimal:
    """손익률(%) = profit / base_amount * 100. base_amount가 0 이하이면 0."""
    if base_amount <= 0:
        return ZERO
    return (profit / base_amount) * HUNDRED


def target_value_for_weight(seed_amount: Decimal, weight_percent: Decimal, money_quant: Decimal) -> Decimal:
    """시드 금액 중 목표 비중(%)에 해당하는 금액. money_quant 단위로 내림."""
    return (seed_amount * weight_percent / HUNDRED).quantize(money_quant, rounding=ROUND_DOWN)


def rebalance_quantity(
    deficit: Decimal,
    price: Decimal,
    held_quantity: Decimal,
    qty_quant: Decimal,
) -> Decimal:
    """
    목표 금액 대비 부족분(deficit, 음수면 초과분)을 메우는 주문 수량.

    deficit > 0이면 매수 수량, deficit < 0이면 매도 수량이다(보유 수량을 넘지 않음,
    보유가 없으면 0). qty_quant 단위로 내림한다.
    """
    if deficit > 0:
        return (deficit / price).quantize(qty_quant, rounding=ROUND_DOWN)
    if held_quantity <= 0:
        return ZERO
    quantity = (abs(deficit) / price).quantize(qty_quant, rounding=ROUND_DOWN)
    return min(quantity, held_quantity)
