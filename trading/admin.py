from django.contrib import admin
from .models import Broker, Symbol, Account, Order


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
    readonly_fields = ['total_assets', 'cash_balance', 'stock_value', 'profit_rate', 'created_at', 'updated_at']
    
    fieldsets = (
        ('기본 정보', {
            'fields': ('user', 'broker', 'account_number', 'account_password')
        }),
        ('API 정보', {
            'fields': ('api_key', 'api_secret')
        }),
        ('자산 정보', {
            'fields': ('total_assets', 'cash_balance', 'stock_value', 'profit_rate')
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
