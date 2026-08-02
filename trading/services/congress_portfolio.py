"""
의회의원(CongressMember)의 공시 거래(CongressTrade) 누적치로부터 비중 미러링 Portfolio를 생성/동기화한다.

공시는 "거래" 내역이지 전체 보유비중이 아니므로, 여기서 만들어지는 포트폴리오는
공개된 매수/매도 공시를 누적 추정한 근사치이며 실제 포트폴리오의 1:1 복제가 아니다.

Portfolio 자체의 CRUD/리밸런싱 계약(단일 통화, 비중 합 <= 100)은 services/portfolio.py와
동일하게 지키고, 리밸런싱 엔진(rebalance_portfolio)은 그대로 재사용한다.
"""
from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal
from typing import Dict

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from ..models import (
    Currency,
    CongressMember,
    OrderType,
    Portfolio,
    PortfolioHolding,
    PortfolioVisibility,
)
from .portfolio import rebalance_portfolio

logger = logging.getLogger(__name__)

WEIGHT_QUANT = Decimal("0.01")
MAX_HOLDINGS_DEFAULT = 20


def get_or_create_system_owner() -> User:
    """
    의회 미러링 포트폴리오를 소유할 시스템 계정.
    PortfolioSerializer.validate_visibility가 PUBLIC 포트폴리오는 owner.is_staff를 요구하므로
    반드시 is_staff=True로 생성한다.
    """
    username = getattr(settings, "CONGRESS_PORTFOLIO_OWNER_USERNAME", "congress_bot")
    user, created = User.objects.get_or_create(
        username=username,
        defaults={"is_staff": True, "is_active": True},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    elif not user.is_staff:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    return user


def _compute_net_positions(member: CongressMember, max_holdings: int) -> Dict[int, Decimal]:
    """
    member.trades를 거래일 순으로 누적해 종목별 net 매수금액(중간값 기준)을 계산한다.
    BUY는 +, SELL은 -. net <= 0인 종목은 제외. 상위 max_holdings개만 남긴다.
    USD 통화 심볼만 대상으로 한다(Portfolio 단일통화 제약).
    """
    trades = (
        member.trades
        .filter(symbol__isnull=False, symbol__currency=Currency.USD)
        .select_related("symbol")
        .order_by("transaction_date")
    )

    net_value: Dict[int, Decimal] = {}
    for trade in trades:
        midpoint = (trade.amount_min + trade.amount_max) / Decimal("2")
        delta = midpoint if trade.transaction_type == "BUY" else -midpoint
        net_value[trade.symbol_id] = net_value.get(trade.symbol_id, Decimal("0")) + delta

    positive = {sid: v for sid, v in net_value.items() if v > 0}
    top = dict(sorted(positive.items(), key=lambda kv: kv[1], reverse=True)[:max_holdings])
    return top


def _normalize_weights(net_positions: Dict[int, Decimal]) -> Dict[int, Decimal]:
    """net_positions를 합계 100% 이하로 정규화. 반올림 오차는 마지막 항목에서 보정."""
    total = sum(net_positions.values())
    if total <= 0:
        return {}

    weights = {}
    items = list(net_positions.items())
    running_sum = Decimal("0")
    for i, (symbol_id, value) in enumerate(items):
        if i == len(items) - 1:
            weight = (Decimal("100") - running_sum).quantize(WEIGHT_QUANT, rounding=ROUND_DOWN)
        else:
            weight = (value / total * Decimal("100")).quantize(WEIGHT_QUANT, rounding=ROUND_DOWN)
            running_sum += weight
        if weight > 0:
            weights[symbol_id] = weight
    return weights


def sync_member_portfolio(member: CongressMember, max_holdings: int = MAX_HOLDINGS_DEFAULT) -> Portfolio:
    """member의 공시 거래 누적치로 Portfolio(없으면 생성)의 holdings를 전량 교체하고 리밸런싱한다."""
    from ..models import Symbol

    if member.portfolio_id:
        portfolio = member.portfolio
    else:
        owner = get_or_create_system_owner()
        portfolio = Portfolio.objects.create(
            owner=owner,
            title=f"{member.name} 포트폴리오",
            description=(
                "공개된 미 하원의원 거래공시(STOCK Act PTR)를 기반으로 자동 생성된 포트폴리오입니다. "
                "실제 전체 보유비중이 아닌, 공시된 매수/매도 내역의 누적 추정치입니다."
            ),
            visibility=PortfolioVisibility.PUBLIC,
            order_type=OrderType.MARKET,
        )
        member.portfolio = portfolio
        member.save(update_fields=["portfolio"])

    net_positions = _compute_net_positions(member, max_holdings)
    weights = _normalize_weights(net_positions)

    symbols_by_id = {s.id: s for s in Symbol.objects.filter(id__in=weights.keys())}

    with transaction.atomic():
        portfolio.holdings.all().delete()
        PortfolioHolding.objects.bulk_create([
            PortfolioHolding(
                portfolio=portfolio,
                symbol=symbols_by_id[symbol_id],
                target_weight_percent=weight,
            )
            for symbol_id, weight in weights.items()
        ])

    rebalance_portfolio(portfolio)

    member.last_synced_at = timezone.now()
    member.save(update_fields=["last_synced_at"])

    portfolio.refresh_from_db()
    return portfolio


def sync_all_portfolios() -> int:
    """거래 내역이 있는 모든 CongressMember의 포트폴리오를 동기화한다. 반환값: 동기화된 의원 수."""
    count = 0
    for member in CongressMember.objects.filter(trades__isnull=False).distinct():
        try:
            sync_member_portfolio(member)
            count += 1
        except Exception:
            logger.exception("congress portfolio sync failed for member=%s", member.id)
    return count
