from urllib.parse import urlencode
import requests
from django.conf import settings
from django.contrib.auth.models import User
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema, OpenApiResponse
from django.db.models import Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from decimal import Decimal
from .models import Order, Account, Symbol, Broker, Country, OrderStatus, DailyRealizedProfit, Holding, TargetAllocationPlan, ExchangeFeeRebate
from .serializers import (
    OrderCreateSerializer, OrderSerializer, OrderUpdateSerializer,
    AccountSerializer, SymbolSerializer, DailyRealizedProfitSerializer,
    UserSerializer, BrokerSerializer, HoldingSerializer, TargetAllocationPlanSerializer,
    ExchangeFeeRebateSerializer,
)
from .profit_calculator import ProfitCalculator
from datetime import date as date_type
from .views_profit import DailyRealizedProfitViewSet


@extend_schema(
    summary='회원가입',
    description='새로운 사용자를 등록합니다.',
    request=UserSerializer,
    responses={
        201: OpenApiResponse(
            description='회원가입 성공',
            response={
                'type': 'object',
                'properties': {
                    'message': {'type': 'string'},
                    'user': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'integer'},
                            'username': {'type': 'string'},
                            'email': {'type': 'string'}
                        }
                    }
                }
            }
        ),
        400: OpenApiResponse(description='잘못된 요청')
    },
    tags=['인증']
)
@api_view(['POST'])
@permission_classes([AllowAny])
def signup(request):
    """회원가입"""
    serializer = UserSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        return Response(
            {
                'message': '회원가입이 완료되었습니다.',
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email
                }
            },
            status=status.HTTP_201_CREATED
        )
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    summary='Google OAuth2 로그인 URL',
    description='Google OAuth2 인증 페이지로 리다이렉트할 URL을 반환합니다.',
    responses={200: {'description': 'auth_url 포함'}, 503: {'description': 'Google OAuth2 미설정'}},
    tags=['인증']
)
@api_view(['GET'])
@permission_classes([AllowAny])
def google_oauth2_login(request):
    """Google OAuth2 로그인 URL 반환"""
    client_id = getattr(settings, 'GOOGLE_OAUTH2_CLIENT_ID', '') or ''
    if not client_id:
        return Response(
            {'detail': 'Google OAuth2가 설정되지 않았습니다. GOOGLE_OAUTH2_CLIENT_ID를 설정해 주세요.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    frontend_url = (getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    redirect_uri = f'{frontend_url}/oauth2/google'
    scope = 'openid email profile'
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': scope,
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return Response({'auth_url': auth_url})


@extend_schema(
    summary='Google OAuth2 콜백',
    description='Google에서 받은 인증 코드로 토큰을 교환하고 JWT를 반환합니다.',
    request={'type': 'object', 'properties': {'code': {'type': 'string'}}, 'required': ['code']},
    responses={
        200: OpenApiResponse(description='access, refresh, user 반환'),
        400: OpenApiResponse(description='code 누락 또는 교환 실패'),
        503: OpenApiResponse(description='Google OAuth2 미설정'),
    },
    tags=['인증']
)
@api_view(['POST'])
@permission_classes([AllowAny])
def google_oauth2_callback(request):
    """Google OAuth2 코드를 JWT로 교환"""
    client_id = getattr(settings, 'GOOGLE_OAUTH2_CLIENT_ID', '') or ''
    client_secret = getattr(settings, 'GOOGLE_OAUTH2_CLIENT_SECRET', '') or ''
    if not client_id or not client_secret:
        return Response(
            {'detail': 'Google OAuth2가 설정되지 않았습니다.'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    code = (request.data.get('code') or request.query_params.get('code') or '').strip()
    if not code:
        return Response({'detail': '인증 코드(code)가 필요합니다.'}, status=status.HTTP_400_BAD_REQUEST)
    frontend_url = (getattr(settings, 'FRONTEND_URL', '') or '').rstrip('/')
    redirect_uri = f'{frontend_url}/oauth2/google'
    token_response = requests.post(
        'https://oauth2.googleapis.com/token',
        data={
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=10,
    )
    if token_response.status_code != 200:
        try:
            google_error = token_response.json()
        except ValueError:
            google_error = {'raw': token_response.text[:500]}
        return Response(
            {
                'detail': 'Google 토큰 교환에 실패했습니다.',
                'redirect_uri': redirect_uri,
                'frontend_url': frontend_url,
                'google_status': token_response.status_code,
                'google_error': google_error,
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    access_token = token_response.json().get('access_token')
    if not access_token:
        return Response(
            {
                'detail': 'Google에서 access_token을 받지 못했습니다.',
                'google_response': token_response.json(),
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    user_response = requests.get(
        'https://www.googleapis.com/oauth2/v2/userinfo',
        headers={'Authorization': f'Bearer {access_token}'},
        timeout=10,
    )
    if user_response.status_code != 200:
        try:
            google_error = user_response.json()
        except ValueError:
            google_error = {'raw': user_response.text[:500]}
        return Response(
            {
                'detail': 'Google 사용자 정보를 가져오지 못했습니다.',
                'google_status': user_response.status_code,
                'google_error': google_error,
            },
            status=status.HTTP_400_BAD_REQUEST
        )
    user_info = user_response.json()
    email = (user_info.get('email') or '').strip()
    if not email:
        return Response(
            {'detail': 'Google 계정에서 이메일을 가져올 수 없습니다.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    # 표시 이름: Google 프로필 name (없으면 given_name, 최후 이메일 로컬파트)
    google_name = (
        (user_info.get('name') or '').strip()
        or (user_info.get('given_name') or '').strip()
        or email.split('@')[0]
    )

    # 이메일로 기존 사용자 찾기 (과거 username=email 로 가입한 경우도 포함)
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        user = User.objects.filter(username=email).first()

    if user is None:
        base_username = email.split('@')[0][:140] or 'user'
        # Django username: 글자/숫자/@/./+/-/_ 만 허용 → 간단히 정리
        safe_base = ''.join(
            c if (c.isalnum() or c in '@.+-_') else '_'
            for c in base_username
        ) or 'user'
        username = safe_base
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f'{safe_base}{counter}'
            counter += 1
        user = User(
            username=username,
            email=email,
            first_name=google_name[:150],
        )
        user.set_unusable_password()
        user.save()
    else:
        # 기존 계정: email/표시이름 보강 (username은 유지)
        updated = False
        if not user.email:
            user.email = email
            updated = True
        if not user.first_name and google_name:
            user.first_name = google_name[:150]
            updated = True
        if updated:
            user.save(update_fields=['email', 'first_name'])

    display_name = (user.first_name or user.username).strip()
    refresh = RefreshToken.for_user(user)
    return Response({
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'user': {
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'display_name': display_name,
        },
    })


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
    filterset_fields = ['broker', 'currency', 'is_crypto', 'is_delisted']
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
        
        # 브로커별 필터링 (매수 시 계좌 선택 후 해당 브로커 종목만 조회)
        broker_id = self.request.query_params.get('broker_id', None)
        if broker_id:
            queryset = queryset.filter(broker_id=broker_id)
        
        # 상장폐지 여부 필터링 (기본값: 상장폐지되지 않은 종목만)
        include_delisted = self.request.query_params.get('include_delisted', 'false')
        if include_delisted.lower() != 'true':
            queryset = queryset.filter(is_delisted=False)
        
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
        
        # 상장폐지 여부 필터링
        include_delisted = request.query_params.get('include_delisted', 'false')
        if include_delisted.lower() != 'true':
            queryset = queryset.filter(is_delisted=False)
        
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
        
        # 상장폐지 여부 필터링
        include_delisted = request.query_params.get('include_delisted', 'false')
        if include_delisted.lower() != 'true':
            queryset = queryset.filter(is_delisted=False)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class BrokerViewSet(viewsets.ReadOnlyModelViewSet):
    """브로커 ViewSet (조회만 가능)"""
    permission_classes = [IsAuthenticated]
    serializer_class = BrokerSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['country', 'is_crypto_exchange']
    search_fields = ['code', 'name']
    ordering_fields = ['name', 'code', 'created_at']
    ordering = ['name']
    
    def get_queryset(self):
        """브로커 조회"""
        queryset = Broker.objects.all()
        
        # 국가별 필터링
        country = self.request.query_params.get('country', None)
        if country:
            queryset = queryset.filter(country=country)
        
        # 암호화폐 거래소만 조회
        is_crypto = self.request.query_params.get('is_crypto', None)
        if is_crypto is not None:
            if is_crypto.lower() == 'true':
                queryset = queryset.filter(is_crypto_exchange=True)
            else:
                queryset = queryset.filter(is_crypto_exchange=False)
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def crypto_exchanges(self, request):
        """암호화폐 거래소만 조회"""
        queryset = self.get_queryset().filter(is_crypto_exchange=True)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def stock_brokers(self, request):
        """증권사만 조회"""
        queryset = self.get_queryset().filter(is_crypto_exchange=False)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class HoldingViewSet(viewsets.ReadOnlyModelViewSet):
    """보유 종목 ViewSet (조회만 가능)"""
    permission_classes = [IsAuthenticated]
    serializer_class = HoldingSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['account', 'symbol']
    search_fields = ['symbol__ticker', 'symbol__name']
    ordering_fields = ['total_value', 'profit_rate', 'updated_at']
    ordering = ['-total_value']
    
    def get_queryset(self):
        """현재 사용자의 보유 종목만 조회"""
        queryset = Holding.objects.filter(account__user=self.request.user)
        
        # 계좌별 필터링
        account_id = self.request.query_params.get('account_id', None)
        if account_id:
            queryset = queryset.filter(account_id=account_id)
        
        # 수량이 0보다 큰 것만 조회 (실제 보유 종목)
        queryset = queryset.filter(quantity__gt=0)
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def by_account(self, request):
        """계좌별 보유 종목 조회"""
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
        
        holdings = self.get_queryset().filter(account=account)
        serializer = self.get_serializer(holdings, many=True)
        return Response(serializer.data)


class TargetAllocationPlanViewSet(viewsets.ModelViewSet):
    """목표 비율 자동매매 계획 ViewSet (CRUD)"""
    permission_classes = [IsAuthenticated]
    serializer_class = TargetAllocationPlanSerializer
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['account', 'symbol', 'enabled']
    ordering_fields = ['created_at', 'start_date', 'end_date']
    ordering = ['-created_at']

    def get_queryset(self):
        return TargetAllocationPlan.objects.filter(account__user=self.request.user)

    def perform_create(self, serializer):
        account = serializer.validated_data.get('account_id') or serializer.validated_data.get('account')
        if account and account.user != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("본인 계좌만 등록할 수 있습니다.")
        serializer.save()


class ExchangeFeeRebateViewSet(viewsets.ReadOnlyModelViewSet):
    """거래소 수수료 환급(페이백) ViewSet (조회만 가능, 비로그인 허용)"""
    permission_classes = [AllowAny]
    serializer_class = ExchangeFeeRebateSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['is_active']
    search_fields = ['exchange_name']
    ordering_fields = ['exchange_name', 'rebate_pct', 'trading_discount_pct', 'avg_payback_krw', 'updated_at']
    ordering = ['-rebate_pct', 'exchange_name']

    def get_queryset(self):
        return ExchangeFeeRebate.objects.filter(is_active=True)
