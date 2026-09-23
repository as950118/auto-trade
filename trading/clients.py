"""
브로커별 API 클라이언트
"""
import logging
import requests
import ccxt
import pyupbit
import time
from decimal import Decimal, ROUND_DOWN
from typing import Dict, Optional
from datetime import timedelta
from django.utils import timezone
from .models import Account, Order, Symbol

logger = logging.getLogger(__name__)


# 정규화된 주문 상태 (ADR-0004). get_order_status()는 브로커와 무관하게 이 값을 'status'로 돌려준다.
ORDER_STATUS_OPEN = 'open'          # 미체결 또는 부분체결 (filled_quantity > 0이면 부분체결)
ORDER_STATUS_FILLED = 'filled'
ORDER_STATUS_CANCELED = 'canceled'
ORDER_STATUS_REJECTED = 'rejected'
ORDER_STATUS_UNKNOWN = 'unknown'    # 브로커 응답을 해석할 수 없음 — 호출부는 상태를 바꾸지 않는다


def order_status_result(
    status: str,
    filled_quantity: Decimal = Decimal('0'),
    average_price: Optional[Decimal] = None,
    external_order_id: Optional[str] = None,
    raw=None,
) -> Dict:
    """get_order_status()의 성공 응답. 'data'는 브로커 원본 응답(하위 호환·디버깅용)."""
    return {
        'success': True,
        'status': status,
        'filled_quantity': filled_quantity,
        'average_price': average_price,
        'external_order_id': external_order_id,
        'data': raw,
    }


class BaseBrokerClient:
    """브로커 클라이언트 기본 클래스"""
    
    def __init__(self, account: Account):
        self.account = account
    
    def place_order(self, order: Order) -> Dict:
        """주문 실행"""
        raise NotImplementedError
    
    def get_order_status(self, order: Order) -> Dict:
        """
        주문 상태 조회. 성공 시 order_status_result() 형태를 반환한다:
            {'success': True, 'status': ORDER_STATUS_*, 'filled_quantity': Decimal,
             'average_price': Optional[Decimal], 'external_order_id': Optional[str], 'data': 원본}
        """
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
    
    def get_crypto_price(self, ticker: str) -> Optional[Decimal]:
        """
        암호화폐 현재가 조회
        Args:
            ticker: 티커 (예: "BTC-KRW", "BTC-USDT")
        Returns:
            현재가 (Decimal) 또는 None
        """
        # 기본 구현은 None 반환 (각 클라이언트에서 구현)
        return None


