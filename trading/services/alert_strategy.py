"""
TradingView Alert → Event 저장 → 검증/사이징 → TradePlan/Legs 생성.
"""
import hashlib
import json
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..alert_guards import (
    AlertGuardError,
    ensure_account_side_enabled,
    ensure_cooldown,
    ensure_passphrase,
    ensure_strategy_active,
    ensure_ticker_allowed,
    resolve_symbol,
)
from ..alert_sizing import AlertSizingError, size_alert_trade, split_amounts
from ..models import (
    AlertEvent,
    AlertEventStatus,
    AlertStrategy,
    AlertTradeLeg,
    AlertTradePlan,
    AlertTradeStatus,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)

logger = logging.getLogger(__name__)


def parse_tradingview_payload(payload: Dict[str, Any]) -> Tuple[str, str, Optional[str], Optional[str], Optional[Decimal]]:
    """
    Returns: ticker, action, passphrase, idempotency_key, price
    """
    ticker = (
        payload.get('ticker')
        or payload.get('symbol')
        or payload.get('Ticker')
        or ''
    )
    action_raw = (
        payload.get('action')
        or payload.get('side')
        or payload.get('order')
        or payload.get('Action')
        or ''
    )
    action = str(action_raw).strip().upper()
    if action in ('LONG', 'BUY', 'B'):
        action = OrderSide.BUY
    elif action in ('SHORT', 'SELL', 'S', 'EXIT', 'CLOSE'):
        action = OrderSide.SELL
    else:
        action = action if action in (OrderSide.BUY, OrderSide.SELL) else ''

    passphrase = payload.get('secret') or payload.get('passphrase')
    idempotency_key = (
        payload.get('alert_id')
        or payload.get('idempotency_key')
        or payload.get('id')
    )
    if idempotency_key is not None:
        idempotency_key = str(idempotency_key)[:255]

    price = None
    raw_price = payload.get('price') or payload.get('close') or payload.get('last')
    if raw_price is not None and str(raw_price).strip() != '':
        try:
            price = Decimal(str(raw_price))
            if price <= 0:
                price = None
        except Exception:
            price = None

    return str(ticker).strip(), action, passphrase, idempotency_key, price


def _fallback_idempotency_key(strategy_id: int, payload: Dict[str, Any]) -> str:
    """alert_id가 없을 때 payload 해시 + 분 단위 버킷으로 약한 멱등 키 생성."""
    minute_bucket = timezone.now().strftime('%Y%m%d%H%M')
    raw = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(f'{strategy_id}:{minute_bucket}:{raw}'.encode()).hexdigest()[:32]
    return f'auto-{digest}'


