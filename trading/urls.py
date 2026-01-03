from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import OrderViewSet, AccountViewSet, SymbolViewSet, DailyRealizedProfitViewSet

router = DefaultRouter()
router.register(r'accounts', AccountViewSet, basename='account')
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'symbols', SymbolViewSet, basename='symbol')
router.register(r'daily-profits', DailyRealizedProfitViewSet, basename='daily-profit')

urlpatterns = [
    # JWT 인증
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # API 라우터
    path('api/', include(router.urls)),
]
