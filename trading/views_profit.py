from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from decimal import Decimal
from datetime import timedelta
from datetime import date as date_type
from .models import Account, DailyRealizedProfit
from .serializers import DailyRealizedProfitSerializer
from .profit_calculator import ProfitCalculator


class DailyRealizedProfitViewSet(viewsets.ReadOnlyModelViewSet):
    """일일 실현 손익 ViewSet"""
    permission_classes = [IsAuthenticated]
    serializer_class = DailyRealizedProfitSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['account', 'date']
    ordering_fields = ['date', 'realized_profit']
    ordering = ['-date']
    
    def get_queryset(self):
        """현재 사용자의 계좌 실현 손익만 조회"""
        return DailyRealizedProfit.objects.filter(account__user=self.request.user)
    
    @action(detail=False, methods=['get'])
    def by_account(self, request):
        """계좌별 일일 실현 손익 조회"""
        account_id = request.query_params.get('account_id')
        if not account_id:
            return Response(
                {"detail": "account_id 파라미터가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # 계좌 소유권 확인
        try:
            account = Account.objects.get(id=account_id, user=request.user)
        except Account.DoesNotExist:
            return Response(
                {"detail": "존재하지 않거나 권한이 없는 계좌입니다."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        profits = self.get_queryset().filter(account=account)
        serializer = self.get_serializer(profits, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def by_date(self, request):
        """특정 날짜의 실현 손익 조회 (실시간 계산)"""
        date_str = request.query_params.get('date')
        if not date_str:
            # 날짜가 없으면 오늘 날짜 사용
            target_date = timezone.now().date()
        else:
            try:
                target_date = date_type.fromisoformat(date_str)
            except ValueError:
                return Response(
                    {"detail": "날짜 형식이 올바르지 않습니다. (YYYY-MM-DD)"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # 실시간 계산
        profit_data = ProfitCalculator.get_user_daily_profit(request.user, target_date)
        
        # 계좌별 상세 정보
        accounts = Account.objects.filter(user=request.user)
        account_details = []
        for account in accounts:
            account_profit = ProfitCalculator.calculate_daily_realized_profit(account, target_date)
            account_details.append({
                'account_id': account.id,
                'account_number': account.account_number,
                'broker': account.broker.name,
                'realized_profit': str(account_profit['realized_profit']),
                'total_sell_amount': str(account_profit['total_sell_amount']),
                'realized_profit_rate': str(account_profit['realized_profit_rate']),
            })
        
        return Response({
            'date': target_date.isoformat(),
            'total_realized_profit': str(profit_data['realized_profit']),
            'total_sell_amount': str(profit_data['total_sell_amount']),
            'realized_profit_rate': str(profit_data['realized_profit_rate']),
            'account_count': profit_data['account_count'],
            'accounts': account_details
        })
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """사용자 전체 실현 손익 요약"""
        # 최근 30일 데이터
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=30)
        
        profits = self.get_queryset().filter(date__gte=start_date, date__lte=end_date)
        
        total_profit = sum(Decimal(str(p.realized_profit)) for p in profits)
        total_sell = sum(Decimal(str(p.total_sell_amount)) for p in profits)
        
        avg_profit_rate = Decimal('0')
        if total_sell > 0:
            avg_profit_rate = (total_profit / total_sell) * Decimal('100')
        
        return Response({
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
            },
            'total_realized_profit': str(total_profit),
            'total_sell_amount': str(total_sell),
            'average_profit_rate': str(avg_profit_rate),
            'days_count': profits.values('date').distinct().count()
        })

