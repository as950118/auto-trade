from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from .models import Order, Account, Symbol, Broker, Country, OrderStatus
from .serializers import (
    OrderCreateSerializer, OrderSerializer, OrderUpdateSerializer,
    AccountSerializer, SymbolSerializer
)


class AccountViewSet(viewsets.ModelViewSet):
    """계좌 ViewSet"""
    permission_classes = [IsAuthenticated]
    serializer_class = AccountSerializer
    
    def get_queryset(self):
        """현재 사용자의 계좌만 조회"""
        return Account.objects.filter(user=self.request.user)
    
    def perform_create(self, serializer):
        """계좌 생성 시 현재 사용자로 설정"""
        serializer.save(user=self.request.user)
    
    def perform_update(self, serializer):
        """계좌 수정 시 소유권 확인"""
        account = self.get_object()
        if account.user != self.request.user:
            return Response(
                {"detail": "해당 계좌에 대한 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN
            )
        serializer.save()
    
    def perform_destroy(self, instance):
        """계좌 삭제 시 소유권 확인"""
        if instance.user != self.request.user:
            return Response(
                {"detail": "해당 계좌에 대한 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN
            )
        instance.delete()


class OrderViewSet(viewsets.ModelViewSet):
    """주문 ViewSet"""
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['account', 'status', 'side', 'order_type']
    search_fields = ['symbol__ticker', 'symbol__name']
    ordering_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """현재 사용자의 주문만 조회"""
        queryset = Order.objects.filter(account__user=self.request.user)
        
        # 계좌 ID 필터링 (쿼리 파라미터)
        account_id = self.request.query_params.get('account_id', None)
        if account_id:
            queryset = queryset.filter(account_id=account_id)
        
        # 처리 여부 필터링 (쿼리 파라미터)
        is_processed = self.request.query_params.get('is_processed', None)
        if is_processed is not None:
            if is_processed.lower() == 'true':
                # 처리된 주문 (체결완료, 취소됨, 거부됨)
                queryset = queryset.filter(
                    status__in=[OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]
                )
            else:
                # 처리 안된 주문 (대기중, 부분체결)
                queryset = queryset.filter(
                    status__in=[OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED]
                )
        
        return queryset
    
    def get_serializer_class(self):
        """액션에 따라 다른 시리얼라이저 사용"""
        if self.action == 'create':
            return OrderCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return OrderUpdateSerializer
        return OrderSerializer
    
    def create(self, request, *args, **kwargs):
        """주문 생성"""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # 계좌 소유권 확인
        account_id = serializer.validated_data['account_id']
        account = Account.objects.get(id=account_id)
        if account.user != request.user:
            return Response(
                {"detail": "해당 계좌에 대한 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        order = serializer.save()
        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_201_CREATED
        )
    
    def update(self, request, *args, **kwargs):
        """주문 수정"""
        order = self.get_object()
        
        # 소유권 확인
        if order.account.user != request.user:
            return Response(
                {"detail": "해당 주문에 대한 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # 대기중인 주문만 수정 가능
        if order.status != OrderStatus.PENDING:
            return Response(
                {"detail": "대기중인 주문만 수정할 수 있습니다."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = self.get_serializer(order, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        
        return Response(OrderSerializer(order).data)
    
    def destroy(self, request, *args, **kwargs):
        """주문 삭제"""
        order = self.get_object()
        
        # 소유권 확인
        if order.account.user != request.user:
            return Response(
                {"detail": "해당 주문에 대한 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # 대기중인 주문만 삭제 가능
        if order.status != OrderStatus.PENDING:
            return Response(
                {"detail": "대기중인 주문만 삭제할 수 있습니다."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        order.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=False, methods=['get'])
    def by_account(self, request):
        """계좌별 주문 조회"""
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
        
        orders = self.get_queryset().filter(account=account)
        serializer = self.get_serializer(orders, many=True)
        return Response(serializer.data)


class SymbolViewSet(viewsets.ReadOnlyModelViewSet):
    """종목 ViewSet (조회만 가능)"""
    permission_classes = [IsAuthenticated]
    serializer_class = SymbolSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['broker', 'currency', 'is_crypto']
    search_fields = ['ticker', 'name']
    ordering_fields = ['ticker', 'name', 'created_at']
    ordering = ['ticker']
    
    def get_queryset(self):
        """종목 조회"""
        queryset = Symbol.objects.all()
        
        # 국가별 필터링
        country = self.request.query_params.get('country', None)
        if country:
            brokers = Broker.objects.filter(country=country)
            queryset = queryset.filter(broker__in=brokers)
        
        # 주식 종목만 조회
        is_stock = self.request.query_params.get('is_stock', None)
        if is_stock is not None:
            if is_stock.lower() == 'true':
                queryset = queryset.filter(is_crypto=False)
            else:
                queryset = queryset.filter(is_crypto=True)
        
        # 암호화폐 종목만 조회
        is_crypto = self.request.query_params.get('is_crypto', None)
        if is_crypto is not None:
            if is_crypto.lower() == 'true':
                queryset = queryset.filter(is_crypto=True)
            else:
                queryset = queryset.filter(is_crypto=False)
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def stocks(self, request):
        """주식 종목 조회 (국가별 필터링 가능)"""
        queryset = self.get_queryset().filter(is_crypto=False)
        
        # 국가별 필터링
        country = request.query_params.get('country', None)
        if country:
            brokers = Broker.objects.filter(country=country, is_crypto_exchange=False)
            queryset = queryset.filter(broker__in=brokers)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def cryptos(self, request):
        """암호화폐 종목 조회"""
        queryset = self.get_queryset().filter(is_crypto=True)
        
        # 브로커별 필터링
        broker_id = request.query_params.get('broker_id', None)
        if broker_id:
            queryset = queryset.filter(broker_id=broker_id)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
