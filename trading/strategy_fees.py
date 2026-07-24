"""
전략 제작자 수수료(커미션) — 향후 확장용 훅 / 설계 메모.

현재는 구현하지 않는다. 호출부에서 이 모듈의 no-op 함수를 호출해 두어,
나중에 StrategyCommission 원장·정산을 붙일 때 수정 지점을 최소화한다.

---------------------------------------------------------------------------
권장 도메인 (미구현)

  Strategy
    - fee_enabled: bool
    - fee_rate_bps: int  # 예: 50 = 0.50%
    - fee만 PUBLIC 전략에 적용하는 것을 권장

  StrategyLink
    - fee_rate_bps: nullable override (연동 시점 스냅샷 권장)
    - 본인 전략(self-link: account.user == strategy.owner)은 면제

  StrategyCommission (신규 테이블 예정)
    - strategy, strategy_link, order
    - payer_user (= link.account.user), payee_user (= strategy.owner)
    - notional, fee_amount, fee_rate_bps_snapshot
    - status: PENDING | SETTLED | WAIVED
    - created_at

정산 트리거
  - 주문 생성(PENDING)이 아니라 체결 확정(FILLED) 시점이 안전하다.
  - AlertTradeLeg.order → Order 가 FILLED 될 때 on_strategy_order_filled() 호출.

정산 흐름 (향후)
  1) FILLED 훅에서 Commission PENDING 생성
  2) 배치/관리자 화면에서 SETTLED 처리 또는 크레딧 반영
  3) Payout 은 Commission 과 분리 (출금·정산 수단은 별 도메인)

관련 호출부
  - trading.services.alert_strategy._create_plan_and_legs  (계획 생성 시점, 선택)
  - trading.tasks.check_order_status  (체결 확정 시점, 권장)
---------------------------------------------------------------------------
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .models import AlertTradePlan, Order, StrategyLink

logger = logging.getLogger(__name__)


def on_strategy_plan_created(plan: "AlertTradePlan", link: Optional["StrategyLink"] = None) -> None:
    """
    Plan/Legs 생성 직후 훅.

    수수료 산정에는 사용하지 않는 것을 권장(아직 미체결).
    통계·알림용 확장만 여기서 한다.
    """
    # TODO(strategy-fee): 필요 시 연동 사용량 카운터 등
    _ = (plan, link)
    return None


def on_strategy_order_filled(order: "Order") -> None:
    """
    전략 연동으로 생성된 주문이 FILLED 되었을 때 호출되는 훅.

    구현 시 체크리스트:
      1. order 가 AlertTradeLeg 를 통해 StrategyLink 와 연결되는지 확인
         (order.alert_trade_legs.first().plan.strategy_link)
      2. link/strategy 없으면 return (수동 주문 등)
      3. payer == payee (자기 전략) 이면 WAIVED 또는 skip
      4. PRIVATE 전략은 skip (정책에 따라)
      5. StrategyCommission PENDING 생성 (멱등: order_id unique)
    """
    # TODO(strategy-fee): StrategyCommission ledger 생성
    try:
        leg = order.alert_trade_legs.select_related(
            'plan', 'plan__strategy_link', 'plan__strategy_link__strategy'
        ).first()
        if not leg or not leg.plan.strategy_link_id:
            return
        # 향후: create_commission_for_filled_order(order, leg.plan.strategy_link)
        logger.debug(
            'strategy fee hook (noop): order=%s link=%s strategy=%s',
            order.id,
            leg.plan.strategy_link_id,
            leg.plan.strategy_link.strategy_id if leg.plan.strategy_link_id else None,
        )
    except Exception:
        # 수수료 훅 실패가 주문 처리를 막지 않도록 한다.
        logger.exception('strategy fee hook failed for order=%s', getattr(order, 'id', None))
