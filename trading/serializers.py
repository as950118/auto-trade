from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from .models import (
    Order, Account, Symbol, Broker, DailyRealizedProfit, Holding,
    TargetAllocationPlan, ExchangeFeeRebate,
    Strategy, StrategyLink, StrategyVisibility, AlertEvent, AlertTradePlan, AlertTradeLeg,
    Portfolio, PortfolioHolding, PortfolioLink, PortfolioVisibility,
)
from .sell_quantity import (
    resolve_sell_quantity,
    QUANTITY_TYPE_EXACT,
    QUANTITY_TYPE_MAX,
    QUANTITY_TYPE_PERCENT,
    QUANTITY_TYPE_AMOUNT,
    QUANTITY_TYPES,
)
from .buy_quantity import resolve_buy_quantity


class UserSerializer(serializers.ModelSerializer):
    """사용자 시리얼라이저"""
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True, label='비밀번호 확인')
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password', 'password2']
        extra_kwargs = {
            'email': {'required': True}
        }
    
    def validate(self, attrs):
        """비밀번호 일치 확인"""
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "비밀번호가 일치하지 않습니다."})
        return attrs
    
    def create(self, validated_data):
        """사용자 생성"""
        validated_data.pop('password2')
        user = User.objects.create(
            username=validated_data['username'],
            email=validated_data.get('email', ''),
        )
        user.set_password(validated_data['password'])
        user.save()
        return user


class MeSerializer(serializers.ModelSerializer):
    """현재 로그인 사용자 프로필"""
    display_name = serializers.SerializerMethodField()
    has_usable_password = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'display_name',
            'has_usable_password',
            'is_staff',
        ]

    def get_display_name(self, obj):
        return (obj.first_name or obj.username or '').strip()

    def get_has_usable_password(self, obj):
        return obj.has_usable_password()


class SetPasswordSerializer(serializers.Serializer):
    """비밀번호 설정/변경 (로그인 필수)"""
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)
    current_password = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        help_text='이미 비밀번호가 있는 계정은 현재 비밀번호 필수',
    )

    def validate(self, attrs):
        user = self.context['request'].user
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({'password': '비밀번호가 일치하지 않습니다.'})
        if user.has_usable_password():
            current = attrs.get('current_password') or ''
            if not current:
                raise serializers.ValidationError(
                    {'current_password': '현재 비밀번호를 입력해 주세요.'}
                )
            if not user.check_password(current):
                raise serializers.ValidationError(
                    {'current_password': '현재 비밀번호가 올바르지 않습니다.'}
                )
        return attrs

    def save(self, **kwargs):
        user = self.context['request'].user
        user.set_password(self.validated_data['password'])
        user.save(update_fields=['password'])
        return user


class BrokerSerializer(serializers.ModelSerializer):
    """브로커 시리얼라이저"""
    country_display = serializers.CharField(source='get_country_display', read_only=True)
    
    class Meta:
        model = Broker
        fields = ['id', 'code', 'name', 'country', 'country_display', 'is_crypto_exchange', 'created_at', 'updated_at']