class AlertStrategyService:
    """Webhook 처리 및 Plan/Leg 생성"""

    @classmethod
    def handle_webhook(
        cls,
        webhook_token: str,
        payload: Dict[str, Any],
    ) -> AlertEvent:
        try:
            strategy = AlertStrategy.objects.select_related(
                'account', 'account__broker'
            ).get(webhook_token=webhook_token)
        except AlertStrategy.DoesNotExist:
            raise AlertGuardError('STRATEGY_NOT_FOUND', '전략을 찾을 수 없습니다.')

        ticker, action, passphrase, idempotency_key, price = parse_tradingview_payload(payload)
        if not idempotency_key:
            idempotency_key = _fallback_idempotency_key(strategy.id, payload)

        # Idempotent short-circuit
        existing = AlertEvent.objects.filter(
            strategy=strategy,
            idempotency_key=idempotency_key,
        ).first()
        if existing:
            logger.info('Duplicate alert ignored: strategy=%s key=%s', strategy.id, idempotency_key)
            return existing

        try:
            with transaction.atomic():
                event = AlertEvent.objects.create(
                    strategy=strategy,
                    raw_payload=payload,
                    ticker=ticker,
                    action=action or '',
                    idempotency_key=idempotency_key,
                    status=AlertEventStatus.RECEIVED,
                )
        except IntegrityError:
            existing = AlertEvent.objects.filter(
                strategy=strategy,
                idempotency_key=idempotency_key,
            ).first()
            if existing:
                return existing
            raise

        try:
            cls._process_event(event, strategy, passphrase, price)
        except (AlertGuardError, AlertSizingError) as exc:
            event.status = AlertEventStatus.REJECTED
            event.reject_reason = f'[{exc.code}] {exc.message}'
            event.save(update_fields=['status', 'reject_reason', 'updated_at'])
            logger.warning('Alert rejected: %s', event.reject_reason)
        except Exception as exc:
            event.status = AlertEventStatus.FAILED
            event.reject_reason = str(exc)
            event.save(update_fields=['status', 'reject_reason', 'updated_at'])
            logger.exception('Alert processing failed: event=%s', event.id)

        return event

    @classmethod
    def _resolve_reference_price(cls, account, symbol, fallback_price: Optional[Decimal]) -> Optional[Decimal]:
        if fallback_price is not None and fallback_price > 0:
            return fallback_price
        try:
            from ..clients import get_broker_client
            client = get_broker_client(account)
            if hasattr(client, 'get_crypto_price'):
                px = client.get_crypto_price(symbol.ticker)
                if px and px > 0:
                    return Decimal(str(px))
        except Exception as exc:
            logger.debug('Broker price lookup failed: %s', exc)
        return None

    @classmethod
    def _process_event(
        cls,
        event: AlertEvent,
        strategy: AlertStrategy,
        passphrase: Optional[str],
        payload_price: Optional[Decimal] = None,
    ) -> None:
        ensure_strategy_active(strategy)
        ensure_passphrase(strategy, passphrase)

        if event.action not in (OrderSide.BUY, OrderSide.SELL):
            raise AlertGuardError('INVALID_ACTION', f'유효하지 않은 action: {event.action}')

        account = strategy.account
        ensure_account_side_enabled(account, event.action)
        ensure_ticker_allowed(strategy, event.ticker)
        symbol = resolve_symbol(account, event.ticker)
        ensure_cooldown(strategy, symbol.ticker, event.action)

        reference_price = cls._resolve_reference_price(account, symbol, payload_price)

        sizing = size_alert_trade(
            strategy=strategy,
            account=account,
            symbol=symbol,
            side=event.action,
            reference_price=reference_price,
        )

        cls._create_plan_and_legs(
            event=event,
            strategy=strategy,
            account=account,
            symbol=symbol,
            sizing=sizing,
        )

        event.ticker = symbol.ticker
        event.status = AlertEventStatus.ACCEPTED
        event.save(update_fields=['ticker', 'status', 'updated_at'])

    @classmethod
    def _create_plan_and_legs(cls, event, strategy, account, symbol, sizing) -> AlertTradePlan:
        split_count = max(1, int(strategy.split_count or 1))
        interval = int(strategy.split_interval_seconds or 0)
        parts = split_amounts(sizing.notional, sizing.quantity, split_count)
        now = timezone.now()

        plan = AlertTradePlan.objects.create(
            event=event,
            account=account,
            symbol=symbol,
            side=sizing.side,
            total_notional=sizing.notional,
            total_quantity=sizing.quantity,
            reference_price=sizing.reference_price,
            split_count=split_count,
            split_interval_seconds=interval,
            order_type=strategy.order_type or OrderType.MARKET,
            status=AlertTradeStatus.PENDING,
        )

        for i, (notional, qty) in enumerate(parts):
            AlertTradeLeg.objects.create(
                plan=plan,
                seq=i,
                scheduled_at=now + timedelta(seconds=i * interval),
                notional=notional,
                quantity=qty,
                status=AlertTradeStatus.SCHEDULED,
            )

        event.status = AlertEventStatus.EXECUTING
        event.save(update_fields=['status', 'updated_at'])
        plan.status = AlertTradeStatus.PENDING
        plan.save(update_fields=['status', 'updated_at'])
        return plan


