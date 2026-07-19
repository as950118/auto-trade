"""
Alert 전략 가드: 계좌/종목/화이트리스트/쿨다운 등.
"""
from datetime import timedelta
from typing import Optional

from django.utils import timezone

from .models import (
    Account,
    AlertEvent,
    AlertEventStatus,
    AlertStrategy,
    OrderSide,
    Symbol,
)


class AlertGuardError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def ensure_strategy_active(strategy: AlertStrategy) -> None:
    if not strategy.enabled:
        raise AlertGuardError('STRATEGY_DISABLED', '전략이 비활성화되어 있습니다.')


def ensure_account_side_enabled(account: Account, side: str) -> None:
    if side == OrderSide.BUY and not account.buy_enabled:
        raise AlertGuardError('BUY_DISABLED', '계좌의 매수가 비활성화되어 있습니다.')
    if side == OrderSide.SELL and not account.sell_enabled:
        raise AlertGuardError('SELL_DISABLED', '계좌의 매도가 비활성화되어 있습니다.')


def resolve_symbol(account: Account, ticker: str) -> Symbol:
    ticker_norm = (ticker or '').strip().upper()
    if not ticker_norm:
        raise AlertGuardError('MISSING_TICKER', '티커가 없습니다.')

    # TradingView may send EXCHANGE:TICKER
    if ':' in ticker_norm:
        ticker_norm = ticker_norm.split(':')[-1]

    qs = Symbol.objects.filter(
        broker=account.broker,
        is_delisted=False,
    )
    symbol = qs.filter(ticker__iexact=ticker_norm).first()
    if symbol is None:
        # try without market suffix e.g. BTCUSDT vs BTC
        symbol = qs.filter(ticker__icontains=ticker_norm).first()
    if symbol is None:
        raise AlertGuardError(
            'SYMBOL_NOT_FOUND',
            f'종목을 찾을 수 없습니다: {ticker} (broker={account.broker.code})',
        )
    return symbol


def ensure_ticker_allowed(strategy: AlertStrategy, ticker: str) -> None:
    allowed = strategy.allowed_tickers
    if not allowed:
        return
    ticker_norm = ticker.strip().upper()
    if ':' in ticker_norm:
        ticker_norm = ticker_norm.split(':')[-1]
    normalized_allowed = [str(t).strip().upper() for t in allowed]
    if ticker_norm not in normalized_allowed and ticker.strip().upper() not in normalized_allowed:
        raise AlertGuardError('TICKER_NOT_ALLOWED', f'허용되지 않은 티커입니다: {ticker}')


def ensure_passphrase(strategy: AlertStrategy, payload_secret: Optional[str]) -> None:
    if not strategy.webhook_passphrase:
        return
    if not payload_secret or payload_secret != strategy.webhook_passphrase:
        raise AlertGuardError('INVALID_PASSPHRASE', '웹훅 패스프레이즈가 일치하지 않습니다.')


def ensure_cooldown(strategy: AlertStrategy, ticker: str, action: str) -> None:
    if not strategy.cooldown_seconds or strategy.cooldown_seconds <= 0:
        return
    since = timezone.now() - timedelta(seconds=strategy.cooldown_seconds)
    recent = AlertEvent.objects.filter(
        strategy=strategy,
        ticker__iexact=ticker,
        action=action,
        received_at__gte=since,
        status__in=[
            AlertEventStatus.ACCEPTED,
            AlertEventStatus.EXECUTING,
            AlertEventStatus.COMPLETED,
            AlertEventStatus.RECEIVED,
        ],
    ).exclude(status=AlertEventStatus.REJECTED)
    # Exclude the event we just created: caller should pass after save with id,
    # so we check count of other accepted-like events in window.
    # Simpler: any non-rejected event in window besides newest.
    if recent.count() > 1:
        raise AlertGuardError(
            'COOLDOWN',
            f'동일 티커/액션 쿨다운 중입니다 ({strategy.cooldown_seconds}초).',
        )
