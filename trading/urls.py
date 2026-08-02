from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import (
    OrderViewSet, AccountViewSet, SymbolViewSet, DailyRealizedProfitViewSet,
    BrokerViewSet, HoldingViewSet, TargetAllocationPlanViewSet, ExchangeFeeRebateViewSet,
    StrategyViewSet, StrategyLinkViewSet, AlertEventViewSet, AlertTradePlanViewSet, signup,
    PortfolioViewSet, PortfolioLinkViewSet, CongressMemberViewSet,
)
from .oauth2_views import google_oauth2_login, google_oauth2_callback
from .webhooks import tradingview_webhook

router = DefaultRouter()
router.register(r'accounts', AccountViewSet, basename='account')
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'symbols', SymbolViewSet, basename='symbol')
router.register(r'brokers', BrokerViewSet, basename='broker')
router.register(r'holdings', HoldingViewSet, basename='holding')
router.register(r'target-allocation-plans', TargetAllocationPlanViewSet, basename='target-allocation-plan')
router.register(r'daily-profits', DailyRealizedProfitViewSet, basename='daily-profit')
router.register(r'fee-rebates', ExchangeFeeRebateViewSet, basename='fee-rebate')
router.register(r'strategies', StrategyViewSet, basename='strategy')
router.register(r'strategy-links', StrategyLinkViewSet, basename='strategy-link')
router.register(r'alert-events', AlertEventViewSet, basename='alert-event')
router.register(r'alert-trade-plans', AlertTradePlanViewSet, basename='alert-trade-plan')
router.register(r'portfolios', PortfolioViewSet, basename='portfolio')
router.register(r'portfolio-links', PortfolioLinkViewSet, basename='portfolio-link')
router.register(r'congress-members', CongressMemberViewSet, basename='congress-member')

urlpatterns = [
    path('api/users/', signup, name='signup'),
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/oauth2/google/login/', google_oauth2_login),
    path('api/oauth2/google/callback/', google_oauth2_callback),
    path(
        'api/webhooks/tradingview/<str:webhook_token>/',
        tradingview_webhook,
        name='tradingview-webhook',
    ),
    path('api/', include(router.urls)),
]
