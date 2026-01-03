"""
주문 처리 태스크
"""
import logging
from decimal import Decimal
from django.utils import timezone
from .models import Order, OrderStatus
from .clients import get_broker_client

logger = logging.getLogger(__name__)


def process_orders():
    """대기중인 주문들을 처리"""
    pending_orders = Order.objects.filter(status=OrderStatus.PENDING)
    
    logger.info(f"처리할 주문 수: {pending_orders.count()}")
    
    for order in pending_orders:
        try:
            process_order(order)
        except Exception as e:
            logger.error(f"주문 처리 실패 (Order ID: {order.id}): {str(e)}")
            order.status = OrderStatus.REJECTED
            order.save()


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
                        order.status = OrderStatus.FILLED
                        order.filled_quantity = Decimal(str(executed_volume))
                        order.average_filled_price = Decimal(str(avg_price)) if avg_price > 0 else None
                        order.filled_at = timezone.now()
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

