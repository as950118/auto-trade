"""
주문 처리 태스크 및 계좌 정보 업데이트
"""
import logging
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from django.db.models import F
from .models import Order, OrderStatus, Account, Holding, Symbol, Currency, TargetAllocationPlan
from .clients import get_broker_client

logger = logging.getLogger(__name__)


def process_orders():
    """대기중인 주문들을 처리"""
    pending_order_ids = list(
        Order.objects.filter(status=OrderStatus.PENDING).values_list('id', flat=True)
    )

    logger.info(f"처리할 주문 수: {len(pending_order_ids)}")

    processed_count = 0
    for order_id in pending_order_ids:
        # PENDING -> SUBMITTING 전이를 하나의 원자적 UPDATE로 수행한다.
        # 브로커 호출(place_order)이 스케줄러 주기보다 오래 걸리는 경우,
        # 이 전이가 없으면 같은 주문이 여전히 PENDING 상태로 남아있어
        # 다음 스케줄에서 다시 집혀 브로커에 중복 체결될 수 있었다.
        # filter().update()는 PostgreSQL/SQLite 모두에서 단일 원자적 문장이므로
        # 갱신된 행 수(claimed)로 "내가 이 주문을 선점했는지"를 안전하게 판별할 수 있다.
        claimed = Order.objects.filter(
            id=order_id, status=OrderStatus.PENDING
        ).update(status=OrderStatus.SUBMITTING)

        if claimed == 0:
            # 다른 실행(또는 이전에 이미 시작된 처리)이 먼저 선점했거나
            # 그 사이 상태가 바뀐 경우 - 건너뛴다.
            logger.info(f"이미 선점되었거나 상태가 변경된 주문, 건너뜀 (Order ID: {order_id})")
            continue

        order = Order.objects.get(id=order_id)
        try:
            process_order(order)
            processed_count += 1
        except Exception as e:
            logger.error(f"주문 처리 실패 (Order ID: {order.id}): {str(e)}")
            order.status = OrderStatus.REJECTED
            order.save()

    return processed_count


def process_order(order: Order):
    """단일 주문 처리"""
    account = order.account
    
    # 계좌의 거래 제한 확인
    if order.side == 'BUY' and not account.buy_enabled:
        order.status = OrderStatus.REJECTED
        order.save()
        logger.warning(f"매수 비활성화된 계좌: {account.id}")
        return
    
    if order.side == 'SELL' and not account.sell_enabled:
        order.status = OrderStatus.REJECTED
        order.save()
        logger.warning(f"매도 비활성화된 계좌: {account.id}")
        return
    
    # 투자 상한선 확인
    if account.investment_limit:
        if order.side == 'BUY':
            order_value = float(order.quantity) * float(order.price or 0)
            if order_value > float(account.investment_limit):
                order.status = OrderStatus.REJECTED
                order.save()
                logger.warning(f"투자 상한선 초과: {account.id}")
                return
    
    try:
        # 브로커 클라이언트 가져오기
        client = get_broker_client(account)
        
        # 주문 실행
        result = client.place_order(order)
        
        if result.get('success'):
            # 주문 성공
            order_id = result.get('order_id')
            if order_id:
                # 외부 주문 ID 저장 (브로커에서 반환한 주문 ID)
                order.external_order_id = str(order_id)
            
            # 주문 상태를 부분체결로 변경 (실제 체결 여부는 추후 확인)
            order.status = OrderStatus.PARTIALLY_FILLED
            order.save()
            
            logger.info(f"주문 실행 성공 (Order ID: {order.id}, External ID: {order_id})")
            
            # 주문 상태 확인 (비동기로 처리하거나 다음 스케줄에서 확인)
            check_order_status(order, client)
        else:
            # 주문 실패
            error_msg = result.get('error', '알 수 없는 오류')
            order.status = OrderStatus.REJECTED
            order.save()
            logger.error(f"주문 실행 실패 (Order ID: {order.id}): {error_msg}")
    
    except ValueError as e:
        # 브로커 클라이언트 생성 실패
        order.status = OrderStatus.REJECTED
        order.save()
        logger.error(f"브로커 클라이언트 생성 실패 (Order ID: {order.id}): {str(e)}")
    except Exception as e:
        # 기타 오류
        order.status = OrderStatus.REJECTED
        order.save()
        logger.error(f"주문 처리 중 오류 발생 (Order ID: {order.id}): {str(e)}")


