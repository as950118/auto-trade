from django.contrib import admin
from .models import (
    Broker, Symbol, Account, Order, Holding, TargetAllocationPlan, ExchangeFeeRebate,
    AlertStrategy, AlertEvent, AlertTradePlan, AlertTradeLeg,
)


@admin.register(Broker)
class BrokerAdmin(admin.ModelAdmin):
    list_display = ['code', 'name', 'country', 'is_crypto_exchange', 'created_at']
    list_filter = ['country', 'is_crypto_exchange']
    search_fields = ['code', 'name']


@admin.register(Symbol)
class SymbolAdmin(admin.ModelAdmin):
    list_display = ['ticker', 'name', 'currency', 'broker', 'is_crypto', 'is_delisted', 'created_at']
    list_filter = ['currency', 'is_crypto', 'is_delisted', 'broker']
    search_fields = ['ticker', 'name']


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'broker', 'account_number', 'total_assets', 
        'cash_balance', 'stock_value', 'profit_rate',
        'buy_enabled', 'sell_enabled', 'created_at'
    ]
    list_filter = ['broker', 'buy_enabled', 'sell_enabled', 'unified_margin']
    search_fields = ['user__username', 'account_number', 'broker__name']
    readonly_fields = [
        'total_assets', 'cash_balance', 'stock_value', 'profit_rate',  # property 필드
        'cash_balance_krw', 'stock_value_krw', 'total_assets_krw',  # 통화별 필드
        'cash_balance_usd', 'stock_value_usd', 'total_assets_usd',  # 통화별 필드
        'access_token', 'refresh_token', 'token_expires_at', 'token_issued_at',
        'created_at', 'updated_at'
    ]
    
    fieldsets = (
        ('기본 정보', {
            'fields': ('user', 'broker', 'account_number', 'account_password')
        }),
        ('API 정보', {
            'fields': ('api_key', 'api_secret', 'access_token', 'refresh_token', 'token_expires_at', 'token_issued_at')
        }),
        ('자산 정보', {
            'fields': (
                'total_assets', 'cash_balance', 'stock_value', 'profit_rate',
                'cash_balance_krw', 'stock_value_krw', 'total_assets_krw',
                'cash_balance_usd', 'stock_value_usd', 'total_assets_usd'
            )
        }),
        ('거래 제한 설정', {
            'fields': ('investment_limit', 'buy_enabled', 'sell_enabled')
        }),
        ('증권사 특화 설정', {
            'fields': ('unified_margin', 'overseas_etp_enabled', 'derivative_etf_enabled')
        }),
        ('시스템 정보', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        'account', 'symbol', 'side', 'order_type', 'quantity', 
        'price', 'status', 'external_order_id', 'filled_quantity', 'average_filled_price', 'created_at'
    ]
    list_filter = ['side', 'order_type', 'status', 'created_at']
    search_fields = ['account__user__username', 'symbol__ticker', 'symbol__name', 'external_order_id']
    readonly_fields = ['created_at', 'updated_at', 'filled_at']
    
    fieldsets = (
        ('주문 정보', {
            'fields': ('account', 'symbol', 'side', 'order_type', 'quantity', 'price')
        }),
        ('체결 정보', {
            'fields': ('status', 'external_order_id', 'filled_quantity', 'average_filled_price', 'filled_at')
        }),
        ('시스템 정보', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    list_display = [
        'account', 'symbol', 'quantity', 'average_price', 'current_price',
        'total_value', 'profit_loss', 'profit_rate', 'updated_at'
    ]
    list_filter = ['account__broker', 'updated_at']
    search_fields = ['account__user__username', 'symbol__ticker', 'symbol__name']
    readonly_fields = ['total_value', 'profit_loss', 'profit_rate', 'created_at', 'updated_at']
    
    fieldsets = (
        ('기본 정보', {
            'fields': ('account', 'symbol')
        }),
        ('보유 정보', {
            'fields': ('quantity', 'average_price', 'current_price')
        }),
        ('평가 정보', {
            'fields': ('total_value', 'profit_loss', 'profit_rate')
        }),
        ('시스템 정보', {
            'fields': ('created_at', 'updated_at')
        }),
    )


@admin.register(TargetAllocationPlan)
class TargetAllocationPlanAdmin(admin.ModelAdmin):
    list_display = [
        'account', 'symbol', 'target_ratio', 'total_days', 'num_trades',
        'trades_done', 'start_date', 'end_date', 'enabled', 'order_type', 'created_at'
    ]
    list_filter = ['enabled', 'order_type', 'created_at']
    search_fields = ['account__user__username', 'symbol__ticker', 'symbol__name']
    readonly_fields = ['trades_done', 'created_at', 'updated_at']
    list_editable = ['enabled']


@admin.register(ExchangeFeeRebate)
class ExchangeFeeRebateAdmin(admin.ModelAdmin):
    list_display = [
        'exchange_name', 'rebate_pct', 'trading_discount_pct',
        'limit_order_fee_pct', 'market_order_fee_pct', 'avg_payback_krw',
        'is_active', 'crawled_at', 'updated_at'
    ]
    list_filter = ['is_active', 'crawled_at']
    search_fields = ['exchange_name']
    list_editable = ['is_active']


@admin.register(AlertStrategy)
class AlertStrategyAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'account', 'seed_amount', 'seed_currency',
        'buy_seed_percent', 'sell_seed_percent', 'max_position_weight_percent',
        'split_count', 'enabled', 'created_at',
    ]
    list_filter = ['enabled', 'seed_currency', 'order_type']
    search_fields = ['name', 'account__user__username', 'webhook_token']
    readonly_fields = ['webhook_token', 'created_at', 'updated_at']
    list_editable = ['enabled']


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'strategy', 'ticker', 'action', 'status',
        'idempotency_key', 'received_at',
    ]
    list_filter = ['status', 'action', 'received_at']
    search_fields = ['ticker', 'idempotency_key', 'strategy__name']
    readonly_fields = ['raw_payload', 'received_at', 'updated_at']


class AlertTradeLegInline(admin.TabularInline):
    model = AlertTradeLeg
    extra = 0
    readonly_fields = ['seq', 'scheduled_at', 'notional', 'quantity', 'order', 'status', 'error_message']


@admin.register(AlertTradePlan)
class AlertTradePlanAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'account', 'symbol', 'side', 'total_notional', 'total_quantity',
        'split_count', 'legs_done', 'status', 'created_at',
    ]
    list_filter = ['status', 'side', 'created_at']
    search_fields = ['symbol__ticker', 'account__user__username']
    inlines = [AlertTradeLegInline]
    readonly_fields = ['legs_done', 'created_at', 'updated_at']
