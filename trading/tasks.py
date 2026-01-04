"""
주문 처리 태스크 및 계좌 정보 업데이트
"""
import logging
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from .models import Order, OrderStatus, Account, Holding, Symbol, Currency
from .clients import get_broker_client

logger = logging.getLogger(__name__)


def process_orders():
    """대기중인 주문들을 처리"""
    pending_orders = Order.objects.filter(status=OrderStatus.PENDING)
    pending_count = pending_orders.count()
    
    logger.info(f"처리할 주문 수: {pending_count}")
    
    processed_count = 0
    for order in pending_orders:
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
                
                # 수익률 계산 (초기 투자 대비, 간단히 처리)
                # 실제로는 초기 투자 금액을 별도로 저장해야 정확한 수익률 계산 가능
                # total_assets는 property이므로 직접 사용 가능
                if account.total_assets > 0:
                    # 임시로 이전 총 자산과 비교 (더 정확한 계산은 별도 필드 필요)
                    previous_total = account.total_assets
                    # 여기서는 간단히 처리, 실제로는 초기 투자 금액 필드가 필요
                    account.profit_rate = Decimal('0')  # 수익률 계산 로직 추가 필요
                
                account.save(update_fields=[
                    'profit_rate',
                    'cash_balance_krw', 'stock_value_krw', 'total_assets_krw',
                    'cash_balance_usd', 'stock_value_usd', 'total_assets_usd'
                ])
                
                # 보유 종목 정보 업데이트
                holdings_data = result.get('holdings', [])
                update_holdings(account, holdings_data)
            
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
    
    # 현재 보유 종목의 티커 집합
    current_tickers = set()
    
    for holding_info in holdings_data:
        ticker = holding_info.get('ticker', '')
        currency = holding_info.get('currency', 'KRW')
        
        if not ticker:
            logger.warning(f"티커가 없는 보유 종목 데이터: {holding_info}")
            continue
        
        logger.debug(f"보유 종목 처리: {ticker} ({currency})")
        
        # 통화 확인 (해외 주식인지 확인)
        currency = holding_info.get('currency', 'KRW')
        if currency == 'USD':
            symbol_currency = Currency.USD
        elif currency == 'USDT':
            symbol_currency = Currency.USDT
        else:
            # 기본값: 암호화폐 거래소면 USDT, 아니면 KRW
            symbol_currency = account.broker.is_crypto_exchange and Currency.USDT or Currency.KRW
        
        # Symbol 찾기 또는 생성
        symbol, _ = Symbol.objects.get_or_create(
            ticker=ticker,
            defaults={
                'name': holding_info.get('name', ticker),
                'currency': symbol_currency,
                'broker': account.broker,
                'is_crypto': account.broker.is_crypto_exchange,
            }
        )
        
        # Symbol의 currency 업데이트 (기존 Symbol이 다른 통화로 설정되어 있을 수 있음)
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
            
            # Holding 업데이트 또는 생성
            # 해외 종목의 경우 current_price가 0일 수 있으므로 average_price를 사용
            if current_price == 0 and average_price > 0:
                # 현재가가 없으면 평균 매수가를 현재가로 사용
                effective_current_price = average_price
            else:
                effective_current_price = current_price
            
            # 평균 매수가가 없으면 현재가를 사용
            if average_price == 0 and current_price > 0:
                effective_average_price = current_price
            else:
                effective_average_price = average_price
            
            # total_value가 0이면 계산
            if total_value == 0 and quantity > 0:
                if effective_current_price > 0:
                    total_value = quantity * effective_current_price
                elif effective_average_price > 0:
                    total_value = quantity * effective_average_price
            
            holding, created = Holding.objects.update_or_create(
                account=account,
                symbol=symbol,
                defaults={
                    'quantity': quantity,
                    'current_price': effective_current_price,  # 0이 아닌 값으로 저장
                    'average_price': effective_average_price,
                    'total_value': total_value,
                    # profit_loss와 profit_rate는 save()에서 자동 계산됨
                }
            )
            
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
    
    logger.info(f"보유 종목 업데이트 완료: {len(current_tickers)}개 종목 (국내+해외)")