def run_target_allocation_plans():
    """
    목표 비율 자동매매 계획 실행.
    총 자산 대비 목표 비율까지, 설정한 일수/횟수에 맞춰 분할 매수/매도 주문을 생성한다.
    """
    from datetime import timedelta
    from decimal import ROUND_DOWN

    today = timezone.now().date()
    plans = TargetAllocationPlan.objects.filter(
        enabled=True,
        trades_done__lt=F('num_trades'),
        start_date__lte=today,
        end_date__gte=today,
    ).select_related('account', 'symbol')

    created_count = 0
    for plan in plans:
        try:
            account = plan.account
            symbol = plan.symbol
            currency = symbol.currency

            # 총 자산 (종목 통화 기준)
            if currency == 'KRW':
                total_assets = account.total_assets_krw or Decimal('0')
            else:
                total_assets = account.total_assets_usd or Decimal('0')

            if total_assets <= 0:
                logger.warning(f"목표비율 계획 {plan.id}: 총 자산 0, 스킵")
                continue

            # 해당 종목 현재 보유 가치
            try:
                holding = Holding.objects.get(account=account, symbol=symbol)
                current_value = holding.total_value or Decimal('0')
                current_price = holding.current_price or holding.average_price or Decimal('0')
            except Holding.DoesNotExist:
                current_value = Decimal('0')
                current_price = Decimal('0')
                # 현재가 없으면 시세 조회는 별도 로직 필요; 여기서는 스킵
                logger.warning(f"목표비율 계획 {plan.id}: 보유/시세 없음, 스킵")
                continue

            if current_price <= 0:
                logger.warning(f"목표비율 계획 {plan.id}: 현재가 0, 스킵")
                continue

            target_value = (total_assets * plan.target_ratio).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
            deficit = target_value - current_value
            remaining = plan.num_trades - plan.trades_done
            if remaining <= 0:
                continue

            # 이번에 실행할 횟수: 기간 내 진행률에 맞춰 1회씩
            total_days = (plan.end_date - plan.start_date).days or 1
            elapsed_days = (today - plan.start_date).days
            trades_should_have = min(plan.num_trades, int(plan.num_trades * elapsed_days / total_days))
            to_do = max(0, trades_should_have - plan.trades_done)
            if to_do <= 0:
                continue

            # 1회분 금액 (부족분을 남은 횟수로 나눔)
            order_amount = (deficit / remaining).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
            min_order_value = Decimal('0.01')  # 최소 주문 금액
            if abs(order_amount) < min_order_value:
                continue

            side = 'BUY' if order_amount > 0 else 'SELL'
            if side == 'BUY' and not account.buy_enabled:
                logger.warning(f"목표비율 계획 {plan.id}: 매수 비활성화")
                continue
            if side == 'SELL' and not account.sell_enabled:
                logger.warning(f"목표비율 계획 {plan.id}: 매도 비활성화")
                continue

            if side == 'BUY':
                quantity = (order_amount / current_price).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
            else:
                # 매도: 금액만큼 팔 수량
                quantity = (abs(order_amount) / current_price).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
                # 보유 수량 초과 방지
                try:
                    h = Holding.objects.get(account=account, symbol=symbol)
                    if quantity > h.quantity:
                        quantity = h.quantity
                except Holding.DoesNotExist:
                    continue
                if quantity <= 0:
                    continue

            if quantity <= 0:
                continue

            price = current_price if plan.order_type == 'LIMIT' else None
            order = Order.objects.create(
                account=account,
                symbol=symbol,
                side=side,
                order_type=plan.order_type,
                quantity=quantity,
                price=price,
                status=OrderStatus.PENDING,
            )
            plan.trades_done += 1
            plan.save(update_fields=['trades_done', 'updated_at'])
            created_count += 1
            logger.info(f"목표비율 계획 {plan.id}: {side} 주문 생성 (Order {order.id}), {plan.trades_done}/{plan.num_trades}")
        except Exception as e:
            logger.exception(f"목표비율 계획 {plan.id} 실행 중 오류: {e}")

    return created_count


