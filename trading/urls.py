from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import OrderViewSet, AccountViewSet, SymbolViewSet, DailyRealizedProfitViewSet, BrokerViewSet, HoldingViewSet, signup
from .oauth2_views import google_oauth2_login, google_oauth2_callback

router = DefaultRouter()
router.register(r'accounts', AccountViewSet, basename='account')
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'symbols', SymbolViewSet, basename='symbol')
router.register(r'brokers', BrokerViewSet, basename='broker')
router.register(r'holdings', HoldingViewSet, basename='holding')
router.register(r'daily-profits', DailyRealizedProfitViewSet, basename='daily-profit')

urlpatterns = [
    # 회원가입
    path('api/users/', signup, name='signup'),
    
    # JWT 인증
    path('api/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    # Google OAuth2 인증
    path('api/oauth2/google/login/', google_oauth2_login, name='google_oauth2_login'),
    path('api/oauth2/google/callback/', google_oauth2_callback, name='google_oauth2_callback'),
    
    # API 라우터
    path('api/', include(router.urls)),
]
