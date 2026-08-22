"""
TASK-0007: alert_sizing 파라미터 백테스트 하네스 (PRD-0005).

과거 AlertTradePlan 이력(StrategyLink 단위)을 시간순으로 재생하면서, 지정한 초기 가상
잔고에서 출발해 후보 EffectiveTradeConfig로 재사이징한 결과("sim" 경로)를 실제로 계획된
사이징 결과("actual" 경로, Plan에 저장된 total_notional/total_quantity/reference_price
기준)와 비교한다.

한계 (PRD-0005 §3 Non-Goals / §6 Assumptions 참고, 회귀가 아니라 의도된 스코프):
- Account/Holding은 "현재 상태"만 저장하고 과거 시점 스냅샷이 없다. 두 경로 모두 동일한
  초기 가상 잔고에서 출발해 자체적으로 잔고를 시뮬레이션하는 "상대 비교" 도구이지, 실제
  계좌를 정확히 재현하는 것이 아니다(같은 계좌의 다른 자금 흐름은 반영되지 않음).
- "actual" 경로는 체결(Order) 결과가 아니라 Plan에 저장된 계획 수량/금액을 쓴다(부분체결/
  거부는 반영하지 않음).
- 가격은 Plan 생성 시점에 저장된 reference_price를 그대로 재사용하고, 최종 평가
  (mark-to-market) 시점 가격만 FinanceDataReader/pyupbit로 온디맨드 조회한다. 조회
  실패/결측이어도 백테스트 자체는 멈추지 않고 해당 종목의 최종 평가액을 "알 수 없음"으로
  표시한다.
- 하나의 StrategyLink가 여러 티커의 알림을 받을 수 있으므로(Strategy.allowed_tickers),
  보유수량은 종목별로 추적하되 현금은 계좌 단위로 공유되는 라이브 사이징 방식과 동일하게
  단일 풀로 다룬다.
"""
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional

from ..alert_sizing import AlertSizingError, EffectiveTradeConfig, config_from_link, size_trade
from ..models import AlertTradePlan, OrderSide, StrategyLink

logger = logging.getLogger(__name__)

MONEY_QUANT = Decimal('0.01')
ZERO = Decimal('0')


@dataclass
class ConfigOverride:
    """None인 필드는 StrategyLink의 현재 effective 설정을 그대로 쓴다."""
    trade_percent: Optional[Decimal] = None
    max_position_weight_percent: Optional[Decimal] = None
    split_count: Optional[int] = None

    def apply(self, base: EffectiveTradeConfig) -> EffectiveTradeConfig:
        return replace(
            base,
            trade_percent=self.trade_percent if self.trade_percent is not None else base.trade_percent,
            max_position_weight_percent=(
                self.max_position_weight_percent
                if self.max_position_weight_percent is not None
                else base.max_position_weight_percent
            ),
            split_count=self.split_count if self.split_count is not None else base.split_count,
        )


@dataclass
class PlanReplay:
    plan_id: int
    created_at: datetime
    symbol_ticker: str
    side: str
    reference_price: Optional[Decimal]
    actual_notional: Decimal
    actual_quantity: Optional[Decimal]
    sim_notional: Optional[Decimal] = None
    sim_quantity: Optional[Decimal] = None
    sim_error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            'plan_id': self.plan_id,
            'created_at': self.created_at.isoformat(),
            'symbol_ticker': self.symbol_ticker,
            'side': self.side,
            'reference_price': str(self.reference_price) if self.reference_price is not None else None,
            'actual_notional': str(self.actual_notional),
            'actual_quantity': str(self.actual_quantity) if self.actual_quantity is not None else None,
            'sim_notional': str(self.sim_notional) if self.sim_notional is not None else None,
            'sim_quantity': str(self.sim_quantity) if self.sim_quantity is not None else None,
            'sim_error': self.sim_error,
        }


@dataclass
class BacktestReport:
    strategy_link_id: int
    initial_cash: Decimal
    replays: List[PlanReplay]
    actual_cash: Decimal
    sim_cash: Decimal
    actual_holdings: Dict[str, Decimal]
    sim_holdings: Dict[str, Decimal]
    mark_prices: Dict[str, Optional[Decimal]] = field(default_factory=dict)

    def _mark_value(self, cash: Decimal, holdings: Dict[str, Decimal]) -> Decimal:
        total = cash
        for ticker, qty in holdings.items():
            price = self.mark_prices.get(ticker)
            if price:
                total += (qty * price).quantize(MONEY_QUANT, rounding=ROUND_DOWN)
        return total

    @property
    def actual_final_value(self) -> Decimal:
        return self._mark_value(self.actual_cash, self.actual_holdings)

    @property
    def sim_final_value(self) -> Decimal:
        return self._mark_value(self.sim_cash, self.sim_holdings)

    def as_dict(self) -> dict:
        return {
            'strategy_link_id': self.strategy_link_id,
            'initial_cash': str(self.initial_cash),
            'plan_count': len(self.replays),
            'replays': [r.as_dict() for r in self.replays],
            'actual_cash': str(self.actual_cash),
            'sim_cash': str(self.sim_cash),
            'actual_holdings': {t: str(q) for t, q in self.actual_holdings.items()},
            'sim_holdings': {t: str(q) for t, q in self.sim_holdings.items()},
            'mark_prices': {t: (str(p) if p is not None else None) for t, p in self.mark_prices.items()},
            'actual_final_value': str(self.actual_final_value),
            'sim_final_value': str(self.sim_final_value),
        }


