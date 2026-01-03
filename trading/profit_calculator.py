"""
실현 손익 계산 모듈
"""
import logging
from decimal import Decimal
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from django.db.models import Q, Sum, F
from django.utils import timezone
from .models import Order, Account, OrderStatus, DailyRealizedProfit

logger = logging.getLogger(__name__)


class ProfitCalculator:
    """실현 손익 계산기"""
    
    @staticmethod
    def calculate_realized_profit_for_order(sell_order: Order) -> Decimal:
        """
        특정 매도 주문에 대한 실현 손익 계산 (FIFO 방식)
        
        Args:
            sell_order: 매도 주문 (체결 완료된 것만)
        
        Returns:
            실현 손익 금액
        """
        if sell_order.side != 'SELL' or sell_order.status != OrderStatus.FILLED:
            return Decimal('0')
        
        if not sell_order.filled_quantity or not sell_order.average_filled_price:
            return Decimal('0')
        
        account = sell_order.account
        symbol = sell_order.symbol
        sell_quantity = Decimal(str(sell_order.filled_quantity))
        sell_price = Decimal(str(sell_order.average_filled_price))
        
        # 해당 계좌의 같은 종목에 대한 매수 주문들 (체결 완료, 매도 주문보다 이전)
        buy_orders = Order.objects.filter(
            account=account,
            symbol=symbol,
            side='BUY',
            status=OrderStatus.FILLED,
            filled_at__lt=sell_order.filled_at
        ).order_by('filled_at')
        
        remaining_sell_quantity = sell_quantity
        total_cost = Decimal('0')
        
        # FIFO 방식으로 매수 주문 처리
        for buy_order in buy_orders:
            if remaining_sell_quantity <= 0:
                break
            
            buy_quantity = Decimal(str(buy_order.filled_quantity))
            buy_price = Decimal(str(buy_order.average_filled_price))
            
            # 아직 매도되지 않은 매수 수량 계산
            # (간단화: 매수 주문의 전체 수량을 원가로 사용)
            # 실제로는 더 복잡한 로직이 필요할 수 있음
            
            if remaining_sell_quantity >= buy_quantity:
                # 이 매수 주문 전체를 매도
                total_cost += buy_quantity * buy_price
                remaining_sell_quantity -= buy_quantity
            else:
                # 이 매수 주문의 일부만 매도
                total_cost += remaining_sell_quantity * buy_price
                remaining_sell_quantity = Decimal('0')
        
        # 실현 손익 = 매도 금액 - 매수 원가
        total_sell_amount = sell_quantity * sell_price
        realized_profit = total_sell_amount - total_cost
        
        return realized_profit
    
    @staticmethod
    def calculate_daily_realized_profit(account: Account, target_date: date) -> Dict:
        """
        특정 계좌의 특정 날짜 실현 손익 계산
        
        Args:
            account: 계좌
            target_date: 날짜
        
        Returns:
            {
                'realized_profit': Decimal,
                'total_buy_amount': Decimal,
                'total_sell_amount': Decimal,
                'realized_profit_rate': Decimal
            }
        """
        start_datetime = timezone.make_aware(datetime.combine(target_date, datetime.min.time()))
        end_datetime = start_datetime + timedelta(days=1)
        
        # 해당 날짜에 체결된 매도 주문들
        sell_orders = Order.objects.filter(
            account=account,
            side='SELL',
            status=OrderStatus.FILLED,
            filled_at__gte=start_datetime,
            filled_at__lt=end_datetime
        )
        
        total_realized_profit = Decimal('0')
        total_buy_amount = Decimal('0')
        total_sell_amount = Decimal('0')
        
        for sell_order in sell_orders:
            # 각 매도 주문에 대한 실현 손익 계산
            realized_profit = ProfitCalculator.calculate_realized_profit_for_order(sell_order)
            total_realized_profit += realized_profit
            
            # 매도 금액
            if sell_order.filled_quantity and sell_order.average_filled_price:
                sell_amount = Decimal(str(sell_order.filled_quantity)) * Decimal(str(sell_order.average_filled_price))
                total_sell_amount += sell_amount
        
        # 실현 손익률 계산
        realized_profit_rate = Decimal('0')
        if total_sell_amount > 0:
            realized_profit_rate = (total_realized_profit / total_sell_amount) * Decimal('100')
        
        return {
            'realized_profit': total_realized_profit,
            'total_buy_amount': total_buy_amount,
            'total_sell_amount': total_sell_amount,
            'realized_profit_rate': realized_profit_rate
        }
    
    @staticmethod
    def update_daily_realized_profit(account: Account, target_date: date) -> DailyRealizedProfit:
        """
        일일 실현 손익 업데이트 또는 생성
        """
        profit_data = ProfitCalculator.calculate_daily_realized_profit(account, target_date)
        
        daily_profit, created = DailyRealizedProfit.objects.update_or_create(
            account=account,
            date=target_date,
            defaults={
                'realized_profit': profit_data['realized_profit'],
                'realized_profit_rate': profit_data['realized_profit_rate'],
                'total_buy_amount': profit_data['total_buy_amount'],
                'total_sell_amount': profit_data['total_sell_amount'],
            }
        )
        
        if created:
            logger.info(f"일일 실현 손익 생성: {account.user.username} - {target_date}")
        else:
            logger.debug(f"일일 실현 손익 업데이트: {account.user.username} - {target_date}")
        
        return daily_profit
    
    @staticmethod
    def update_all_accounts_daily_profit(target_date: Optional[date] = None):
        """
        모든 계좌의 일일 실현 손익 업데이트
        
        Args:
            target_date: 날짜 (None이면 오늘)
        """
        if target_date is None:
            target_date = timezone.now().date()
        
        accounts = Account.objects.all()
        updated_count = 0
        
        for account in accounts:
            try:
                ProfitCalculator.update_daily_realized_profit(account, target_date)
                updated_count += 1
            except Exception as e:
                logger.error(f"계좌 {account.id}의 일일 실현 손익 계산 실패: {str(e)}")
        
        logger.info(f"일일 실현 손익 업데이트 완료: {updated_count}개 계좌")
        return updated_count
    
    @staticmethod
    def get_user_daily_profit(user, target_date: date) -> Dict:
        """
        사용자의 특정 날짜 실현 손익 조회 (실시간 계산)
        """
        accounts = Account.objects.filter(user=user)
        total_realized_profit = Decimal('0')
        total_buy_amount = Decimal('0')
        total_sell_amount = Decimal('0')
        
        for account in accounts:
            profit_data = ProfitCalculator.calculate_daily_realized_profit(account, target_date)
            total_realized_profit += profit_data['realized_profit']
            total_buy_amount += profit_data['total_buy_amount']
            total_sell_amount += profit_data['total_sell_amount']
        
        realized_profit_rate = Decimal('0')
        if total_sell_amount > 0:
            realized_profit_rate = (total_realized_profit / total_sell_amount) * Decimal('100')
        
        return {
            'date': target_date,
            'realized_profit': total_realized_profit,
            'total_buy_amount': total_buy_amount,
            'total_sell_amount': total_sell_amount,
            'realized_profit_rate': realized_profit_rate,
            'account_count': accounts.count()
        }

