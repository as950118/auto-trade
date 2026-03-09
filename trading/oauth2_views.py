"""
Google OAuth2 인증 뷰
"""
import requests
from django.conf import settings
from django.contrib.auth.models import User
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema, OpenApiResponse


# Google OAuth2 설정
GOOGLE_CLIENT_ID = ''
GOOGLE_CLIENT_SECRET = ''
GOOGLE_REDIRECT_URI = 'http://localhost:3000/oauth2/google'
GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USER_INFO_URL = 'https://www.googleapis.com/oauth2/v2/userinfo'


@extend_schema(
    summary='Google OAuth2 인증 URL 생성',
    description='Google OAuth2 로그인을 위한 인증 URL을 생성합니다.',
    responses={
        200: OpenApiResponse(
            description='인증 URL 생성 성공',
            response={
                'type': 'object',
                'properties': {
                    'auth_url': {'type': 'string', 'description': 'Google OAuth2 인증 URL'}
                }
            }
        )
    },
    tags=['인증']
)
@api_view(['GET'])
@permission_classes([AllowAny])
def google_oauth2_login(request):
    """Google OAuth2 로그인 URL 생성"""
    from urllib.parse import urlencode
    
    params = {
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'response_type': 'code',
        'scope': 'openid email profile',
        'access_type': 'offline',
        'prompt': 'consent',
    }
    
    auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    
    return Response({'auth_url': auth_url}, status=status.HTTP_200_OK)


@extend_schema(
    summary='Google OAuth2 콜백 처리',
    description='Google OAuth2 인증 후 받은 authorization code를 처리하여 JWT 토큰을 발급합니다.',
    request={
        'type': 'object',
        'properties': {
            'code': {'type': 'string', 'description': 'Google에서 받은 authorization code'}
        },
        'required': ['code']
    },
    responses={
        200: OpenApiResponse(
            description='로그인 성공',
            response={
                'type': 'object',
                'properties': {
                    'access': {'type': 'string', 'description': 'JWT Access Token'},
                    'refresh': {'type': 'string', 'description': 'JWT Refresh Token'},
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
        400: OpenApiResponse(description='잘못된 요청'),
        401: OpenApiResponse(description='인증 실패')
    },
    tags=['인증']
)
@api_view(['POST'])
@permission_classes([AllowAny])
def google_oauth2_callback(request):
    """Google OAuth2 콜백 처리"""
    code = request.data.get('code')
    
    if not code:
        return Response(
            {'detail': 'authorization code가 필요합니다.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # 1. Authorization code를 access token으로 교환
    token_data = {
        'code': code,
        'client_id': GOOGLE_CLIENT_ID,
        'client_secret': GOOGLE_CLIENT_SECRET,
        'redirect_uri': GOOGLE_REDIRECT_URI,
        'grant_type': 'authorization_code',
    }
    
    try:
        token_response = requests.post(GOOGLE_TOKEN_URL, data=token_data)
        token_response.raise_for_status()
        token_json = token_response.json()
        access_token = token_json.get('access_token')
        
        if not access_token:
            return Response(
                {'detail': 'Google에서 access token을 받지 못했습니다.'},
                status=status.HTTP_401_UNAUTHORIZED
            )
    except requests.exceptions.RequestException as e:
        return Response(
            {'detail': f'토큰 교환 실패: {str(e)}'},
            status=status.HTTP_401_UNAUTHORIZED
        )
    
    # 2. Google 사용자 정보 가져오기
    try:
        user_info_response = requests.get(
            GOOGLE_USER_INFO_URL,
            headers={'Authorization': f'Bearer {access_token}'}
        )
        user_info_response.raise_for_status()
        user_info = user_info_response.json()
    except requests.exceptions.RequestException as e:
        return Response(
            {'detail': f'사용자 정보 가져오기 실패: {str(e)}'},
            status=status.HTTP_401_UNAUTHORIZED
        )
    
    # 3. 사용자 정보 추출
    google_id = user_info.get('id')
    email = user_info.get('email')
    name = user_info.get('name', '')
    picture = user_info.get('picture', '')
    
    if not email:
        return Response(
            {'detail': '이메일 정보를 가져올 수 없습니다.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # 4. 사용자 찾기 또는 생성
    # 이메일로 먼저 찾기
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        # 사용자가 없으면 생성
        # username은 이메일의 @ 앞부분 사용, 중복 시 숫자 추가
        base_username = email.split('@')[0]
        username = base_username
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1
        
        user = User.objects.create_user(
            username=username,
            email=email,
            first_name=name,
        )
        # 비밀번호는 설정하지 않음 (OAuth2 사용자는 비밀번호 없음)
        user.set_unusable_password()
        user.save()
    
    # 5. JWT 토큰 발급
    refresh = RefreshToken.for_user(user)
    
    return Response({
        'access': str(refresh.access_token),
        'refresh': str(refresh),
        'user': {
            'id': user.id,
            'username': user.username,
            'email': user.email,
        }
    }, status=status.HTTP_200_OK)
