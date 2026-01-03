from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Order, Account, Symbol, Broker, DailyRealizedProfit


class BrokerSerializer(serializers.ModelSerializer):
    """브로커 시리얼라이저"""
    country_display = serializers.CharField(source='get_country_display', read_only=True)
    
    class Meta:
        model = Broker
        fields = ['id', 'name', 'country', 'country_display', 'is_crypto_exchange', 'created_at', 'updated_at']


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
            'total_assets', 'cash_balance', 'stock_value', 'profit_rate',
            'investment_limit', 'buy_enabled', 'sell_enabled',
            'unified_margin', 'overseas_etp_enabled', 'derivative_etf_enabled',
            'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'user', 'total_assets', 'cash_balance', 'stock_value', 
            'profit_rate', 'created_at', 'updated_at'
        ]
        extra_kwargs = {
            'account_password': {'write_only': True},
            'api_key': {'write_only': True},
            'api_secret': {'write_only': True},
        }
    
    def validate_broker_id(self, value):
        """브로커 존재 확인"""
        try:
            Broker.objects.get(id=value)
        except Broker.DoesNotExist:
            raise serializers.ValidationError("존재하지 않는 브로커입니다.")
        return value


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