def process_due_alert_trade_legs(now=None) -> int:
    """예약 시각이 된 Leg에 대해 PENDING Order를 생성한다."""
    now = now or timezone.now()
    due_legs = (
        AlertTradeLeg.objects.select_related(
            'plan', 'plan__account', 'plan__symbol', 'plan__event'
        )
        .filter(
            status=AlertTradeStatus.SCHEDULED,
            scheduled_at__lte=now,
        )
        .order_by('scheduled_at', 'seq')
    )

    created = 0
    for leg in due_legs:
        try:
            with transaction.atomic():
                locked = AlertTradeLeg.objects.select_for_update().get(pk=leg.pk)
                if locked.status != AlertTradeStatus.SCHEDULED:
                    continue
                _create_order_for_leg(locked)
                created += 1
        except Exception as exc:
            logger.exception('Failed to process alert leg %s: %s', leg.id, exc)
            leg.status = AlertTradeStatus.FAILED
            leg.error_message = str(exc)
            leg.save(update_fields=['status', 'error_message', 'updated_at'])
            _refresh_plan_status(leg.plan_id)

    return created


def _create_order_for_leg(leg: AlertTradeLeg) -> Order:
    plan = leg.plan
    account = plan.account
    symbol = plan.symbol

    if plan.side == OrderSide.BUY:
        from ..buy_quantity import resolve_buy_quantity, QUANTITY_TYPE_AMOUNT

        quantity = resolve_buy_quantity(
            account=account,
            symbol=symbol,
            quantity_type=QUANTITY_TYPE_AMOUNT,
            quantity_value=leg.notional,
            order_type=plan.order_type,
            reference_price=plan.reference_price,
        )
    else:
        quantity = leg.quantity
        if quantity is None or quantity <= 0:
            from ..sell_quantity import resolve_sell_quantity, QUANTITY_TYPE_AMOUNT

            quantity = resolve_sell_quantity(
                account=account,
                symbol=symbol,
                quantity_type=QUANTITY_TYPE_AMOUNT,
                quantity_value=leg.notional,
            )

    if quantity <= 0:
        leg.status = AlertTradeStatus.SKIPPED
        leg.error_message = '수량이 0입니다.'
        leg.save(update_fields=['status', 'error_message', 'updated_at'])
        _refresh_plan_status(plan.id)
        return None

    order = Order.objects.create(
        account=account,
        symbol=symbol,
        side=plan.side,
        order_type=plan.order_type,
        quantity=quantity,
        price=None if plan.order_type == OrderType.MARKET else plan.reference_price,
        status=OrderStatus.PENDING,
    )

    leg.order = order
    leg.status = AlertTradeStatus.ORDERED
    leg.save(update_fields=['order', 'status', 'updated_at'])

    plan.legs_done = plan.legs.filter(
        status__in=[AlertTradeStatus.ORDERED, AlertTradeStatus.COMPLETED, AlertTradeStatus.SKIPPED]
    ).count()
    plan.save(update_fields=['legs_done', 'updated_at'])
    _refresh_plan_status(plan.id)
    return order


def _refresh_plan_status(plan_id: int) -> None:
    try:
        plan = AlertTradePlan.objects.select_related('event').get(pk=plan_id)
    except AlertTradePlan.DoesNotExist:
        return

    legs = list(plan.legs.all())
    if not legs:
        return

    statuses = {leg.status for leg in legs}
    if all(s in (AlertTradeStatus.ORDERED, AlertTradeStatus.COMPLETED, AlertTradeStatus.SKIPPED) for s in statuses):
        if AlertTradeStatus.FAILED in statuses:
            plan.status = AlertTradeStatus.FAILED
            plan.event.status = AlertEventStatus.FAILED
        else:
            plan.status = AlertTradeStatus.COMPLETED
            plan.event.status = AlertEventStatus.COMPLETED
        plan.save(update_fields=['status', 'updated_at'])
        plan.event.save(update_fields=['status', 'updated_at'])
    elif AlertTradeStatus.FAILED in statuses and AlertTradeStatus.SCHEDULED not in statuses:
        plan.status = AlertTradeStatus.FAILED
        plan.event.status = AlertEventStatus.FAILED
        plan.save(update_fields=['status', 'updated_at'])
        plan.event.save(update_fields=['status', 'updated_at'])
    else:
        plan.status = AlertTradeStatus.PENDING
        plan.event.status = AlertEventStatus.EXECUTING
        plan.save(update_fields=['status', 'updated_at'])
        plan.event.save(update_fields=['status', 'updated_at'])
