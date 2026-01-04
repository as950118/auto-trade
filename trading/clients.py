"""
브로커별 API 클라이언트
"""
import logging
import requests
import pyupbit
import hmac
import hashlib
import time
import urllib.parse
from decimal import Decimal
from typing import Dict, Optional
from datetime import timedelta
from django.utils import timezone
from .models import Account, Order, Symbol

logger = logging.getLogger(__name__)


class BaseBrokerClient:
    """브로커 클라이언트 기본 클래스"""
    
    def __init__(self, account: Account):
        self.account = account
    
    def place_order(self, order: Order) -> Dict:
        """주문 실행"""
        raise NotImplementedError
    
    def get_order_status(self, order: Order) -> Dict:
        """주문 상태 조회"""
        raise NotImplementedError
    
    def get_account_info(self) -> Dict:
        """
        계좌 정보 조회 (잔고, 보유 종목 등)
        Returns:
            {
                'success': bool,
                'cash_balance': Decimal,  # 원화 기준 (호환성)
                'stock_value': Decimal,   # 원화 기준 (호환성)
                'total_assets': Decimal,  # 원화 기준 (호환성)
                'cash_balance_krw': Decimal,  # 원화 예수금
                'stock_value_krw': Decimal,   # 원화 보유종목가치
                'total_assets_krw': Decimal,  # 원화 총 자산
                'cash_balance_usd': Decimal,  # 달러 예수금
                'stock_value_usd': Decimal,   # 달러 보유종목가치
                'total_assets_usd': Decimal,  # 달러 총 자산
                'holdings': List[Dict],  # 보유 종목 리스트
                'data': dict
            }
        """
        raise NotImplementedError