def check_order_status(order: Order, client=None):
    """주문 상태 확인 및 업데이트"""
    if not client:
        try:
            client = get_broker_client(order.account)
        except Exception as e:
            logger.error(f"클라이언트 생성 실패: {str(e)}")
            return
    
    try:
        result = client.get_order_status(order)
        
        if result.get('success'):
            data = result.get('data', {})
            
            # 브로커별로 응답 형식이 다르므로 각각 처리
            broker = order.account.broker
            
            if broker.is_crypto_exchange and 'upbit' in broker.name.lower():
                # Upbit 응답 처리
                if isinstance(data, dict):
                    state = data.get('state')
                    executed_volume = float(data.get('executed_volume', 0))
                    avg_price = float(data.get('avg_price', 0))
                    uuid = data.get('uuid')  # Upbit 주문 UUID
                    
                    # 외부 주문 ID가 없으면 저장
                    if uuid and not order.external_order_id:
                        order.external_order_id = uuid
                    
                    if state == 'done':
                        # 체결 완료
                        was_filled = order.status == OrderStatus.FILLED
                        order.status = OrderStatus.FILLED
                        order.filled_quantity = Decimal(str(executed_volume))
                        order.average_filled_price = Decimal(str(avg_price)) if avg_price > 0 else None
                        order.filled_at = timezone.now()
                        
                        # 매도 주문이고 새로 체결된 경우 실현 손익 계산
                        if not was_filled and order.side == 'SELL':
                            from .profit_calculator import ProfitCalculator
                            try:
                                ProfitCalculator.update_daily_realized_profit(
                                    order.account,
                                    order.filled_at.date()
                                )
                            except Exception as e:
                                logger.error(f"실현 손익 계산 실패 (Order ID: {order.id}): {str(e)}")

                        # 전략 연동 주문 수수료 훅 (현재 no-op — strategy_fees 참고)
                        if not was_filled:
                            try:
                                from .strategy_fees import on_strategy_order_filled
                                on_strategy_order_filled(order)
                            except Exception as e:
                                logger.error(f"전략 수수료 훅 실패 (Order ID: {order.id}): {str(e)}")
                    elif state == 'cancel':
                        # 취소됨
                        order.status = OrderStatus.CANCELLED
                    elif executed_volume > 0:
                        # 부분 체결
                        order.status = OrderStatus.PARTIALLY_FILLED
                        order.filled_quantity = Decimal(str(executed_volume))
                        order.average_filled_price = Decimal(str(avg_price)) if avg_price > 0 else None
            
            else:
                # 한국투자증권 응답 처리
                # 실제 API 응답 형식에 맞게 수정 필요
                output = data.get('output', [])
                if isinstance(output, list) and len(output) > 0:
                    # 주문 상태 확인 로직 구현
                    pass
            
            order.save()
            logger.info(f"주문 상태 업데이트 완료 (Order ID: {order.id}, Status: {order.status})")
        
    except Exception as e:
        logger.error(f"주문 상태 확인 실패 (Order ID: {order.id}): {str(e)}")


def update_accounts_info():
    """모든 계좌의 정보 업데이트 (잔고, 보유 종목 가치 등)"""
    accounts = Account.objects.all()
    updated_count = 0
    failed_count = 0
    
    logger.info(f"계좌 정보 업데이트 시작: {accounts.count()}개 계좌")
    
    for account in accounts:
        try:
            update_account_info(account)
            updated_count += 1
        except Exception as e:
            failed_count += 1
            logger.error(f"계좌 정보 업데이트 실패 (Account ID: {account.id}): {str(e)}")
    
    logger.info(f"계좌 정보 업데이트 완료: 성공 {updated_count}개, 실패 {failed_count}개")
    return updated_count


