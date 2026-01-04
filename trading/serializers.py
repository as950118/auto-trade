from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from .models import Order, Account, Symbol, Broker, DailyRealizedProfit, Holding


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
            'total_assets', 'cash_balance', 'stock_value', 'profit_rate',
            # 통화별 필드
            'cash_balance_krw', 'stock_value_krw', 'total_assets_krw',
            'cash_balance_usd', 'stock_value_usd', 'total_assets_usd',
            'investment_limit', 'buy_enabled', 'sell_enabled',
            'unified_margin', 'overseas_etp_enabled', 'derivative_etf_enabled',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'user', 
            # 호환성 필드
            'total_assets', 'cash_balance', 'stock_value', 'profit_rate',
            # 통화별 필드
            'cash_balance_krw', 'stock_value_krw', 'total_assets_krw',
            'cash_balance_usd', 'stock_value_usd', 'total_assets_usd',
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
    """주문 생성용 시리얼라이저"""
    account_id = serializers.IntegerField(write_only=True)
    symbol_id = serializers.IntegerField(write_only=True)
    
    class Meta:
        model = Order
        fields = [
            'account_id', 'symbol_id', 'side', 'order_type', 
            'quantity', 'price'
        ]
    
    def validate(self, attrs):
        """유효성 검사"""
        account_id = attrs.get('account_id')
        symbol_id = attrs.get('symbol_id')
        order_type = attrs.get('order_type')
        price = attrs.get('price')
        
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
        side = attrs.get('side')
        if side == 'BUY' and not account.buy_enabled:
            raise serializers.ValidationError("매수 동작이 비활성화된 계좌입니다.")
        if side == 'SELL' and not account.sell_enabled:
            raise serializers.ValidationError("매도 동작이 비활성화된 계좌입니다.")
        
        return attrs
    
    def create(self, validated_data):
        """주문 생성"""
        account_id = validated_data.pop('account_id')
        symbol_id = validated_data.pop('symbol_id')
        
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