class UpbitClient(BaseBrokerClient):
    """Upbit API 클라이언트"""
    
    def __init__(self, account: Account):
        super().__init__(account)
        if not account.api_key or not account.api_secret:
            raise ValueError("Upbit 계좌에는 API 키와 시크릿이 필요합니다.")
        
        # pyupbit 초기화
        self.upbit = pyupbit.Upbit(account.api_key, account.api_secret)
    
    def place_order(self, order: Order) -> Dict:
        """Upbit 주문 실행"""
        try:
            symbol = order.symbol
            ticker = symbol.ticker
            
            # Upbit 티커 형식 변환 (예: BTC-KRW)
            if not ticker.startswith('KRW-') and not ticker.startswith('BTC-') and not ticker.startswith('USDT-'):
                ticker = f"KRW-{ticker}"
            
            if order.order_type == 'MARKET':
                # 시장가 주문
                if order.side == 'BUY':
                    result = self.upbit.buy_market_order(ticker, order.quantity)
                else:  # SELL
                    result = self.upbit.sell_market_order(ticker, order.quantity)
            else:  # LIMIT
                # 지정가 주문
                if order.side == 'BUY':
                    result = self.upbit.buy_limit_order(ticker, float(order.price), order.quantity)
                else:  # SELL
                    result = self.upbit.sell_limit_order(ticker, float(order.price), order.quantity)
            
            if 'error' in result:
                return {
                    'success': False,
                    'error': result.get('error', {}).get('message', '주문 실패')
                }
            
            return {
                'success': True,
                'order_id': result.get('uuid'),
                'data': result
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_account_info(self) -> Dict:
        """Upbit 계좌 정보 조회"""
        try:
            # Upbit 잔고 조회
            balances = self.upbit.get_balances()
            
            if 'error' in balances:
                return {
                    'success': False,
                    'error': balances.get('error', {}).get('message', '잔고 조회 실패')
                }
            
            cash_balance = Decimal('0')
            stock_value = Decimal('0')
            holdings = []
            
            # KRW 잔고 찾기
            for balance in balances:
                currency = balance.get('currency', '')
                if currency == 'KRW':
                    cash_balance = Decimal(str(balance.get('balance', 0)))
                else:
                    # 암호화폐 보유량 * 현재가
                    locked = Decimal(str(balance.get('locked', 0)))
                    balance_amount = Decimal(str(balance.get('balance', 0)))
                    total_amount = balance_amount + locked
                    
                    if total_amount > 0:
                        # 현재가 조회
                        ticker = f"KRW-{currency}"
                        try:
                            current_price = pyupbit.get_current_price(ticker)
                            if current_price:
                                current_price_decimal = Decimal(str(current_price))
                                value = total_amount * current_price_decimal
                                stock_value += value
                                
                                # 보유 종목 정보 추가
                                holdings.append({
                                    'ticker': ticker,
                                    'name': currency,
                                    'quantity': total_amount,
                                    'current_price': current_price_decimal,
                                    'total_value': value,
                                    'average_price': Decimal('0'),  # Upbit는 평균 매수가 정보를 제공하지 않음
                                    'currency': 'KRW',
                                })
                        except:
                            pass
            
            # 통화별 자산 계산 (Upbit는 KRW 기준)
            total_assets_krw = cash_balance + stock_value
            total_assets_usd = Decimal('0')
            
            # 호환성을 위한 기존 필드
            total_assets = total_assets_krw
            
            return {
                'success': True,
                # 호환성 필드 (원화 기준)
                'cash_balance': cash_balance,
                'stock_value': stock_value,
                'total_assets': total_assets,
                # 통화별 필드
                'cash_balance_krw': cash_balance,
                'stock_value_krw': stock_value,
                'total_assets_krw': total_assets_krw,
                'cash_balance_usd': Decimal('0'),
                'stock_value_usd': Decimal('0'),
                'total_assets_usd': total_assets_usd,
                'holdings': holdings,
                'data': balances
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_order_status(self, order: Order) -> Dict:
        """Upbit 주문 상태 조회"""
        try:
            # 외부 주문 ID가 있는 경우 조회
            if order.external_order_id:
                result = self.upbit.get_order(order.external_order_id)
            else:
                # 주문 목록에서 찾기 (티커로 조회)
                ticker = order.symbol.ticker
                if not ticker.startswith('KRW-') and not ticker.startswith('BTC-') and not ticker.startswith('USDT-'):
                    ticker = f"KRW-{ticker}"
                result = self.upbit.get_order(ticker)
            
            if 'error' in result:
                return {
                    'success': False,
                    'error': result.get('error', {}).get('message', '조회 실패')
                }
            
            return {
                'success': True,
                'data': result
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }


class BingXClient(BaseBrokerClient):
    """BingX API 클라이언트"""
    
    BASE_URL = "https://open-api.bingx.com"
    
    def __init__(self, account: Account):
        super().__init__(account)
        if not account.api_key or not account.api_secret:
            raise ValueError("BingX 계좌에는 API 키와 시크릿이 필요합니다.")
        
        self.api_key = account.api_key
        self.api_secret = account.api_secret
    
    def _generate_signature(self, params: Dict) -> str:
        """HMAC SHA256 서명 생성"""
        # BingX API 서명 생성 방식
        # 쿼리 문자열 생성 (키 정렬, 값은 URL 인코딩)
        query_string = urllib.parse.urlencode(sorted(params.items()))
        # HMAC SHA256 서명
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Dict:
        """BingX API 요청"""
        url = f"{self.BASE_URL}{endpoint}"
        
        # 기본 파라미터 설정
        if params is None:
            params = {}
        
        # 타임스탬프 추가
        timestamp = int(time.time() * 1000)
        params['timestamp'] = timestamp
        
        # 헤더 설정
        headers = {
            'X-BX-APIKEY': self.api_key,
            'Content-Type': 'application/json'
        }
        
        try:
            if method == 'GET':
                # GET 요청: 쿼리 파라미터에 서명 포함
                signature = self._generate_signature(params)
                params['signature'] = signature
                response = requests.get(url, params=params, headers=headers, timeout=10)
            elif method == 'POST':
                # POST 요청: body에 데이터를 넣고, 서명은 쿼리 파라미터에 포함
                # BingX API는 POST 요청 시 body 데이터는 서명에 포함하지 않고, 
                # 쿼리 파라미터(timestamp 등)만 서명에 포함하는 경우가 많음
                signature = self._generate_signature(params)
                params['signature'] = signature
                response = requests.post(url, params=params, json=data, headers=headers, timeout=10)
            else:
                return {'success': False, 'error': f'지원하지 않는 HTTP 메서드: {method}'}
            
            # 응답 본문 로깅 (디버깅용)
            response_text = response.text
            logger.debug(f"BingX API 응답 ({endpoint}): {response.status_code} - {response_text[:500]}")
            
            try:
                result = response.json()
            except:
                # JSON 파싱 실패 시 텍스트 반환
                return {
                    'success': False,
                    'error': f'JSON 파싱 실패: {response_text[:200]}'
                }
            
            if response.status_code == 200:
                # BingX API는 code 필드로 성공/실패를 표시
                if result.get('code') == 0:
                    return {'success': True, 'data': result.get('data', result)}
                else:
                    error_msg = result.get('msg', result.get('message', f"API 오류: {result.get('code')}"))
                    return {
                        'success': False,
                        'error': error_msg
                    }
            else:
                # HTTP 에러 응답도 JSON일 수 있음
                error_msg = result.get('msg', result.get('message', response_text[:200]))
                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}: {error_msg}'
                }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_account_info(self) -> Dict:
        """BingX 계좌 정보 조회"""
        try:
            # BingX 잔고 조회 API
            # 테스트 결과: /openApi/spot/v1/account/balance 엔드포인트 사용
            # 응답 구조: {"code": 0, "data": {"balances": [{"asset": "...", "free": "...", "locked": "..."}]}}
            result = self._make_request('GET', '/openApi/spot/v1/account/balance')
            
            if not result.get('success'):
                return result
            
            account_data = result.get('data', {})
            balances = account_data.get('balances', [])
            
            if not balances:
                logger.warning("BingX 계좌 잔고가 비어있습니다.")
                balances = []
            
            cash_balance = Decimal('0')
            stock_value = Decimal('0')
            holdings = []
            
            # 잔고 처리
            for balance in balances:
                asset = balance.get('asset', '')
                free = Decimal(str(balance.get('free', 0)))  # 사용 가능한 잔고
                locked = Decimal(str(balance.get('locked', 0)))  # 주문에 묶인 잔고
                total_amount = free + locked
                
                if total_amount > 0:
                    if asset == 'USDT' or asset == 'USD':
                        # USDT/USD는 현금으로 처리
                        cash_balance += total_amount
                    else:
                        # 암호화폐는 보유 종목으로 처리
                        # BingX는 USDT 기준 거래이므로 USDT 가치로 계산
                        # 간단히 1:1로 처리하거나, 실제 시세 API를 호출해야 함
                        # 여기서는 간단히 수량만 저장하고 가치는 0으로 설정
                        holdings.append({
                            'ticker': f"{asset}-USDT",
                            'name': balance.get('disPlayName', asset),  # disPlayName 사용
                            'quantity': total_amount,
                            'current_price': Decimal('0'),  # 실제 시세 조회 필요
                            'average_price': Decimal('0'),
                            'total_value': Decimal('0'),  # 실제 시세 조회 필요
                            'currency': 'USDT',
                        })
                        # 임시로 수량만 저장 (실제 가치는 시세 조회 필요)
                        stock_value += Decimal('0')
            
            # 통화별 자산 계산 (BingX는 USDT 기준)
            total_assets_krw = Decimal('0')  # BingX는 원화 거래 없음
            total_assets_usd = cash_balance + stock_value  # USDT 기준
            
            # 호환성을 위한 기존 필드 (USDT를 원화로 변환하지 않음, 0으로 설정)
            total_assets = total_assets_usd
            
            return {
                'success': True,
                # 호환성 필드 (USDT 기준)
                'cash_balance': cash_balance,
                'stock_value': stock_value,
                'total_assets': total_assets,
                # 통화별 필드
                'cash_balance_krw': Decimal('0'),
                'stock_value_krw': Decimal('0'),
                'total_assets_krw': total_assets_krw,
                'cash_balance_usd': cash_balance,  # USDT를 USD로 처리
                'stock_value_usd': stock_value,
                'total_assets_usd': total_assets_usd,
                'holdings': holdings,
                'data': account_data
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def place_order(self, order: Order) -> Dict:
        """BingX 주문 실행"""
        try:
            symbol = order.symbol
            ticker = symbol.ticker
            
            # BingX 심볼 형식 변환 (예: BTC-USDT)
            # 티커가 이미 - 형식이 아닌 경우 USDT를 기본으로 추가
            if '-' not in ticker:
                ticker = f"{ticker}-USDT"
            
            # 주문 타입 변환
            # BingX: MARKET, LIMIT
            order_type = order.order_type
            
            # 주문 방향 변환
            # BingX: BUY, SELL
            side = order.side
            
            # 주문 파라미터 구성
            order_data = {
                'symbol': ticker,
                'side': side,
                'type': order_type,
                'quantity': str(order.quantity)
            }
            
            # 지정가 주문인 경우 가격 추가
            if order.order_type == 'LIMIT':
                if not order.price:
                    return {
                        'success': False,
                        'error': '지정가 주문은 가격이 필수입니다.'
                    }
                order_data['price'] = str(order.price)
            
            # 주문 생성 API 호출
            result = self._make_request('POST', '/openApi/spot/v1/trade/order', data=order_data)
            
            if result.get('success'):
                order_data = result.get('data', {})
                return {
                    'success': True,
                    'order_id': order_data.get('orderId') or order_data.get('order_id'),
                    'data': order_data
                }
            else:
                return result
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_order_status(self, order: Order) -> Dict:
        """BingX 주문 상태 조회"""
        try:
            symbol = order.symbol
            ticker = symbol.ticker
            
            # BingX 심볼 형식 변환
            if '-' not in ticker:
                ticker = f"{ticker}-USDT"
            
            # 주문 조회 파라미터
            params = {
                'symbol': ticker
            }
            
            # 외부 주문 ID가 있는 경우 사용
            if order.external_order_id:
                params['orderId'] = order.external_order_id
            
            # 주문 조회 API 호출
            result = self._make_request('GET', '/openApi/spot/v1/trade/query', params=params)
            
            return result
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
class KisClient(BaseBrokerClient):
    """한국투자증권 Open API 클라이언트 (실전투자)"""
    
    BASE_URL = "https://openapi.koreainvestment.com:9443"  # 실전투자
    
    def __init__(self, account: Account):
        super().__init__(account)
        if not account.api_key or not account.api_secret:
            raise ValueError("한국투자증권 계좌에는 API 키와 시크릿이 필요합니다.")
        
        if not account.account_number:
            raise ValueError("한국투자증권 계좌에는 계좌번호가 필요합니다.")
        
        self.api_key = account.api_key
        self.api_secret = account.api_secret
        self.account_number = account.account_number
        self.account_password = account.account_password or ''  # 계좌비밀번호는 선택적
    
    def _get_access_token(self) -> Optional[str]:
        """액세스 토큰 발급 (저장된 토큰이 있으면 재사용, 만료되었거나 없으면 새로 발급)"""
        # 저장된 토큰이 있고 아직 만료되지 않았으면 재사용
        if (self.account.access_token and 
            self.account.token_expires_at and 
            timezone.now() < self.account.token_expires_at):
            return self.account.access_token
        
        # 새 토큰 발급
        try:
            url = f"{self.BASE_URL}/oauth2/tokenP"
            headers = {
                "content-type": "application/json"
            }
            data = {
                "grant_type": "client_credentials",
                "appkey": self.api_key,
                "appsecret": self.api_secret
            }
            
            response = requests.post(url, json=data, headers=headers)
            if response.status_code == 200:
                result = response.json()
                access_token = result.get('access_token')
                
                if access_token:
                    # 토큰 정보 저장
                    now = timezone.now()
                    # 한국투자증권 토큰은 보통 24시간 유효 (안전하게 23시간으로 설정)
                    expires_at = now + timedelta(hours=23)
                    
                    self.account.access_token = access_token
                    self.account.token_issued_at = now
                    self.account.token_expires_at = expires_at
                    self.account.save(update_fields=['access_token', 'token_issued_at', 'token_expires_at'])
                    
                    return access_token
            return None
        except Exception as e:
            print(f"토큰 발급 실패: {e}")
            return None
    
    def place_order(self, order: Order) -> Dict:
        """한국투자증권 주문 실행"""
        try:
            access_token = self._get_access_token()
            if not access_token:
                return {
                    'success': False,
                    'error': '액세스 토큰 발급 실패'
                }
            
            symbol = order.symbol
            ticker = symbol.ticker
            
            # 주문 타입 변환
            ord_dvsn = "01" if order.order_type == "MARKET" else "00"  # 00: 지정가, 01: 시장가
            sll_buy_dvsn = "02" if order.side == "BUY" else "01"  # 01: 매도, 02: 매수
            
            # 매수/매도에 따라 tr_id 설정
            if order.side == "BUY":
                tr_id = "TTTC0802U"  # 주식 현금 매수 주문
            else:
                tr_id = "TTTC0801U"  # 주식 현금 매도 주문
            
            url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {access_token}",
                "appkey": self.api_key,
                "appsecret": self.api_secret,
                "tr_id": tr_id
            }
            
            data = {
                "CANO": self.account_number[:8],  # 종합계좌번호 앞 8자리
                "ACNT_PRDT_CD": self.account_number[8:],  # 종합계좌번호 뒤 2자리
                "PDNO": ticker,  # 종목코드
                "ORD_DVSN": ord_dvsn,  # 주문구분
                "ORD_QTY": str(int(order.quantity)),  # 주문수량
                "ORD_UNPR": str(int(order.price)) if order.price else "0",  # 주문단가
            }
            
            response = requests.post(url, json=data, headers=headers)
            
            if response.status_code == 200:
                result = response.json()
                if result.get('rt_cd') == '0':  # 성공
                    return {
                        'success': True,
                        'order_id': result.get('output', {}).get('ODNO'),  # 주문번호
                        'data': result
                    }
                else:
                    return {
                        'success': False,
                        'error': result.get('msg1', '주문 실패')
                    }
            else:
                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}: {response.text}'
                }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_order_status(self, order: Order) -> Dict:
        """한국투자증권 주문 상태 조회"""
        try:
            access_token = self._get_access_token()
            if not access_token:
                return {
                    'success': False,
                    'error': '액세스 토큰 발급 실패'
                }
            
            url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {access_token}",
                "appkey": self.api_key,
                "appsecret": self.api_secret,
                "tr_id": "TTTC8908R"  # 주식 잔고 조회
            }
            
            data = {
                "CANO": self.account_number[:8],
                "ACNT_PRDT_CD": self.account_number[8:],
                "PDNO": order.symbol.ticker,
                "ORD_DVSN": "00",  # 전체
            }
            
            response = requests.get(url, params=data, headers=headers)
            
            if response.status_code == 200:
                result = response.json()
                return {
                    'success': True,
                    'data': result
                }
            else:
                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}: {response.text}'
                }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_account_info(self) -> Dict:
        """한국투자증권 계좌 정보 조회"""
        try:
            access_token = self._get_access_token()
            if not access_token:
                return {
                    'success': False,
                    'error': '액세스 토큰 발급 실패'
                }
            
            # 잔고 조회 API
            url = f"{self.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
            headers = {
                "Content-Type": "application/json",
                "authorization": f"Bearer {access_token}",
                "appkey": self.api_key,
                "appsecret": self.api_secret,
                "tr_id": "TTTC8434R"  # 주식 잔고 조회
            }
            
            params = {
                "CANO": self.account_number[:8],
                "ACNT_PRDT_CD": self.account_number[8:],
                "AFHR_FLPR_YN": "N",  # 시간외단일가여부
                "OFL_YN": "",  # 오프라인여부
                "INQR_DVSN": "02",  # 조회구분 (01:대출일별, 02:종목별)
                "UNPR_DVSN": "01",  # 단가 구분 (01:현재가, 02:평균단가)
                "FUND_STTL_ICLD_YN": "N",  # 펀드결제분포함여부
                "FNCG_AMT_AUTO_RDPT_YN": "N",  # 융자금액자동상환여부
                "PRCS_DVSN": "01",  # 처리구분 (01:전체, 02:체결, 03:미체결)
                "CTX_AREA_FK100": "",  # 연속조회검색조건100
                "CTX_AREA_NK100": "",  # 연속조회키100
            }
            
            response = requests.get(url, params=params, headers=headers)
            
            if response.status_code == 200:
                result = response.json()
                
                if result.get('rt_cd') == '0':  # 성공
                    output1 = result.get('output1', [])  # 주식 잔고
                    output2 = result.get('output2', [])  # 예수금 정보
                    
                    # 예수금 계산
                    cash_balance = Decimal('0')
                    if output2 and len(output2) > 0:
                        cash_info = output2[0]
                        # 예수금 = 주문가능금액
                        cash_balance = Decimal(str(cash_info.get('ord_psbl_cash', 0)))
                    
                    # 보유 종목 가치 계산 및 보유 종목 정보 수집
                    stock_value = Decimal('0')
                    holdings = []
                    
                    for stock in output1:
                        # 보유수량, 현재가
                        qty = Decimal(str(stock.get('hldg_qty', 0)))
                        prpr = Decimal(str(stock.get('prpr', 0)))  # 현재가
                        pchs_avg_pric = Decimal(str(stock.get('pchs_avg_pric', 0)))  # 평균 매수가
                        pdno = stock.get('pdno', '')  # 종목코드
                        
                        if qty > 0:
                            value = qty * prpr
                            stock_value += value
                            
                            # 보유 종목 정보 추가 (국내 주식은 KRW 기준)
                            holdings.append({
                                'ticker': pdno,
                                'quantity': qty,
                                'current_price': prpr,  # KRW 기준
                                'average_price': pchs_avg_pric if pchs_avg_pric > 0 else prpr,  # KRW 기준
                                'total_value': value,  # KRW 기준
                                'currency': 'KRW',  # 국내 주식은 KRW
                            })
                    
                    # 해외 주식 잔고 조회
                    stock_value_usd = Decimal('0')  # USD 기준 보유종목가치
                    try:
                        logger.info("해외 주식 잔고 조회 시작")
                        overseas_holdings, overseas_stock_value_krw = self._get_overseas_holdings(access_token)
                        logger.info(f"해외 주식 조회 결과: {len(overseas_holdings)}개 종목, 총 가치: {overseas_stock_value_krw:,.0f}원")
                        
                        if overseas_holdings:
                            holdings.extend(overseas_holdings)
                            stock_value += overseas_stock_value_krw  # 원화 기준으로 총 자산에 추가
                            
                            # USD 기준 보유종목가치 계산
                            for h in overseas_holdings:
                                stock_value_usd += h.get('total_value', Decimal('0'))
                            
                            logger.info(f"해외 주식 {len(overseas_holdings)}개 종목 수집 완료 (USD 가치: ${stock_value_usd:,.2f})")
                        else:
                            logger.info("해외 주식 보유 종목 없음")
                    except Exception as e:
                        logger.error(f"해외 주식 조회 중 오류 (국내 주식은 정상): {str(e)}", exc_info=True)
                    
                    # 통화별 자산 계산
                    total_assets_krw = cash_balance + stock_value  # 원화 총 자산 (국내+해외 원화변환)
                    total_assets_usd = stock_value_usd  # USD 총 자산 (해외 주식만)
                    
                    # 호환성을 위한 기존 필드 (원화 기준)
                    total_assets = total_assets_krw
                    
                    return {
                        'success': True,
                        # 호환성 필드 (원화 기준)
                        'cash_balance': cash_balance,
                        'stock_value': stock_value,
                        'total_assets': total_assets,
                        # 통화별 필드
                        'cash_balance_krw': cash_balance,
                        'stock_value_krw': stock_value,
                        'total_assets_krw': total_assets_krw,
                        'cash_balance_usd': Decimal('0'),  # 해외 주식 계좌는 보통 USD 예수금 없음
                        'stock_value_usd': stock_value_usd,
                        'total_assets_usd': total_assets_usd,
                        'holdings': holdings,
                        'data': result
                    }
                else:
                    return {
                        'success': False,
                        'error': result.get('msg1', '잔고 조회 실패')
                    }
            else:
                return {
                    'success': False,
                    'error': f'HTTP {response.status_code}: {response.text}'
                }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _get_overseas_holdings(self, access_token: str) -> tuple:
        """해외 주식 잔고 조회"""
        holdings = []
        stock_value = Decimal('0')
        
        # 해외 주식 잔고 조회 API
        url = f"{self.BASE_URL}/uapi/overseas-stock/v1/trading/inquire-balance"
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {access_token}",
            "appkey": self.api_key,
            "appsecret": self.api_secret,
            "tr_id": "TTTS3012R"  # 해외주식 잔고조회
        }
        
        # 여러 거래소 조회 (나스닥, 뉴욕 등)
        exchanges = ['NASD', 'NYSE', 'AMEX', 'TSEI', 'HASE']  # 주요 거래소
        
        for exchange in exchanges:
            try:
                params = {
                    "CANO": self.account_number[:8],
                    "ACNT_PRDT_CD": self.account_number[8:],
                    "OVRS_EXCG_CD": exchange,  # 해외거래소코드
                    "TR_CRCY_CD": "USD",  # 거래통화코드
                    "CTX_AREA_FK200": "",  # 연속조회검색조건200
                    "CTX_AREA_NK200": "",  # 연속조회키200
                }
                
                response = requests.get(url, params=params, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    result = response.json()
                    
                    if result.get('rt_cd') == '0':  # 성공
                        output1 = result.get('output1', [])  # 해외 주식 잔고
                        
                        if output1:
                            logger.info(f"거래소 {exchange}에서 {len(output1)}개 종목 조회")
                            
                            # 첫 번째 종목의 실제 API 응답 구조 로그 (디버깅용)
                            if output1:
                                logger.debug(f"해외 주식 API 응답 샘플 (첫 번째 종목): {output1[0]}")
                            
                            for stock in output1:
                                # 보유수량
                                qty = Decimal(str(stock.get('ovrs_cblc_qty', 0)))  # 해외주식잔고수량
                                
                                # 현재가 필드명 확인 (여러 가능한 필드명 시도)
                                prpr = Decimal('0')
                                price_fields = [
                                    'ovrs_stck_prpr',      # 해외주식현재가 (기본)
                                    'ovrs_stck_prpr1',     # 해외주식현재가1
                                    'now_pric2',           # 현재가2
                                    'prpr',                # 현재가
                                    'ovrs_stck_prpr_cncl', # 해외주식현재가(정정취소)
                                    'ovrs_stck_prpr2',     # 해외주식현재가2
                                    'base_pric',           # 기준가
                                ]
                                
                                for price_field in price_fields:
                                    price_val = stock.get(price_field)
                                    if price_val is not None and price_val != '':
                                        try:
                                            price_decimal = Decimal(str(price_val))
                                            if price_decimal > 0:
                                                prpr = price_decimal
                                                break
                                        except:
                                            continue
                                
                                # 매입평균가
                                pchs_avg_pric = Decimal(str(stock.get('pchs_avg_pric', 0)))  # 매입평균가
                                
                                # 종목 정보
                                pdno = stock.get('ovrs_pdno', '')  # 해외종목코드
                                ovrs_excg_cd = stock.get('ovrs_excg_cd', '')  # 해외거래소코드
                                ovrs_item_name = stock.get('ovrs_item_name', '')  # 해외종목명
                                
                                if qty > 0:
                                    # 환율
                                    xch_rate = Decimal(str(stock.get('xch_rate', 1)))  # 환율
                                    
                                    # 현재가가 없으면 매입평균가를 사용
                                    if prpr == 0 and pchs_avg_pric > 0:
                                        prpr = pchs_avg_pric
                                        logger.debug(f"종목 {pdno} 현재가 없음, 매입평균가 사용: {prpr}")
                                    
                                    # USD 기준 가치 계산 (환율 적용 전)
                                    if prpr > 0:
                                        value_usd = qty * prpr  # USD 기준
                                        value_krw = value_usd * xch_rate  # 원화 기준 (계좌 총 자산 계산용)
                                    else:
                                        value_usd = Decimal('0')
                                        value_krw = Decimal('0')
                                        logger.warning(f"종목 {pdno} 현재가와 매입평균가 모두 없음")
                                    
                                    # 원화 기준 가치를 stock_value에 추가 (계좌 총 자산 계산용)
                                    stock_value += value_krw
                                    
                                    # 보유 종목 정보 추가 (USD 기준으로 저장)
                                    holdings.append({
                                        'ticker': pdno,
                                        'name': ovrs_item_name or pdno,
                                        'quantity': qty,
                                        'current_price': prpr,  # USD 기준
                                        'average_price': pchs_avg_pric if pchs_avg_pric > 0 else prpr,  # USD 기준
                                        'total_value': value_usd,  # USD 기준 (환율 적용 전)
                                        'total_value_krw': value_krw,  # 원화 기준 (참고용)
                                        'exchange': ovrs_excg_cd,  # 거래소 코드
                                        'currency': 'USD',  # 해외 주식은 USD
                                        'exchange_rate': xch_rate,  # 환율
                                    })
                    else:
                        # rt_cd가 0이 아닌 경우 (에러 또는 데이터 없음)
                        msg1 = result.get('msg1', '')
                        msg_cd = result.get('msg_cd', '')
                        if msg_cd != 'EGW00123':  # EGW00123은 보유 종목이 없을 때의 코드
                            logger.warning(f"거래소 {exchange} 조회 실패: {msg1} (코드: {msg_cd})")
                else:
                    logger.warning(f"거래소 {exchange} 조회 HTTP 오류: {response.status_code} - {response.text[:200]}")
                    
            except requests.exceptions.Timeout:
                logger.warning(f"거래소 {exchange} 조회 타임아웃")
                continue
            except requests.exceptions.RequestException as e:
                logger.warning(f"거래소 {exchange} 조회 네트워크 오류: {str(e)}")
                continue
            except Exception as e:
                logger.warning(f"거래소 {exchange} 조회 중 오류: {str(e)}")
                continue
        
        if holdings:
            logger.info(f"해외 주식 총 {len(holdings)}개 종목 수집 완료 (총 가치: {stock_value:,.0f}원)")
            # 각 종목 로그
            for h in holdings:
                logger.info(f"  - {h.get('ticker')} ({h.get('name')}): {h.get('quantity')}주, 현재가: ${h.get('current_price')}, 총가치: ${h.get('total_value')} ({h.get('currency')})")
        else:
            logger.info("해외 주식 보유 종목 없음 (모든 거래소 조회 완료)")
        
        return holdings, stock_value


def get_broker_client(account: Account) -> BaseBrokerClient:
    """계좌의 브로커에 맞는 클라이언트 반환"""
    broker = account.broker
    broker_code = broker.code.upper() if broker.code else ''
    
    if broker.is_crypto_exchange:
        # 암호화폐 거래소
        if broker_code == 'UPBIT':
            return UpbitClient(account)
        elif broker_code == 'BINGX':
            return BingXClient(account)
        else:
            raise ValueError(f"지원하지 않는 암호화폐 거래소: {broker.name} (코드: {broker_code})")
    else:
        # 증권사
        if broker_code == 'KIS':
            return KisClient(account)
        else:
            raise ValueError(f"지원하지 않는 증권사: {broker.name} (코드: {broker_code})")