def update_account_info(account: Account):
    """단일 계좌의 정보 업데이트"""
    try:
        # 브로커 클라이언트 가져오기
        client = get_broker_client(account)
        
        # 계좌 정보 조회
        result = client.get_account_info()
        
        if result.get('success'):
            with transaction.atomic():
                # 통화별 자산 업데이트 (통화별 필드만 저장)
                account.cash_balance_krw = result.get('cash_balance_krw', Decimal('0'))
                account.stock_value_krw = result.get('stock_value_krw', Decimal('0'))
                account.total_assets_krw = result.get('total_assets_krw', Decimal('0'))
                account.cash_balance_usd = result.get('cash_balance_usd', Decimal('0'))
                account.stock_value_usd = result.get('stock_value_usd', Decimal('0'))
                account.total_assets_usd = result.get('total_assets_usd', Decimal('0'))
                
                account.save(update_fields=[
                    'cash_balance_krw', 'stock_value_krw', 'total_assets_krw',
                    'cash_balance_usd', 'stock_value_usd', 'total_assets_usd'
                ])
                
                # 보유 종목 정보 업데이트 (내부에서 계좌 수익률도 계산됨)
                holdings_data = result.get('holdings', [])
                update_holdings(account, holdings_data)
                
                # 보유 종목 업데이트 후 실제 DB의 보유 종목 총액으로 계좌 자산 정보 재계산
                _recalculate_account_assets(account)
            
            logger.info(f"계좌 정보 업데이트 성공 (Account ID: {account.id}, 총 자산: {account.total_assets})")
        else:
            error_msg = result.get('error', '알 수 없는 오류')
            logger.warning(f"계좌 정보 조회 실패 (Account ID: {account.id}): {error_msg}")
            raise Exception(error_msg)
    
    except ValueError as e:
        # 브로커 클라이언트 생성 실패 (API 키 없음 등)
        logger.warning(f"계좌 정보 업데이트 스킵 (Account ID: {account.id}): {str(e)}")
    except Exception as e:
        logger.error(f"계좌 정보 업데이트 중 오류 (Account ID: {account.id}): {str(e)}")
        raise


def update_holdings(account: Account, holdings_data: list):
    """보유 종목 정보 업데이트"""
    logger.info(f"보유 종목 업데이트 시작: {len(holdings_data)}개 종목 데이터")
    
    # 브로커 클라이언트 가져오기 (암호화폐 시세 조회용)
    client = None
    try:
        client = get_broker_client(account)
    except Exception as e:
        logger.warning(f"브로커 클라이언트 생성 실패 (시세 조회 불가): {str(e)}")
    
    # 현재 보유 종목의 티커 집합
    current_tickers = set()
    
    for holding_info in holdings_data:
        ticker = holding_info.get('ticker', '')
        currency = holding_info.get('currency', 'KRW')
        
        if not ticker:
            logger.warning(f"티커가 없는 보유 종목 데이터: {holding_info}")
            continue
        
        logger.debug(f"보유 종목 처리: {ticker} ({currency})")
        
        # holding_info.currency를 우선 사용 (Upbit KRW 마켓은 KRW)
        currency_code = (holding_info.get('currency') or 'KRW').upper()
        valid_currencies = {choice.value for choice in Currency}
        if currency_code in valid_currencies:
            symbol_currency = currency_code
        elif account.broker.is_crypto_exchange:
            # 알 수 없는 암호화폐 페어 기본값
            symbol_currency = Currency.USDT
        else:
            symbol_currency = Currency.KRW
        
        # Symbol 찾기 또는 생성 (티커는 전역 unique)
        symbol, _ = Symbol.objects.get_or_create(
            ticker=ticker,
            defaults={
                'name': holding_info.get('name', ticker),
                'currency': symbol_currency,
                'broker': account.broker,
                'is_crypto': account.broker.is_crypto_exchange,
            }
        )

        if symbol.currency != symbol_currency:
            symbol.currency = symbol_currency
            symbol.save(update_fields=['currency'])
        
        quantity = Decimal(str(holding_info.get('quantity', 0)))
        current_price = Decimal(str(holding_info.get('current_price', 0)))
        average_price = Decimal(str(holding_info.get('average_price', 0)))
        total_value = Decimal(str(holding_info.get('total_value', 0)))
        
        # 보유 수량이 0보다 큰 경우만 저장
        if quantity > 0:
            current_tickers.add(ticker)
            
            # 암호화폐인 경우 현재가가 0이면 API로 조회
            if account.broker.is_crypto_exchange and current_price == 0 and client:
                try:
                    api_price = client.get_crypto_price(ticker)
                    if api_price and api_price > 0:
                        current_price = api_price
                        logger.debug(f"API로 현재가 조회 성공: {ticker} = {current_price}")
                except Exception as e:
                    logger.warning(f"현재가 조회 실패 ({ticker}): {str(e)}")
            
            # Holding 업데이트 또는 생성
            # 해외 종목의 경우 current_price가 0일 수 있음
            # save() 메서드에서 current_price가 0이면 average_price를 사용하여 수익률 계산
            
            # 평균 매수가가 없으면 현재가를 사용
            if average_price == 0 and current_price > 0:
                effective_average_price = current_price
            else:
                effective_average_price = average_price
            
            # total_value가 0이면 계산
            if total_value == 0 and quantity > 0:
                # current_price가 있으면 사용, 없으면 average_price 사용
                price_for_value = current_price if current_price > 0 else effective_average_price
                if price_for_value > 0:
                    total_value = quantity * price_for_value
            
            # current_price는 실제 값이 있으면 저장, 없으면 0으로 저장
            # save() 메서드에서 current_price가 0이면 average_price를 사용하여 수익률 계산
            holding, created = Holding.objects.update_or_create(
                account=account,
                symbol=symbol,
                defaults={
                    'quantity': quantity,
                    'current_price': current_price,  # 실제 현재가 (0일 수 있음)
                    'average_price': effective_average_price,
                    'total_value': total_value,
                    # profit_loss와 profit_rate는 save()에서 자동 계산됨
                    # save()에서 current_price가 0이면 average_price를 사용하여 수익률 계산
                }
            )
            
            # update_or_create 후 명시적으로 save()를 호출하여 profit_loss와 profit_rate 계산
            # (update_or_create의 defaults에 없는 필드는 업데이트되지 않을 수 있음)
            holding.save()
            
            if created:
                logger.info(f"보유 종목 생성: {account.id} - {ticker} ({currency}, {quantity}주)")
            else:
                logger.debug(f"보유 종목 업데이트: {account.id} - {ticker} ({currency}, {quantity}주)")
        else:
            logger.warning(f"보유 수량이 0인 종목 스킵: {ticker}")
    
    # 더 이상 보유하지 않는 종목 삭제 (수량이 0인 경우)
    deleted_count = Holding.objects.filter(account=account).exclude(symbol__ticker__in=current_tickers).delete()[0]
    if deleted_count > 0:
        logger.info(f"보유하지 않는 종목 {deleted_count}개 삭제 완료")
    
    # 계좌의 수익률 계산 및 업데이트
    _update_account_profit_rate(account)
    
    logger.info(f"보유 종목 업데이트 완료: {len(current_tickers)}개 종목 (국내+해외)")


