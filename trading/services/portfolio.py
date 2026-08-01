"""
포트폴리오 비중 미러링 리밸런싱.

Portfolio.holdings(목표 비중 바스켓)와 PortfolioLink(구독 계좌, 고정 시드)를 기준으로
각 종목의 목표 금액과 현재 보유 금액 차이만큼 즉시 Order(PENDING)를 생성한다.
생성된 Order는 기존 process_orders 스케줄러(scheduler.py)가 그대로 집행한다.

tasks.run_target_allocation_plans()의 target_value/deficit 계산 패턴을
단일 종목이 아닌 바스켓 전체로 확장한 버전.
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_DOWN
from typing import Iterable, Optional

from .. import alert_sizing
from ..models import (
    Holding,
    Order,
    OrderStatus,
    Portfolio,
    PortfolioLink,
)

logger = logging.getLogger(__name__)

ZERO = Decimal('0')
MONEY_QUANT = alert_sizing.MONEY_QUANT
QTY_QUANT = alert_sizing.QTY_QUANT
MIN_ORDER_VALUE = MONEY_QUANT


def _resolve_reference_price(account, symbol) -> Optional[Decimal]:
    """계좌 자신의 보유 시세 → 동일 종목을 보유 중인 다른 계좌의 최신 시세 순으로 기준가를 찾는다."""
    holding = Holding.objects.filter(account=account, symbol=symbol).first()
    if holding and holding.current_price and holding.current_price > 0:
        return Decimal(str(holding.current_price))

    fallback = (
        Holding.objects.filter(symbol=symbol, current_price__gt=0)
        .order_by('-updated_at')
        .first()
    )
    if fallback:
        return Decimal(str(fallback.current_price))
    return None


def rebalance_link(link: PortfolioLink) -> int:
    """단일 구독(계좌)에 대해 포트폴리오 전체 바스켓을 목표 비중까지 리밸런싱한다."""
    if not link.enabled or not link.portfolio.enabled:
        return 0

    portfolio = link.portfolio
    account = link.account
    created_count = 0

    for holding in portfolio.holdings.select_related('symbol').all():
        symbol = holding.symbol
        try:
            target_value = (
                link.seed_amount * holding.target_weight_percent / Decimal('100')
            ).quantize(MONEY_QUANT, rounding=ROUND_DOWN)

            current_holding = Holding.objects.filter(account=account, symbol=symbol).first()
            current_value = current_holding.total_value if current_holding else ZERO

            deficit = target_value - current_value
            if abs(deficit) < MIN_ORDER_VALUE:
                continue

            price = _resolve_reference_price(account, symbol)
            if not price or price <= 0:
                logger.warning(
                    'portfolio rebalance: no reference price for symbol=%s account=%s, skip',
                    symbol.ticker, account.id,
                )
                continue

            if deficit > 0:
                side = 'BUY'
                if not account.buy_enabled:
                    logger.info('portfolio rebalance: buy disabled for account=%s, skip', account.id)
                    continue
                quantity = (deficit / price).quantize(QTY_QUANT, rounding=ROUND_DOWN)
            else:
                side = 'SELL'
                if not account.sell_enabled:
                    logger.info('portfolio rebalance: sell disabled for account=%s, skip', account.id)
                    continue
                if not current_holding or current_holding.quantity <= 0:
                    continue
                quantity = (abs(deficit) / price).quantize(QTY_QUANT, rounding=ROUND_DOWN)
                quantity = min(quantity, current_holding.quantity)

            if quantity <= 0:
                continue

            order_price = price if portfolio.order_type == 'LIMIT' else None
            Order.objects.create(
                account=account,
                symbol=symbol,
                side=side,
                order_type=portfolio.order_type,
                quantity=quantity,
                price=order_price,
                status=OrderStatus.PENDING,
                portfolio_link=link,
            )
            created_count += 1
            logger.info(
                'portfolio rebalance: %s order created (portfolio=%s account=%s symbol=%s qty=%s)',
                side, portfolio.id, account.id, symbol.ticker, quantity,
            )
        except Exception:
            # TODO(portfolio-fee): 구독 과금 도입 시 strategy_fees.py 패턴처럼 체결 훅에서 정산
            logger.exception(
                'portfolio rebalance failed for portfolio=%s account=%s symbol=%s',
                portfolio.id, account.id, symbol.ticker,
            )

    return created_count


def rebalance_portfolio(portfolio: Portfolio, link_ids: Optional[Iterable[int]] = None) -> int:
    """포트폴리오에 연동된 활성 계좌 전체(또는 지정된 link_ids)를 리밸런싱한다."""
    links = portfolio.links.filter(enabled=True).select_related('account')
    if link_ids is not None:
        links = links.filter(id__in=list(link_ids))

    created_count = 0
    for link in links:
        created_count += rebalance_link(link)
    return created_count