def fetch_mark_price(symbol, as_of: datetime) -> Optional[Decimal]:
    """as_of 이전(당일 포함) 가장 최근 종가를 조회한다. 실패/결측 시 None."""
    try:
        if symbol.is_crypto:
            return _fetch_upbit_price(symbol.ticker, as_of)
        return _fetch_fdr_price(symbol.ticker, as_of)
    except Exception:
        logger.warning('mark price 조회 실패: symbol=%s as_of=%s', symbol.ticker, as_of, exc_info=True)
        return None


def _fetch_fdr_price(ticker: str, as_of: datetime) -> Optional[Decimal]:
    try:
        import FinanceDataReader as fdr
    except ImportError:
        return None
    start = (as_of - timedelta(days=10)).strftime('%Y-%m-%d')
    end = as_of.strftime('%Y-%m-%d')
    df = fdr.DataReader(ticker, start, end)
    if df is None or df.empty:
        return None
    return Decimal(str(df['Close'].iloc[-1]))


def _fetch_upbit_price(ticker: str, as_of: datetime) -> Optional[Decimal]:
    import pyupbit
    df = pyupbit.get_ohlcv(ticker, count=10, to=as_of.strftime('%Y%m%d 090000'))
    if df is None or df.empty:
        return None
    return Decimal(str(df['close'].iloc[-1]))


def run_alert_sizing_backtest(
    strategy_link: StrategyLink,
    start: datetime,
    end: datetime,
    initial_cash: Decimal,
    override: Optional[ConfigOverride] = None,
    fetch_mark_prices: bool = True,
) -> BacktestReport:
    """StrategyLink의 과거 AlertTradePlan 이력을 재생해 사이징 파라미터 변경 효과를 비교한다."""
    override = override or ConfigOverride()
    base_config = config_from_link(strategy_link)
    candidate_config = override.apply(base_config)

    plans = list(
        AlertTradePlan.objects.filter(
            strategy_link=strategy_link,
            created_at__gte=start,
            created_at__lte=end,
        ).select_related('symbol').order_by('created_at')
    )

    sim_cash = initial_cash
    actual_cash = initial_cash
    sim_holdings: Dict[str, Decimal] = {}
    actual_holdings: Dict[str, Decimal] = {}
    replays: List[PlanReplay] = []

    for plan in plans:
        ticker = plan.symbol.ticker
        price = plan.reference_price
        replay = PlanReplay(
            plan_id=plan.id,
            created_at=plan.created_at,
            symbol_ticker=ticker,
            side=plan.side,
            reference_price=price,
            actual_notional=plan.total_notional,
            actual_quantity=plan.total_quantity,
        )

        actual_qty_delta = plan.total_quantity or ZERO
        if plan.side == OrderSide.BUY:
            actual_cash -= plan.total_notional
            actual_holdings[ticker] = actual_holdings.get(ticker, ZERO) + actual_qty_delta
        else:
            actual_cash += plan.total_notional
            actual_holdings[ticker] = actual_holdings.get(ticker, ZERO) - actual_qty_delta

        if not price or price <= 0:
            replay.sim_error = 'NO_REFERENCE_PRICE'
            replays.append(replay)
            continue

        sim_qty = sim_holdings.get(ticker, ZERO)
        sim_position_value = (sim_qty * price).quantize(MONEY_QUANT, rounding=ROUND_DOWN)
        try:
            sim_result = size_trade(
                candidate_config,
                plan.side,
                reference_price=price,
                position_value=sim_position_value,
                holding_quantity=sim_qty,
                cash_balance=sim_cash,
            )
        except AlertSizingError as exc:
            replay.sim_error = f'{exc.code}: {exc.message}'
            replays.append(replay)
            continue

        replay.sim_notional = sim_result.notional
        replay.sim_quantity = sim_result.quantity
        sim_qty_delta = sim_result.quantity or ZERO
        if plan.side == OrderSide.BUY:
            sim_cash -= sim_result.notional
            sim_holdings[ticker] = sim_qty + sim_qty_delta
        else:
            sim_cash += sim_result.notional
            sim_holdings[ticker] = sim_qty - sim_qty_delta
        replays.append(replay)

    report = BacktestReport(
        strategy_link_id=strategy_link.id,
        initial_cash=initial_cash,
        replays=replays,
        actual_cash=actual_cash,
        sim_cash=sim_cash,
        actual_holdings=actual_holdings,
        sim_holdings=sim_holdings,
    )

    if fetch_mark_prices:
        symbols_by_ticker = {plan.symbol.ticker: plan.symbol for plan in plans}
        for ticker, symbol in symbols_by_ticker.items():
            report.mark_prices[ticker] = fetch_mark_price(symbol, end)

    return report