class SymbolSerializer(serializers.ModelSerializer):
    """종목 시리얼라이저"""
    broker = BrokerSerializer(read_only=True)
    broker_id = serializers.IntegerField(write_only=True, required=False)
    currency_display = serializers.CharField(source='get_currency_display', read_only=True)
    
    class Meta:
        model = Symbol
        fields = [
            'id', 'ticker', 'name', 'currency', 'currency_display', 
            'broker', 'broker_id', 'is_crypto', 'is_delisted', 
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_at', 'updated_at']


class AccountSerializer(serializers.ModelSerializer):
    """계좌 시리얼라이저"""
    broker = BrokerSerializer(read_only=True)
    broker_id = serializers.IntegerField(write_only=True)
    user = serializers.StringRelatedField(read_only=True)
    
    class Meta:
        model = Account
        fields = [
            'id', 'user', 'broker', 'broker_id', 'account_number', 
            'account_password', 'api_key', 'api_secret',
            'access_token', 'refresh_token', 'token_expires_at', 'token_issued_at',
            # 호환성 필드 (원화 기준)
            'total_assets', 'cash_balance', 'stock_value', 'profit_rate', 'profit_loss',
            # 통화별 자산
            'cash_balance_krw', 'stock_value_krw', 'total_assets_krw',
            'cash_balance_usd', 'stock_value_usd', 'total_assets_usd',
            # 통화별 손익
            'profit_loss_krw', 'profit_rate_krw',
            'profit_loss_usd', 'profit_rate_usd',
            'investment_limit', 'buy_enabled', 'sell_enabled',
            'unified_margin', 'overseas_etp_enabled', 'derivative_etf_enabled',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'user', 
            # 호환성 필드
            'total_assets', 'cash_balance', 'stock_value', 'profit_rate', 'profit_loss',
            # 통화별 자산
            'cash_balance_krw', 'stock_value_krw', 'total_assets_krw',
            'cash_balance_usd', 'stock_value_usd', 'total_assets_usd',
            # 통화별 손익
            'profit_loss_krw', 'profit_rate_krw',
            'profit_loss_usd', 'profit_rate_usd',
            'access_token', 'refresh_token', 'token_expires_at', 
            'token_issued_at', 'created_at', 'updated_at'
        ]
        extra_kwargs = {
            'account_number': {'required': False, 'allow_blank': True, 'allow_null': True},
            'account_password': {'write_only': True, 'required': False, 'allow_blank': True, 'allow_null': True},
            'api_key': {'write_only': True},
            'api_secret': {'write_only': True},
        }
    
    def validate_broker_id(self, value):
        """브로커 존재 확인"""
        try:
            broker = Broker.objects.get(id=value)
        except Broker.DoesNotExist:
            raise serializers.ValidationError("존재하지 않는 브로커입니다.")
        return value
    
    def validate(self, attrs):
        """계좌번호 및 계좌비밀번호 유효성 검사"""
        broker_id = attrs.get('broker_id')
        account_number = attrs.get('account_number', '')
        account_password = attrs.get('account_password', '')
        
        if broker_id:
            try:
                broker = Broker.objects.get(id=broker_id)
                # 암호화폐 거래소가 아닌 경우 계좌번호 필수
                if not broker.is_crypto_exchange:
                    if not account_number:
                        raise serializers.ValidationError({
                            'account_number': '증권사 계좌는 계좌번호가 필수입니다.'
                        })
            except Broker.DoesNotExist:
                pass  # broker_id validation에서 이미 처리됨
        
        return attrs


class DailyRealizedProfitSerializer(serializers.ModelSerializer):
    """일일 실현 손익 시리얼라이저"""
    account = AccountSerializer(read_only=True)
    
    class Meta:
        model = DailyRealizedProfit
        fields = [
            'id', 'account', 'date', 'realized_profit', 'realized_profit_rate',
            'total_buy_amount', 'total_sell_amount', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class OrderCreateSerializer(serializers.ModelSerializer):
    """
    주문 생성용 시리얼라이저.
    - 매수/매도 공통: quantity(직접 수량) 또는 quantity_type + quantity_value
    - quantity_type: EXACT(직접 수량), MAX(전량/예수금 전액), PERCENT(비율%), AMOUNT(금액)
    - order_type: MARKET(시장가), LIMIT(지정가, price 필수)
    - 매수 시 전량/비율/금액 사용 시: 지정가면 price가 기준가, 시장가면 price는 수량 계산용 예상가(reference)
    """
    account_id = serializers.IntegerField(write_only=True)
    symbol_id = serializers.IntegerField(write_only=True)
    quantity = serializers.DecimalField(
        max_digits=20,
        decimal_places=8,
        min_value=0,
        required=False,
        allow_null=True,
        help_text='수량. 매도에서 quantity_type 사용 시 생략 가능.'
    )
    quantity_type = serializers.ChoiceField(
        choices=QUANTITY_TYPES,
        required=False,
        allow_blank=True,
        help_text='수량 지정 방식: EXACT(직접 수량), MAX(전량/예수금 전액), PERCENT(10/50 등), AMOUNT(금액)'
    )
    quantity_value = serializers.DecimalField(
        max_digits=20,
        decimal_places=8,
        min_value=0,
        required=False,
        allow_null=True,
        help_text='PERCENT일 때 비율(10, 50 등), AMOUNT일 때 금액(KRW/USD 등)'
    )
    price = serializers.DecimalField(
        max_digits=20,
        decimal_places=2,
        min_value=0,
        required=False,
        allow_null=True,
        help_text='지정가(LIMIT) 시 필수. 시장가(MARKET) 매수에서 전량/비율/금액 사용 시 수량 계산용 예상가.'
    )

    class Meta:
        model = Order
        fields = [
            'account_id', 'symbol_id', 'side', 'order_type',
            'quantity', 'quantity_type', 'quantity_value', 'price'
        ]
    
    def validate(self, attrs):
        """유효성 검사"""
        account_id = attrs.get('account_id')
        symbol_id = attrs.get('symbol_id')
        order_type = attrs.get('order_type')
        price = attrs.get('price')
        side = attrs.get('side')
        quantity = attrs.get('quantity')
        quantity_type = (attrs.get('quantity_type') or '').strip() or None
        quantity_value = attrs.get('quantity_value')
        
        # 계좌 존재 확인
        try:
            account = Account.objects.get(id=account_id)
        except Account.DoesNotExist:
            raise serializers.ValidationError("존재하지 않는 계좌입니다.")
        
        # 종목 존재 확인
        try:
            symbol = Symbol.objects.get(id=symbol_id)
        except Symbol.DoesNotExist:
            raise serializers.ValidationError("존재하지 않는 종목입니다.")
        
        # 지정가 주문인 경우 가격 필수
        if order_type == 'LIMIT' and not price:
            raise serializers.ValidationError("지정가 주문은 가격이 필수입니다.")
        
        # 매수/매도 동작 여부 확인
        if side == 'BUY' and not account.buy_enabled:
            raise serializers.ValidationError("매수 동작이 비활성화된 계좌입니다.")
        if side == 'SELL' and not account.sell_enabled:
            raise serializers.ValidationError("매도 동작이 비활성화된 계좌입니다.")
        
        # 매도 시 수량 해석: quantity_type 사용 시 resolve_sell_quantity 호출
        if side == 'SELL' and quantity_type:
            if quantity_type == QUANTITY_TYPE_PERCENT and quantity_value is None:
                raise serializers.ValidationError({
                    'quantity_value': '비율(%) 지정 시 quantity_value(예: 10, 50)가 필요합니다.'
                })
            if quantity_type == QUANTITY_TYPE_AMOUNT and quantity_value is None:
                raise serializers.ValidationError({
                    'quantity_value': '금액 지정 시 quantity_value(금액)가 필요합니다.'
                })
            value_for_resolve = quantity_value
            if quantity_type == QUANTITY_TYPE_EXACT and value_for_resolve is None and quantity is not None:
                value_for_resolve = quantity
            if quantity_type == QUANTITY_TYPE_EXACT and value_for_resolve is None:
                raise serializers.ValidationError({
                    'quantity': '직접 수량(EXACT) 지정 시 quantity 또는 quantity_value가 필요합니다.'
                })
            try:
                resolved = resolve_sell_quantity(
                    account=account,
                    symbol=symbol,
                    quantity_type=quantity_type,
                    quantity_value=float(value_for_resolve) if value_for_resolve is not None else None,
                    order_type=order_type,
                    limit_price=Decimal(str(price)) if price else None,
                )
                attrs['quantity'] = resolved
            except DjangoValidationError as e:
                msg = getattr(e, 'messages', None)
                if msg and isinstance(msg, list) and msg:
                    raise serializers.ValidationError(msg[0] if len(msg) == 1 else msg)
                raise serializers.ValidationError(str(e))
            # API 응답용이 아닌 주문 생성용이므로 quantity_type/value 제거는 create에서
        elif side == 'SELL' and not quantity_type and (quantity is None or quantity <= 0):
            raise serializers.ValidationError({
                'quantity': '매도 시 수량(quantity) 또는 quantity_type을 지정해 주세요.'
            })
        # 매수 시 수량 해석: quantity_type 사용 시 resolve_buy_quantity 호출
        elif side == 'BUY' and quantity_type:
            if quantity_type == QUANTITY_TYPE_PERCENT and quantity_value is None:
                raise serializers.ValidationError({
                    'quantity_value': '비율(%) 지정 시 quantity_value(예: 10, 50)가 필요합니다.'
                })
            if quantity_type == QUANTITY_TYPE_AMOUNT and quantity_value is None:
                raise serializers.ValidationError({
                    'quantity_value': '금액 지정 시 quantity_value(금액)가 필요합니다.'
                })
            value_for_resolve = quantity_value
            if quantity_type == QUANTITY_TYPE_EXACT and value_for_resolve is None and quantity is not None:
                value_for_resolve = quantity
            if quantity_type == QUANTITY_TYPE_EXACT and value_for_resolve is None:
                raise serializers.ValidationError({
                    'quantity': '직접 수량(EXACT) 지정 시 quantity 또는 quantity_value가 필요합니다.'
                })
            # 시장가 + 전량/비율/금액 이면 수량 계산을 위해 기준가(price) 필요
            if order_type == 'MARKET' and quantity_type in (QUANTITY_TYPE_MAX, QUANTITY_TYPE_PERCENT, QUANTITY_TYPE_AMOUNT):
                if not price or price <= 0:
                    raise serializers.ValidationError({
                        'price': '시장가 매수에서 전량/비율/금액 지정 시 수량 계산을 위한 예상가(price)가 필요합니다.'
                    })
            try:
                resolved = resolve_buy_quantity(
                    account=account,
                    symbol=symbol,
                    quantity_type=quantity_type,
                    quantity_value=float(value_for_resolve) if value_for_resolve is not None else None,
                    order_type=order_type,
                    limit_price=Decimal(str(price)) if (order_type == 'LIMIT' and price) else None,
                    reference_price=Decimal(str(price)) if (order_type == 'MARKET' and price) else None,
                )
                attrs['quantity'] = resolved
                # 시장가 매수에서 price는 수량 계산용만 사용; DB에는 저장하지 않음(지정가만 price 저장)
                if order_type == 'MARKET' and quantity_type in (QUANTITY_TYPE_MAX, QUANTITY_TYPE_PERCENT, QUANTITY_TYPE_AMOUNT):
                    attrs['price'] = None
            except DjangoValidationError as e:
                msg = getattr(e, 'messages', None)
                if msg and isinstance(msg, list) and msg:
                    raise serializers.ValidationError(msg[0] if len(msg) == 1 else msg)
                raise serializers.ValidationError(str(e))
        elif side == 'BUY' and (quantity is None or quantity <= 0):
            raise serializers.ValidationError({'quantity': '매수 수량(quantity) 또는 quantity_type을 지정해 주세요.'})
        
        return attrs
    
    def create(self, validated_data):
        """주문 생성"""
        account_id = validated_data.pop('account_id')
        symbol_id = validated_data.pop('symbol_id')
        validated_data.pop('quantity_type', None)
        validated_data.pop('quantity_value', None)
        account = Account.objects.get(id=account_id)
        symbol = Symbol.objects.get(id=symbol_id)
        
        order = Order.objects.create(
            account=account,
            symbol=symbol,
            **validated_data
        )
        return order


class OrderUpdateSerializer(serializers.ModelSerializer):
    """주문 수정용 시리얼라이저"""
    class Meta:
        model = Order
        fields = ['quantity', 'price']
    
    def validate(self, attrs):
        """유효성 검사"""
        order = self.instance
        if order.status != 'PENDING':
            raise serializers.ValidationError("대기중인 주문만 수정할 수 있습니다.")
        return attrs


class HoldingSerializer(serializers.ModelSerializer):
    """보유 종목 시리얼라이저"""
    account = AccountSerializer(read_only=True)
    symbol = SymbolSerializer(read_only=True)
    
    class Meta:
        model = Holding
        fields = [
            'id', 'account', 'symbol', 'quantity', 'average_price', 'current_price',
            'total_value', 'profit_loss', 'profit_rate', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'account', 'symbol', 'total_value', 'profit_loss', 'profit_rate',
            'created_at', 'updated_at'
        ]


class OrderSerializer(serializers.ModelSerializer):
    """주문 조회용 시리얼라이저"""
    account = AccountSerializer(read_only=True)
    symbol = SymbolSerializer(read_only=True)
    side_display = serializers.CharField(source='get_side_display', read_only=True)
    order_type_display = serializers.CharField(source='get_order_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = Order
        fields = '__all__'
        read_only_fields = [
            'id', 'account', 'symbol', 'status', 'external_order_id', 
            'filled_quantity', 'average_filled_price',
            'created_at', 'updated_at', 'filled_at'
        ]


class TargetAllocationPlanSerializer(serializers.ModelSerializer):
    """목표 비율 자동매매 계획 시리얼라이저"""
    account = AccountSerializer(read_only=True)
    account_id = serializers.PrimaryKeyRelatedField(queryset=Account.objects.none(), write_only=True)
    symbol = SymbolSerializer(read_only=True)
    symbol_id = serializers.PrimaryKeyRelatedField(queryset=Symbol.objects.all(), write_only=True)
    order_type_display = serializers.CharField(source='get_order_type_display', read_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            self.fields['account_id'].queryset = Account.objects.filter(user=request.user)

    class Meta:
        model = TargetAllocationPlan
        fields = [
            'id', 'account', 'account_id', 'symbol', 'symbol_id',
            'target_ratio', 'total_days', 'num_trades', 'trades_done',
            'start_date', 'end_date', 'enabled', 'order_type', 'order_type_display',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'trades_done', 'created_at', 'updated_at']

    def validate_target_ratio(self, value):
        if value is not None and (value < 0 or value > 1):
            raise serializers.ValidationError('목표 비율은 0~1 사이여야 합니다. (예: 0.2 = 20%)')
        return value

    def create(self, validated_data):
        validated_data['account'] = validated_data.pop('account_id')
        validated_data['symbol'] = validated_data.pop('symbol_id')
        return TargetAllocationPlan.objects.create(**validated_data)

    def update(self, instance, validated_data):
        validated_data.pop('account_id', None)
        validated_data.pop('symbol_id', None)
        return super().update(instance, validated_data)


class ExchangeFeeRebateSerializer(serializers.ModelSerializer):
    """거래소 수수료 환급(페이백) 시리얼라이저 (조회 전용)"""
    class Meta:
        model = ExchangeFeeRebate
        fields = [
            'id', 'exchange_name', 'rebate_pct', 'trading_discount_pct',
            'limit_order_fee_pct', 'market_order_fee_pct', 'avg_payback_krw',
            'tags', 'source_url', 'crawled_at', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'exchange_name', 'rebate_pct', 'trading_discount_pct',
            'limit_order_fee_pct', 'market_order_fee_pct', 'avg_payback_krw',
            'tags', 'source_url', 'crawled_at', 'is_active',
            'created_at', 'updated_at',
        ]


class StrategySerializer(serializers.ModelSerializer):
    """전략 템플릿 시리얼라이저"""
    owner_username = serializers.CharField(source='owner.username', read_only=True)
    visibility_display = serializers.CharField(source='get_visibility_display', read_only=True)
    order_type_display = serializers.CharField(source='get_order_type_display', read_only=True)
    webhook_url = serializers.SerializerMethodField()

    class Meta:
        model = Strategy
        fields = [
            'id', 'owner', 'owner_username', 'title', 'description', 'visibility',
            'visibility_display', 'webhook_token', 'webhook_url', 'webhook_passphrase',
            'default_max_position_weight_percent', 'default_trade_percent',
            'default_split_count', 'default_split_interval_seconds',
            'order_type', 'order_type_display', 'enabled', 'allowed_tickers',
            'cooldown_seconds', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'owner', 'webhook_token', 'created_at', 'updated_at']
        extra_kwargs = {
            'webhook_passphrase': {'write_only': True, 'required': False, 'allow_null': True},
        }

    def get_webhook_url(self, obj):
        request = self.context.get('request')
        path = f'/api/webhooks/tradingview/{obj.webhook_token}/'
        if request:
            return request.build_absolute_uri(path)
        return path

    def validate_visibility(self, value):
        request = self.context.get('request')
        if value == StrategyVisibility.PUBLIC and request and not request.user.is_staff:
            raise serializers.ValidationError('공개 전략은 관리자만 생성할 수 있습니다.')
        return value

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['owner'] = request.user
        if not request.user.is_staff:
            validated_data['visibility'] = StrategyVisibility.PRIVATE
        return Strategy.objects.create(**validated_data)


class StrategyLinkSerializer(serializers.ModelSerializer):
    """전략 연동 시리얼라이저"""
    strategy = StrategySerializer(read_only=True)
    strategy_id = serializers.PrimaryKeyRelatedField(
        queryset=Strategy.objects.all(), write_only=True, source='strategy'
    )
    account = AccountSerializer(read_only=True)
    account_id = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.none(), write_only=True, source='account'
    )
    effective_max_position_weight_percent = serializers.DecimalField(
        max_digits=8, decimal_places=4, read_only=True
    )
    effective_trade_percent = serializers.DecimalField(
        max_digits=8, decimal_places=4, read_only=True
    )
    effective_split_count = serializers.IntegerField(read_only=True)
    effective_split_interval_seconds = serializers.IntegerField(read_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            self.fields['account_id'].queryset = Account.objects.filter(user=request.user)
            self.fields['strategy_id'].queryset = Strategy.objects.filter(
                Q(visibility=StrategyVisibility.PUBLIC) | Q(owner=request.user)
            )

    class Meta:
        model = StrategyLink
        fields = [
            'id', 'strategy', 'strategy_id', 'account', 'account_id',
            'seed_amount', 'seed_currency',
            'max_position_weight_percent', 'trade_percent',
            'split_count', 'split_interval_seconds', 'enabled',
            'effective_max_position_weight_percent', 'effective_trade_percent',
            'effective_split_count', 'effective_split_interval_seconds',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        request = self.context.get('request')
        account = attrs.get('account') or getattr(self.instance, 'account', None)
        strategy = attrs.get('strategy') or getattr(self.instance, 'strategy', None)
        if account and request and account.user != request.user:
            raise serializers.ValidationError({'account_id': '본인 계좌만 연동할 수 있습니다.'})
        if strategy and request:
            if strategy.visibility != StrategyVisibility.PUBLIC and strategy.owner_id != request.user.id:
                raise serializers.ValidationError({'strategy_id': '연동할 수 없는 전략입니다.'})
        return attrs


class PortfolioHoldingSerializer(serializers.ModelSerializer):
    """포트폴리오 구성 종목 시리얼라이저 (Portfolio.holdings 벌크 교체용)"""
    symbol = SymbolSerializer(read_only=True)
    symbol_id = serializers.PrimaryKeyRelatedField(
        queryset=Symbol.objects.all(), write_only=True, source='symbol'
    )

    class Meta:
        model = PortfolioHolding
        fields = ['id', 'symbol', 'symbol_id', 'target_weight_percent', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_target_weight_percent(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError('목표 비중은 0~100 사이여야 합니다.')
        return value


class PortfolioSerializer(serializers.ModelSerializer):
    """포트폴리오 시리얼라이저"""
    owner_username = serializers.CharField(source='owner.username', read_only=True)
    visibility_display = serializers.CharField(source='get_visibility_display', read_only=True)
    order_type_display = serializers.CharField(source='get_order_type_display', read_only=True)
    holdings = PortfolioHoldingSerializer(many=True, read_only=True)

    class Meta:
        model = Portfolio
        fields = [
            'id', 'owner', 'owner_username', 'title', 'description', 'visibility',
            'visibility_display', 'order_type', 'order_type_display', 'enabled',
            'holdings', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'owner', 'created_at', 'updated_at']

    def validate_visibility(self, value):
        request = self.context.get('request')
        if value == PortfolioVisibility.PUBLIC and request and not request.user.is_staff:
            raise serializers.ValidationError('공개 포트폴리오는 관리자만 생성할 수 있습니다.')
        return value

    def create(self, validated_data):
        request = self.context.get('request')
        validated_data['owner'] = request.user
        if not request.user.is_staff:
            validated_data['visibility'] = PortfolioVisibility.PRIVATE
        return Portfolio.objects.create(**validated_data)


class PortfolioLinkSerializer(serializers.ModelSerializer):
    """포트폴리오 연동(구독) 시리얼라이저"""
    portfolio = PortfolioSerializer(read_only=True)
    portfolio_id = serializers.PrimaryKeyRelatedField(
        queryset=Portfolio.objects.all(), write_only=True, source='portfolio'
    )
    account = AccountSerializer(read_only=True)
    account_id = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.none(), write_only=True, source='account'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            self.fields['account_id'].queryset = Account.objects.filter(user=request.user)
            self.fields['portfolio_id'].queryset = Portfolio.objects.filter(
                Q(visibility=PortfolioVisibility.PUBLIC) | Q(owner=request.user)
            )

    class Meta:
        model = PortfolioLink
        fields = [
            'id', 'portfolio', 'portfolio_id', 'account', 'account_id',
            'seed_amount', 'seed_currency', 'enabled',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        request = self.context.get('request')
        account = attrs.get('account') or getattr(self.instance, 'account', None)
        portfolio = attrs.get('portfolio') or getattr(self.instance, 'portfolio', None)
        seed_currency = attrs.get('seed_currency') or getattr(self.instance, 'seed_currency', None)

        if account and request and account.user != request.user:
            raise serializers.ValidationError({'account_id': '본인 계좌만 연동할 수 있습니다.'})
        if portfolio and request:
            if portfolio.visibility != PortfolioVisibility.PUBLIC and portfolio.owner_id != request.user.id:
                raise serializers.ValidationError({'portfolio_id': '구독할 수 없는 포트폴리오입니다.'})
        if portfolio and seed_currency:
            portfolio_currencies = set(
                portfolio.holdings.values_list('symbol__currency', flat=True)
            )
            if portfolio_currencies and seed_currency not in portfolio_currencies:
                raise serializers.ValidationError(
                    {'seed_currency': '포트폴리오 종목 통화와 시드 통화가 일치해야 합니다.'}
                )
        return attrs


class AlertTradeLegSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertTradeLeg
        fields = [
            'id', 'seq', 'scheduled_at', 'notional', 'quantity',
            'order', 'status', 'error_message', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class AlertTradePlanSerializer(serializers.ModelSerializer):
    account = AccountSerializer(read_only=True)
    symbol = SymbolSerializer(read_only=True)
    legs = AlertTradeLegSerializer(many=True, read_only=True)
    side_display = serializers.CharField(source='get_side_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = AlertTradePlan
        fields = [
            'id', 'event', 'strategy_link', 'account', 'symbol', 'side', 'side_display',
            'total_notional', 'total_quantity', 'reference_price',
            'split_count', 'split_interval_seconds', 'order_type',
            'status', 'status_display', 'legs_done', 'legs',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class AlertEventSerializer(serializers.ModelSerializer):
    strategy_title = serializers.CharField(source='strategy.title', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    trade_plans = AlertTradePlanSerializer(many=True, read_only=True)

    class Meta:
        model = AlertEvent
        fields = [
            'id', 'strategy', 'strategy_title', 'raw_payload', 'ticker', 'action',
            'idempotency_key', 'status', 'status_display', 'reject_reason',
            'trade_plans', 'received_at', 'updated_at',
        ]
        read_only_fields = fields