def _recalculate_account_assets(account: Account):
    """보유 종목의 실제 total_value 합계로 계좌 자산 정보 재계산"""
    from .models import Holding
    
    # 보유 종목들의 실제 total_value 합계 계산
    holdings = Holding.objects.filter(account=account, quantity__gt=0)
    
    stock_value_krw = Decimal('0')
    stock_value_usd = Decimal('0')
    
    for holding in holdings:
        if holding.symbol.currency == 'KRW':
            stock_value_krw += holding.total_value
        elif holding.symbol.currency == 'USD':
            stock_value_usd += holding.total_value
        elif holding.symbol.currency == 'USDT':
            # USDT는 USD로 처리
            stock_value_usd += holding.total_value
    
    # 계좌 자산 정보 업데이트 (예수금은 유지, 보유종목가치만 재계산)
    account.stock_value_krw = stock_value_krw
    account.stock_value_usd = stock_value_usd
    account.total_assets_krw = account.cash_balance_krw + stock_value_krw
    account.total_assets_usd = account.cash_balance_usd + stock_value_usd
    
    account.save(update_fields=[
        'stock_value_krw', 'stock_value_usd',
        'total_assets_krw', 'total_assets_usd'
    ])
    
    logger.debug(f"계좌 자산 재계산: {account.id} - 보유종목가치 (KRW: {stock_value_krw:,.0f}원, USD: ${stock_value_usd:,.2f})")