class UpbitClient(BaseBrokerClient):
    """Upbit API 클라이언트"""
    
    def __init__(self, account: Account):
        super().__init__(account)
        if not account.api_key or not account.api_secret:
            raise ValueError("Upbit 계좌에는 API 키와 시크릿이 필요합니다.")
        
        # pyupbit 초기화
        self.upbit = pyupbit.Upbit(account.api_key, account.api_secret)

    @staticmethod
    def to_market_ticker(ticker: str) -> str:
        """심볼 티커(BTC)를 Upbit 마켓 코드(KRW-BTC)로 변환"""
        if not ticker:
            return ticker
        if ticker.startswith(('KRW-', 'BTC-', 'USDT-')):
            return ticker
        return f"KRW-{ticker}"

    @staticmethod
    def to_symbol_ticker(market_or_currency: str) -> str:
        """업비트 마켓/통화 코드를 시스템 티커로 변환 (KRW-BTC -> BTC)"""
        if not market_or_currency:
            return market_or_currency
        if '-' in market_or_currency:
            return market_or_currency.split('-')[-1]
        return market_or_currency
    
    def place_order(self, order: Order) -> Dict:
        """Upbit 주문 실행"""
        try:
            ticker = self.to_market_ticker(order.symbol.ticker)
            
            if order.order_type == 'MARKET':
                # 시장가 주문
                if order.side == 'BUY':
                    # pyupbit buy_market_order 2번째 인자는 코인 수량이 아니라 KRW 금액
                    current_price = pyupbit.get_current_price(ticker)
                    if not current_price:
                        return {'success': False, 'error': f'현재가 조회 실패: {ticker}'}
                    krw_amount = (Decimal(str(order.quantity)) * Decimal(str(current_price))).quantize(
                        Decimal('1'), rounding=ROUND_DOWN
                    )
                    if krw_amount < 5000:
                        return {
                            'success': False,
                            'error': f'최소 주문금액 미달: {krw_amount}원 (최소 5,000원)'
                        }
                    result = self.upbit.buy_market_order(ticker, float(krw_amount))
                else:  # SELL
                    result = self.upbit.sell_market_order(ticker, float(order.quantity))
            else:  # LIMIT
                # 지정가 주문
                if order.side == 'BUY':
                    result = self.upbit.buy_limit_order(ticker, float(order.price), float(order.quantity))
                else:  # SELL
                    result = self.upbit.sell_limit_order(ticker, float(order.price), float(order.quantity))

            if result is None:
                return {'success': False, 'error': '업비트 주문 응답이 없습니다.'}
            
            if isinstance(result, dict) and 'error' in result:
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

            if not isinstance(balances, list):
                if isinstance(balances, dict):
                    error = balances.get('error') or {}
                    return {
                        'success': False,
                        'error': error.get('message', str(balances)),
                    }
                return {'success': False, 'error': '잔고 조회 실패'}
            
            cash_balance = Decimal('0')
            stock_value = Decimal('0')
            holdings = []
            
            for balance in balances:
                currency = balance.get('currency', '')
                if currency == 'KRW':
                    cash_balance = Decimal(str(balance.get('balance', 0) or 0))
                    continue

                locked = Decimal(str(balance.get('locked', 0) or 0))
                balance_amount = Decimal(str(balance.get('balance', 0) or 0))
                total_amount = balance_amount + locked
                if total_amount <= 0:
                    continue

                # 크롤링 종목과 동일하게 티커는 코인 코드(BTC)로 저장
                symbol_ticker = self.to_symbol_ticker(currency)
                market = self.to_market_ticker(symbol_ticker)
                avg_buy_price = Decimal(str(balance.get('avg_buy_price', 0) or 0))

                current_price_decimal = Decimal('0')
                try:
                    current_price = pyupbit.get_current_price(market)
                    if current_price:
                        current_price_decimal = Decimal(str(current_price))
                except Exception as e:
                    logger.warning(f"Upbit 현재가 조회 실패 ({market}): {e}")

                # 상장폐지/미지원 마켓은 시세 없음 → 평가액 0 (평균매수가로 부풀리지 않음)
                value = (
                    total_amount * current_price_decimal
                    if current_price_decimal > 0
                    else Decimal('0')
                )
                stock_value += value

                holdings.append({
                    'ticker': symbol_ticker,
                    'name': currency,
                    'quantity': total_amount,
                    'current_price': current_price_decimal,
                    'total_value': value,
                    'average_price': avg_buy_price,
                    'currency': 'KRW',
                })
            
            # 통화별 자산 계산 (Upbit는 KRW 기준)
            total_assets_krw = cash_balance + stock_value
            
            return {
                'success': True,
                # 호환성 필드 (원화 기준)
                'cash_balance': cash_balance,
                'stock_value': stock_value,
                'total_assets': total_assets_krw,
                # 통화별 필드
                'cash_balance_krw': cash_balance,
                'stock_value_krw': stock_value,
                'total_assets_krw': total_assets_krw,
                'cash_balance_usd': Decimal('0'),
                'stock_value_usd': Decimal('0'),
                'total_assets_usd': Decimal('0'),
                'holdings': holdings,
                'data': balances
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    def get_crypto_price(self, ticker: str) -> Optional[Decimal]:
        """Upbit 암호화폐 현재가 조회 (KRW 마켓 기준)"""
        try:
            market = self.to_market_ticker(ticker)
            price = pyupbit.get_current_price(market)
            if price:
                return Decimal(str(price))
            return None
        except Exception as e:
            logger.warning(f"Upbit 현재가 조회 실패 ({ticker}): {e}")
            return None
    
    def get_order_status(self, order: Order) -> Dict:
        """Upbit 주문 상태 조회"""
        try:
            if not order.external_order_id:
                return {
                    'success': False,
                    'error': '외부 주문 ID가 없어 상태를 조회할 수 없습니다.'
                }

            result = self.upbit.get_order(order.external_order_id)
            if result is None:
                return {'success': False, 'error': '주문 조회 응답이 없습니다.'}

            if isinstance(result, dict) and 'error' in result:
                return {
                    'success': False,
                    'error': result.get('error', {}).get('message', '조회 실패')
                }
            
            return self._normalize_order(result)
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    @staticmethod
    def _normalize_order(data: Dict) -> Dict:
        """
        Upbit 주문 응답 → 정규화 상태. 기존 tasks.check_order_status()의 Upbit 해석을 그대로 옮겼다:
        state 'done' → filled, 'cancel' → canceled, 그 외는 open(executed_volume > 0이면 부분체결).
        """
        state = data.get('state')
        executed_volume = Decimal(str(float(data.get('executed_volume', 0))))
        avg_price = float(data.get('avg_price', 0))
        if state == 'done':
            status = ORDER_STATUS_FILLED
        elif state == 'cancel':
            status = ORDER_STATUS_CANCELED
        else:
            status = ORDER_STATUS_OPEN
        return order_status_result(
            status,
            filled_quantity=executed_volume,
            average_price=Decimal(str(avg_price)) if avg_price > 0 else None,
            external_order_id=data.get('uuid'),
            raw=data,
        )


class BingXClient(BaseBrokerClient):
    """
    BingX 현물 클라이언트 — ccxt 기반 (ADR-0004, TASK-0015).

    이전에는 HMAC 서명·요청·응답 파싱을 직접 구현했으나, 거래소 API 변경 추적 부담을 ccxt(MIT)에 넘긴다.
    ccxt 버전은 requirements.txt에서 고정한다(pyOpenSSL이 요구하는 cryptography 버전과 호환되는 4.5.64).
    ccxt는 요청 헤더에 X-SOURCE-KEY(기본값 'CCXT', 브로커 식별용)를 붙인다.
    """

    QUOTE = 'USDT'
    MARKETS_TTL_SEC = 6 * 60 * 60

    # load_markets()는 spot/swap/inverse 마켓 목록을 모두 받아오는 무거운 호출이라 프로세스 단위로 캐시한다.
    _markets = None
    _markets_loaded_at = 0.0

    def __init__(self, account: Account):
        super().__init__(account)
        if not account.api_key or not account.api_secret:
            raise ValueError("BingX 계좌에는 API 키와 시크릿이 필요합니다.")

        self.exchange = ccxt.bingx({
            'apiKey': account.api_key,
            'secret': account.api_secret,
            'enableRateLimit': True,
            'timeout': 10000,
            'options': {'defaultType': 'spot'},
        })

    def _ensure_markets(self):
        cls = BingXClient
        if cls._markets is None or time.time() - cls._markets_loaded_at > cls.MARKETS_TTL_SEC:
            cls._markets = self.exchange.load_markets()
            cls._markets_loaded_at = time.time()
        elif not self.exchange.markets:
            self.exchange.set_markets(cls._markets)

    @classmethod
    def to_ccxt_symbol(cls, ticker: str) -> str:
        """'BTC-USDT' / 'BTC' → ccxt 통합 심볼 'BTC/USDT'."""
        if '/' in ticker:
            return ticker
        if '-' in ticker:
            base, quote = ticker.split('-', 1)
            return f"{base}/{quote}"
        return f"{ticker}/{cls.QUOTE}"

    def get_account_info(self) -> Dict:
        """BingX 계좌 정보 조회"""
        try:
            balance = self.exchange.fetch_balance()
            raw = (balance.get('info') or {}).get('data') or {}
            display_names = {
                b.get('asset'): b.get('disPlayName')
                for b in raw.get('balances', [])
                if isinstance(b, dict)
            }

            cash_balance = Decimal('0')
            stock_value = Decimal('0')
            holdings = []

            for asset, amount in (balance.get('total') or {}).items():
                total_amount = Decimal(str(amount or 0))
                if total_amount <= 0:
                    continue
                if asset in ('USDT', 'USD'):
                    # USDT/USD는 현금으로 처리
                    cash_balance += total_amount
                    continue

                # BingX는 USDT 기준 거래이므로 USDT 가치로 계산
                ticker = f"{asset}-{self.QUOTE}"
                current_price = self.get_crypto_price(ticker) or Decimal('0')
                total_value = total_amount * current_price if current_price > 0 else Decimal('0')
                stock_value += total_value

                holdings.append({
                    'ticker': ticker,
                    'name': display_names.get(asset) or asset,
                    'quantity': total_amount,
                    'current_price': current_price,
                    'average_price': Decimal('0'),  # BingX는 평균 매수가 정보를 제공하지 않음
                    'total_value': total_value,
                    'currency': 'USDT',
                })

            total_assets_usd = cash_balance + stock_value  # USDT 기준

            return {
                'success': True,
                # 호환성 필드 (USDT 기준)
                'cash_balance': cash_balance,
                'stock_value': stock_value,
                'total_assets': total_assets_usd,
                # 통화별 필드 (BingX는 원화 거래 없음)
                'cash_balance_krw': Decimal('0'),
                'stock_value_krw': Decimal('0'),
                'total_assets_krw': Decimal('0'),
                'cash_balance_usd': cash_balance,  # USDT를 USD로 처리
                'stock_value_usd': stock_value,
                'total_assets_usd': total_assets_usd,
                'holdings': holdings,
                'data': raw,
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def place_order(self, order: Order) -> Dict:
        """BingX 주문 실행. 시장가 매수도 수량(base) 기준으로 보낸다(기존 구현과 동일)."""
        if order.order_type == 'LIMIT' and not order.price:
            return {'success': False, 'error': '지정가 주문은 가격이 필수입니다.'}
        try:
            self._ensure_markets()
            price = float(order.price) if order.order_type == 'LIMIT' else None
            result = self.exchange.create_order(
                self.to_ccxt_symbol(order.symbol.ticker),
                order.order_type.lower(),
                order.side.lower(),
                float(order.quantity),
                price,
            )
            return {'success': True, 'order_id': result.get('id'), 'data': result}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ccxt 통합 주문 상태 → 정규화 상태
    _CCXT_STATUS = {
        'open': ORDER_STATUS_OPEN,
        'closed': ORDER_STATUS_FILLED,
        'canceled': ORDER_STATUS_CANCELED,
        'expired': ORDER_STATUS_CANCELED,
        'rejected': ORDER_STATUS_REJECTED,
    }

    def get_order_status(self, order: Order) -> Dict:
        """BingX 주문 상태 조회"""
        if not order.external_order_id:
            return {'success': False, 'error': '외부 주문 ID가 없어 상태를 조회할 수 없습니다.'}
        try:
            self._ensure_markets()
            result = self.exchange.fetch_order(order.external_order_id, self.to_ccxt_symbol(order.symbol.ticker))
            filled = result.get('filled')
            average = result.get('average')
            return order_status_result(
                self._CCXT_STATUS.get(result.get('status'), ORDER_STATUS_UNKNOWN),
                filled_quantity=Decimal(str(filled)) if filled is not None else Decimal('0'),
                average_price=Decimal(str(average)) if average else None,
                external_order_id=result.get('id'),
                raw=result,
            )
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_crypto_price(self, ticker: str) -> Optional[Decimal]:
        """BingX 암호화폐 현재가 조회"""
        try:
            self._ensure_markets()
            last = self.exchange.fetch_ticker(self.to_ccxt_symbol(ticker)).get('last')
            return Decimal(str(last)) if last else None
        except Exception as e:
            logger.warning(f"BingX 현재가 조회 실패 ({ticker}): {str(e)}")
            return None


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
                # 이 엔드포인트(inquire-psbl-order)는 주문 체결 조회가 아니라 주문가능 조회라 체결 상태를
                # 해석할 수 없다(TASK-0017에서 확인). 해석 전까지 unknown으로 두어 상태를 바꾸지 않는다(기존 동작 유지).
                return order_status_result(ORDER_STATUS_UNKNOWN, raw=result)
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

