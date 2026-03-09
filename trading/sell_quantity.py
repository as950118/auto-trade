"""
매도 수량 해석: 전량(MAX), 비율(PERCENT), 금액(AMOUNT), 수량(EXACT) 지원
"""
from decimal import Decimal, ROUND_DOWN
from django.core.exceptions import ValidationError

from .models import Account, Symbol, Holding


# 매도 수량 지정 방식
QUANTITY_TYPE_EXACT = 'EXACT'   # 직접 수량
QUANTITY_TYPE_MAX = 'MAX'       # 전량 (100%)
QUANTITY_TYPE_PERCENT = 'PERCENT'  # 보유량의 N% (예: 10, 50)
QUANTITY_TYPE_AMOUNT = 'AMOUNT'    # 금액 단위 (KRW/USD 등)

QUANTITY_TYPES = [
    (QUANTITY_TYPE_EXACT, '직접 수량'),
    (QUANTITY_TYPE_MAX, '전량'),
    (QUANTITY_TYPE_PERCENT, '비율(%)'),
    (QUANTITY_TYPE_AMOUNT, '금액'),
]


def resolve_sell_quantity(
    account: Account,
    symbol: Symbol,
    quantity_type: str,
    quantity_value=None,
    order_type: str = None,
    limit_price: Decimal = None,
) -> Decimal:
    """
    매도 수량을 해석하여 실제 수량(Decimal)을 반환합니다.

    - EXACT: quantity_value가 곧 수량 (quantity_value를 수량으로 사용)
    - MAX: 해당 계좌/종목 보유량 전부
    - PERCENT: 보유량의 quantity_value% (예: 50 -> 50%)
    - AMOUNT: quantity_value는 금액. limit_price 또는 보유 종목 현재가로 수량 환산

    Raises:
        ValidationError: 보유량 부족, 가격 없음 등
    """
    if quantity_type == QUANTITY_TYPE_EXACT:
        if quantity_value is None:
            raise ValidationError('직접 수량 지정 시 quantity(수량)이 필요합니다.')
        q = Decimal(str(quantity_value))
        if q <= 0:
            raise ValidationError('수량은 0보다 커야 합니다.')
        return q

    try:
        holding = Holding.objects.get(account=account, symbol=symbol)
    except Holding.DoesNotExist:
        raise ValidationError('해당 종목을 보유하고 있지 않습니다.')
    if holding.quantity <= 0:
        raise ValidationError('보유 수량이 없습니다.')

    if quantity_type == QUANTITY_TYPE_MAX:
        return holding.quantity

    if quantity_type == QUANTITY_TYPE_PERCENT:
        if quantity_value is None:
            raise ValidationError('비율(%) 지정 시 quantity_value가 필요합니다.')
        pct = Decimal(str(quantity_value))
        if pct <= 0 or pct > 100:
            raise ValidationError('비율은 0 초과 100 이하여야 합니다.')
        q = (holding.quantity * pct / 100).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        if q <= 0:
            raise ValidationError('계산된 매도 수량이 0입니다. 비율을 올리거나 보유량을 확인하세요.')
        return q

    if quantity_type == QUANTITY_TYPE_AMOUNT:
        if quantity_value is None:
            raise ValidationError('금액 지정 시 quantity_value(금액)가 필요합니다.')
        amount = Decimal(str(quantity_value))
        if amount <= 0:
            raise ValidationError('금액은 0보다 커야 합니다.')
        # 가격 결정: 지정가 주문이면 limit_price, 시장가는 보유 종목 현재가
        price = limit_price
        if price is None or price <= 0:
            price = holding.current_price
        if price is None or price <= 0:
            raise ValidationError(
                '금액 단위 매도는 기준 가격이 필요합니다. '
                '지정가 주문으로 가격을 넣거나, 보유 종목의 현재가가 조회된 상태에서 요청하세요.'
            )
        q = (amount / price).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
        if q <= 0:
            raise ValidationError('금액/가격으로 계산된 수량이 0입니다.')
        if q > holding.quantity:
            raise ValidationError(
                f'계산된 수량({q})이 보유 수량({holding.quantity})을 초과합니다.'
            )
        return q

    raise ValidationError(f'지원하지 않는 수량 지정 방식: {quantity_type}')
