"""
브로커별 API 클라이언트
"""
import requests
import pyupbit
from decimal import Decimal
from typing import Dict, Optional
from .models import Account, Order, Symbol


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


class KisClient(BaseBrokerClient):
    """한국투자증권 Open API 클라이언트 (실전투자)"""
    
    BASE_URL = "https://openapi.koreainvestment.com:9443"  # 실전투자
    
    def __init__(self, account: Account):
        super().__init__(account)
        if not account.api_key or not account.api_secret:
            raise ValueError("한국투자증권 계좌에는 API 키와 시크릿이 필요합니다.")
        
        self.api_key = account.api_key
        self.api_secret = account.api_secret
        self.account_number = account.account_number
        self.account_password = account.account_password
    
    def _get_access_token(self) -> Optional[str]:
        """액세스 토큰 발급"""
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
                return result.get('access_token')
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


def get_broker_client(account: Account) -> BaseBrokerClient:
    """계좌의 브로커에 맞는 클라이언트 반환"""
    broker = account.broker
    
    if broker.is_crypto_exchange:
        # 암호화폐 거래소
        if 'upbit' in broker.name.lower():
            return UpbitClient(account)
        else:
            raise ValueError(f"지원하지 않는 암호화폐 거래소: {broker.name}")
    else:
        # 증권사
        if '한국투자증권' in broker.name or 'kis' in broker.name.lower():
            return KisClient(account)
        else:
            raise ValueError(f"지원하지 않는 증권사: {broker.name}")