def _update_account_profit_rate(account: Account):
    """계좌의 평가손익·수익률을 통화별로 계산 (환율 환산 없음).

    - KRW 종목 → profit_loss_krw / profit_rate_krw
    - USD·USDT 종목 → profit_loss_usd / profit_rate_usd
    - 하위호환 profit_loss / profit_rate 는 자산이 있는 주 통화 값 사용
    """
    from .models import Holding

    holdings = Holding.objects.filter(account=account, quantity__gt=0).select_related('symbol')

    profit_loss_krw = Decimal('0')
    cost_krw = Decimal('0')
    profit_loss_usd = Decimal('0')
    cost_usd = Decimal('0')

    for holding in holdings:
        if not holding.current_price or holding.current_price <= 0:
            continue
        if not holding.average_price or holding.average_price <= 0:
            continue

        cost = holding.quantity * holding.average_price
        pl = holding.profit_loss or Decimal('0')
        currency = holding.symbol.currency

        if currency == 'KRW':
            profit_loss_krw += pl
            cost_krw += cost
        elif currency in ('USD', 'USDT'):
            profit_loss_usd += pl
            cost_usd += cost
        else:
            # 기타 통화는 KRW 버킷에 보관
            profit_loss_krw += pl
            cost_krw += cost

    rate_krw = (
        ((profit_loss_krw / cost_krw) * 100).quantize(Decimal('0.0001'))
        if cost_krw > 0 else Decimal('0')
    )
    rate_usd = (
        ((profit_loss_usd / cost_usd) * 100).quantize(Decimal('0.0001'))
        if cost_usd > 0 else Decimal('0')
    )

    account.profit_loss_krw = profit_loss_krw.quantize(Decimal('0.01'))
    account.profit_rate_krw = rate_krw
    account.profit_loss_usd = profit_loss_usd.quantize(Decimal('0.01'))
    account.profit_rate_usd = rate_usd

    # 주 통화 요약: 원가 기준 더 큰 쪽 (둘 다 있으면 KRW 우선)
    if cost_usd > cost_krw:
        account.profit_loss = account.profit_loss_usd
        account.profit_rate = account.profit_rate_usd
    else:
        account.profit_loss = account.profit_loss_krw
        account.profit_rate = account.profit_rate_krw

    account.save(update_fields=[
        'profit_loss_krw', 'profit_rate_krw',
        'profit_loss_usd', 'profit_rate_usd',
        'profit_loss', 'profit_rate',
    ])
    logger.debug(
        f"계좌 수익률 업데이트: {account.id} - "
        f"KRW pl={account.profit_loss_krw} ({account.profit_rate_krw}%), "
        f"USD pl={account.profit_loss_usd} ({account.profit_rate_usd}%)"
    )


def update_crypto_prices():
    """암호화폐 보유 종목의 현재가 업데이트"""
    from .models import Holding, Symbol, Account
    from .clients import get_broker_client
    
    logger.info("암호화폐 시세 업데이트 시작")
    
    # 암호화폐 보유 종목만 조회
    crypto_holdings = Holding.objects.filter(
        symbol__is_crypto=True,
        quantity__gt=0
    ).select_related('account', 'symbol', 'account__broker')
    
    updated_count = 0
    failed_count = 0
    
    # 계좌별로 그룹화하여 클라이언트 재사용
    accounts_dict = {}
    for holding in crypto_holdings:
        account_id = holding.account.id
        if account_id not in accounts_dict:
            accounts_dict[account_id] = {
                'account': holding.account,
                'holdings': []
            }
        accounts_dict[account_id]['holdings'].append(holding)
    
    for account_id, data in accounts_dict.items():
        account = data['account']
        holdings = data['holdings']
        
        # 브로커 클라이언트 가져오기
        try:
            client = get_broker_client(account)
        except Exception as e:
            logger.warning(f"브로커 클라이언트 생성 실패 (Account ID: {account_id}): {str(e)}")
            failed_count += len(holdings)
            continue
        
        # 각 보유 종목의 현재가 업데이트
        for holding in holdings:
            try:
                ticker = holding.symbol.ticker
                
                # 현재가 조회
                current_price = client.get_crypto_price(ticker)
                
                if current_price and current_price > 0:
                    # 현재가 업데이트
                    holding.current_price = current_price
                    
                    # 총 가치 재계산
                    if holding.quantity > 0:
                        holding.total_value = holding.quantity * current_price
                    
                    # save()에서 profit_loss와 profit_rate 자동 계산됨
                    holding.save(update_fields=['current_price', 'total_value'])
                    updated_count += 1
                    logger.debug(f"시세 업데이트: {ticker} = {current_price}")
                else:
                    logger.warning(f"현재가 조회 실패 또는 0: {ticker}")
                    failed_count += 1
            except Exception as e:
                logger.warning(f"시세 업데이트 실패 ({holding.symbol.ticker}): {str(e)}")
                failed_count += 1
    
    logger.info(f"암호화폐 시세 업데이트 완료: 성공 {updated_count}개, 실패 {failed_count}개")
    return updated_count

