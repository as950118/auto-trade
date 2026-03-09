"""
매수 수량 해석: 전량(MAX), 비율(PERCENT), 금액(AMOUNT), 수량(EXACT) 지원
"""
from decimal import Decimal, ROUND_DOWN
from django.core.exceptions import ValidationError

from .models import Account, Symbol

# 매수 수량 지정 방식 (sell_quantity와 동일한 상수 사용)
QUANTITY_TYPE_EXACT = 'EXACT'   # 직접 수량
QUANTITY_TYPE_MAX = 'MAX'       # 예수금 전액(100%)
QUANTITY_TYPE_PERCENT = 'PERCENT'  # 예수금의 N% (예: 10, 50)
QUANTITY_TYPE_AMOUNT = 'AMOUNT'    # 금액 단위 (KRW/USD 등)


def _get_cash_balance(account: Account, symbol: Symbol) -> Decimal:
    """종목 통화에 맞는 예수금 반환"""
    currency = symbol.currency
    if currency == 'KRW':
        return account.cash_balance_krw or Decimal('0')
    if currency in ('USD', 'USDT'):
        return account.cash_balance_usd or Decimal('0')
    # 기타 통화는 원화 기준으로 처리 (필요 시 확장)
    return account.cash_balance_krw or Decimal('0')


def resolve_buy_quantity(
    account: Account,
    symbol: Symbol,
    quantity_type: str,
    quantity_value=None,
    order_type: str = None,
    limit_price: Decimal = None,
    reference_price: Decimal = None,
) -> Decimal:
    """
    매수 수량을 해석하여 실제 수량(Decimal)을 반환합니다.

    - EXACT: quantity_value가 곧 수량
    - MAX: 예수금 전액으로 매수 가능한 수량 (기준가 필요)
    - PERCENT: 예수금의 quantity_value% 금액으로 매수 가능한 수량 (기준가 필요)
    - AMOUNT: quantity_value 금액으로 매수 가능한 수량 (기준가 필요)

    기준가: 지정가 주문이면 limit_price, 시장가면 reference_price(예상가) 필수.

    Raises:
        ValidationError: 예수금 부족, 가격 없음 등
    """
    if quantity_type == QUANTITY_TYPE_EXACT:
        if quantity_value is None:
            raise ValidationError('직접 수량 지정 시 quantity(수량)이 필요합니다.')
        q = Decimal(str(quantity_value))
        if q <= 0:
            raise ValidationError('수량은 0보다 커야 합니다.')
        return q

    cash = _get_cash_balance(account, symbol)
    if cash <= 0:
        raise ValidationError('매수 가능한 예수금이 없습니다.')

    # MAX/PERCENT/AMOUNT는 기준가 필요
    price = limit_price or reference_price
    if price is None or price <= 0:
        raise ValidationError(
            '전량/비율/금액 매수는 기준 가격이 필요합니다. '
            '지정가 주문이면 price를 넣고, 시장가 주문이면 예상가(reference_price)를 넣어 주세요.'
        )

    if quantity_type == QUANTITY_TYPE_MAX:
        amount = cash
    elif quantity_type == QUANTITY_TYPE_PERCENT:
        if quantity_value is None:
            raise ValidationError('비율(%) 지정 시 quantity_value가 필요합니다.')
        pct = Decimal(str(quantity_value))
        if pct <= 0 or pct > 100:
            raise ValidationError('비율은 0 초과 100 이하여야 합니다.')
        amount = (cash * pct / 100).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
    elif quantity_type == QUANTITY_TYPE_AMOUNT:
        if quantity_value is None:
            raise ValidationError('금액 지정 시 quantity_value(금액)가 필요합니다.')
        amount = Decimal(str(quantity_value))
        if amount <= 0:
            raise ValidationError('금액은 0보다 커야 합니다.')
        if amount > cash:
            raise ValidationError(
                f'지정 금액({amount})이 예수금({cash})을 초과합니다.'
            )
    else:
        raise ValidationError(f'지원하지 않는 수량 지정 방식: {quantity_type}')

    if amount <= 0:
        raise ValidationError('매수에 사용할 금액이 0입니다.')

    q = (amount / price).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
    if q <= 0:
        raise ValidationError('금액/가격으로 계산된 매수 수량이 0입니다.')

    return q
