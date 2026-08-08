"""
종목 크롤링 모듈
"""
import logging
import requests
import pyupbit
from decimal import Decimal
from typing import List, Dict, Optional
from django.utils import timezone
from .models import Symbol, Broker, Currency, Country

logger = logging.getLogger(__name__)

# FinanceDataReader는 선택적 import (설치되어 있을 때만 사용)
try:
    import FinanceDataReader as fdr
    FDR_AVAILABLE = True
except ImportError:
    FDR_AVAILABLE = False
    logger.warning("FinanceDataReader가 설치되지 않았습니다. 한국 주식 종목 크롤링이 제한될 수 있습니다.")


class StockCrawler:
    """주식 종목 크롤러"""
    
    @staticmethod
    def crawl_korea_stocks() -> List[Dict]:
        """한국 주식 종목 크롤링 (FinanceDataReader 사용)"""
        try:
            logger.info("한국 주식 종목 크롤링 시작")
            
            stocks = []
            
            if FDR_AVAILABLE:
                # FinanceDataReader를 사용하여 한국 주식 종목 목록 가져오기
                try:
                    # KRX 상장 종목 목록 가져오기
                    # 'KRX'(시세 API)는 data.krx.co.kr의 세션 검증 강화로 LOGOUT 오류 발생 -
                    # 종목 기본정보만 필요하므로 'KRX-DESC'(상장회사 목록 API)를 사용
                    stock_list = fdr.StockListing('KRX-DESC')
                    
                    if stock_list is not None and not stock_list.empty:
                        for _, row in stock_list.iterrows():
                            ticker = str(row.get('Code', '')).strip()
                            name = str(row.get('Name', '')).strip()
                            
                            if ticker and name:
                                stocks.append({
                                    'ticker': ticker,
                                    'name': name,
                                    'currency': Currency.KRW
                                })
                        
                        logger.info(f"FinanceDataReader로 {len(stocks)}개 종목 수집 완료")
                    else:
                        logger.warning("FinanceDataReader에서 종목 목록을 가져올 수 없습니다.")
                except Exception as e:
                    logger.error(f"FinanceDataReader 사용 중 오류: {str(e)}")
            
            # FinanceDataReader가 없거나 실패한 경우, 한국투자증권 API 사용 고려
            # 또는 다른 공개 데이터 소스 사용
            
            if not stocks:
                logger.warning("한국 주식 종목을 수집하지 못했습니다. FinanceDataReader 설치를 확인하세요.")
            
            return stocks
            
        except Exception as e:
            logger.error(f"한국 주식 종목 크롤링 실패: {str(e)}")
            return []
    
    @staticmethod
    def crawl_us_stocks() -> List[Dict]:
        """미국 주식 종목 크롤링"""
        try:
            logger.info("미국 주식 종목 크롤링 시작")
            
            stocks = []
            
            # 방법 1: FinanceDataReader 사용 (미국 주식 지원)
            if FDR_AVAILABLE:
                try:
                    # NASDAQ 상장 종목
                    nasdaq_list = fdr.StockListing('NASDAQ')
                    if nasdaq_list is not None and not nasdaq_list.empty:
                        for _, row in nasdaq_list.iterrows():
                            ticker = str(row.get('Symbol', '')).strip()
                            name = str(row.get('Name', '')).strip()
                            
                            if ticker and name:
                                stocks.append({
                                    'ticker': ticker,
                                    'name': name,
                                    'currency': Currency.USD
                                })
                        logger.info(f"NASDAQ 종목 {len(stocks)}개 수집 완료")
                    
                    # NYSE 상장 종목
                    nyse_list = fdr.StockListing('NYSE')
                    if nyse_list is not None and not nyse_list.empty:
                        nyse_count = 0
                        for _, row in nyse_list.iterrows():
                            ticker = str(row.get('Symbol', '')).strip()
                            name = str(row.get('Name', '')).strip()
                            
                            if ticker and name:
                                # 중복 제거 (NASDAQ과 겹칠 수 있음)
                                if not any(s['ticker'] == ticker for s in stocks):
                                    stocks.append({
                                        'ticker': ticker,
                                        'name': name,
                                        'currency': Currency.USD
                                    })
                                    nyse_count += 1
                        logger.info(f"NYSE 종목 {nyse_count}개 추가 수집 완료")
                    
                except Exception as e:
                    logger.error(f"FinanceDataReader로 미국 주식 크롤링 중 오류: {str(e)}")
            
            # 방법 2: Wikipedia를 통한 주요 지수 종목 수집 (FinanceDataReader 실패 시)
            if not stocks:
                try:
                    import pandas as pd
                    from io import StringIO
                    
                    # S&P 500 종목 목록
                    sp500_url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
                    response = requests.get(sp500_url, timeout=10)
                    if response.status_code == 200:
                        # HTML 테이블 파싱
                        tables = pd.read_html(StringIO(response.text))
                        if tables:
                            sp500_table = tables[0]
                            for _, row in sp500_table.iterrows():
                                ticker = str(row.get('Symbol', '')).strip()
                                name = str(row.get('Security', '')).strip()
                                
                                if ticker and name:
                                    stocks.append({
                                        'ticker': ticker,
                                        'name': name,
                                        'currency': Currency.USD
                                    })
                            logger.info(f"S&P 500 종목 {len(stocks)}개 수집 완료")
                    
                    # NASDAQ-100 종목 목록
                    nasdaq100_url = "https://en.wikipedia.org/wiki/NASDAQ-100"
                    response = requests.get(nasdaq100_url, timeout=10)
                    if response.status_code == 200:
                        tables = pd.read_html(StringIO(response.text))
                        if tables and len(tables) > 4:
                            nasdaq100_table = tables[4]  # NASDAQ-100 테이블
                            nasdaq100_count = 0
                            for _, row in nasdaq100_table.iterrows():
                                ticker = str(row.get('Ticker', '')).strip()
                                name = str(row.get('Company', '')).strip()
                                
                                if ticker and name:
                                    # 중복 제거
                                    if not any(s['ticker'] == ticker for s in stocks):
                                        stocks.append({
                                            'ticker': ticker,
                                            'name': name,
                                            'currency': Currency.USD
                                        })
                                        nasdaq100_count += 1
                            logger.info(f"NASDAQ-100 종목 {nasdaq100_count}개 추가 수집 완료")
                    
                except Exception as e:
                    logger.error(f"Wikipedia를 통한 미국 주식 크롤링 중 오류: {str(e)}")
            
            if not stocks:
                logger.warning("미국 주식 종목을 수집하지 못했습니다. FinanceDataReader 설치를 확인하세요.")
            else:
                logger.info(f"미국 주식 종목 총 {len(stocks)}개 수집 완료")
            
            return stocks
            
        except Exception as e:
            logger.error(f"미국 주식 종목 크롤링 실패: {str(e)}")
            return []
    
    @staticmethod
    def crawl_japan_stocks() -> List[Dict]:
        """일본 주식 종목 크롤링"""
        try:
            logger.info("일본 주식 종목 크롤링 시작")
            
            stocks = []
            
            # FinanceDataReader 사용 (일본 주식 지원)
            if FDR_AVAILABLE:
                try:
                    # 일본 주식 상장 종목 (FinanceDataReader는 'TSE' 코드로 도쿄증권거래소 종목을 제공)
                    japan_list = fdr.StockListing('TSE')
                    if japan_list is not None and not japan_list.empty:
                        for _, row in japan_list.iterrows():
                            ticker = str(row.get('Symbol', '')).strip()
                            name = str(row.get('Name', '')).strip()
                            
                            if ticker and name:
                                stocks.append({
                                    'ticker': ticker,
                                    'name': name,
                                    'currency': Currency.JPY
                                })
                        logger.info(f"일본 주식 종목 {len(stocks)}개 수집 완료")
                except Exception as e:
                    logger.error(f"FinanceDataReader로 일본 주식 크롤링 중 오류: {str(e)}")
            
            if not stocks:
                logger.warning("일본 주식 종목을 수집하지 못했습니다. FinanceDataReader 설치를 확인하세요.")
            
            return stocks
            
        except Exception as e:
            logger.error(f"일본 주식 종목 크롤링 실패: {str(e)}")
            return []
    
    @staticmethod
    def update_stocks(broker: Broker, stocks: List[Dict]):
        """주식 종목 업데이트"""
        try:
            existing_tickers = set(
                Symbol.objects.filter(broker=broker, is_crypto=False)
                .values_list('ticker', flat=True)
            )

            # 같은 티커가 소스 목록에 중복으로 나타날 수 있어(예: 동일 종목이 복수 거래소 목록에 겹침)
            # 티커 기준으로 먼저 dedup하지 않으면 bulk_create의 ON CONFLICT DO UPDATE가
            # 같은 배치 안에서 동일 행을 두 번 건드리게 되어 DB 오류가 발생함
            deduped = {}
            for stock in stocks:
                ticker = stock.get('ticker')
                name = stock.get('name')

                if not ticker or not name:
                    continue

                deduped[ticker] = stock

            current_tickers = set()
            symbols = []

            for ticker, stock in deduped.items():
                name = stock.get('name')
                currency = stock.get('currency', 'KRW' if broker.country == Country.KOREA else 'USD')

                current_tickers.add(ticker)
                symbols.append(Symbol(
                    ticker=ticker,
                    broker=broker,
                    name=name,
                    currency=currency,
                    is_crypto=False,
                    is_delisted=False,
                )

            current_tickers = set(symbols_by_ticker.keys())
            symbols = list(symbols_by_ticker.values())

            # 종목 수가 많을 수 있어 건별 update_or_create 대신 일괄 upsert로 처리
            # (네트워크 왕복이 많으면 원격 DB 커넥션이 중간에 끊길 수 있음)
            BATCH_SIZE = 500
            for i in range(0, len(symbols), BATCH_SIZE):
                Symbol.objects.bulk_create(
                    symbols[i:i + BATCH_SIZE],
                    update_conflicts=True,
                    unique_fields=['broker', 'ticker'],
                    update_fields=['name', 'currency', 'is_crypto', 'is_delisted'],
                )

            # 상장폐지 처리: 현재 목록에 없지만 DB에 있는 종목
            delisted_tickers = existing_tickers - current_tickers
            if delisted_tickers:
                Symbol.objects.filter(
                    ticker__in=delisted_tickers,
                    broker=broker,
                    is_crypto=False
                ).update(is_delisted=True, updated_at=timezone.now())
                logger.info(f"상장폐지 처리된 종목 수: {len(delisted_tickers)}")

            logger.info(f"주식 종목 크롤링 완료: {len(current_tickers)}개 종목")

        except Exception as e:
            logger.error(f"주식 종목 업데이트 실패: {str(e)}")


class CryptoCrawler:
    """암호화폐 종목 크롤러"""
    
    @staticmethod
    def crawl_upbit_cryptos() -> List[Dict]:
        """Upbit 암호화폐 종목 크롤링"""
        try:
            logger.info("Upbit 암호화폐 종목 크롤링 시작")
            
            # Upbit에서 거래 가능한 모든 마켓 조회
            # pyupbit 0.2.x에서 get_market_all()이 제거되어 get_tickers(verbose=True)로 대체
            markets = pyupbit.get_tickers(verbose=True)
            
            if not markets:
                logger.warning("Upbit 마켓 정보를 가져올 수 없습니다.")
                return []
            
            cryptos = []
            for market in markets:
                market_name = market.get('market', '')
                korean_name = market.get('korean_name', '')
                english_name = market.get('english_name', '')
                
                if not market_name:
                    continue
                
                # 마켓 이름에서 티커 추출 (예: KRW-BTC -> BTC)
                ticker = market_name.split('-')[-1] if '-' in market_name else market_name
                
                # 화폐 단위 결정
                if market_name.startswith('KRW-'):
                    currency = Currency.KRW
                elif market_name.startswith('BTC-'):
                    currency = Currency.BTC
                elif market_name.startswith('USDT-'):
                    currency = Currency.USDT
                else:
                    currency = Currency.USDT
                
                cryptos.append({
                    'ticker': ticker,
                    'name': korean_name or english_name or ticker,
                    'currency': currency,
                    'market': market_name
                })
            
            logger.info(f"Upbit 암호화폐 종목 {len(cryptos)}개 수집 완료")
            return cryptos
            
        except Exception as e:
            logger.error(f"Upbit 암호화폐 종목 크롤링 실패: {str(e)}")
            return []
    
    @staticmethod
    def update_cryptos(broker: Broker, cryptos: List[Dict]):
        """암호화폐 종목 업데이트"""
        try:
            existing_tickers = set(
                Symbol.objects.filter(broker=broker, is_crypto=True)
                .values_list('ticker', flat=True)
            )

            # 같은 코인이 KRW/BTC/USDT 등 여러 마켓으로 상장돼 있으면 ticker가 동일해지므로
            # (예: KRW-BTC, USDT-BTC 모두 ticker="BTC") ticker 기준으로 중복을 제거해야
            # bulk_create의 ON CONFLICT가 같은 배치 내에서 동일 행을 두 번 갱신하는 오류를 피할 수 있음
            symbols_by_ticker = {}

            for crypto in cryptos:
                ticker = crypto.get('ticker')
                name = crypto.get('name')
                currency = crypto.get('currency', Currency.USDT)

                if not ticker or not name:
                    continue

                symbols_by_ticker[ticker] = Symbol(
                    ticker=ticker,
                    broker=broker,
                    name=name,
                    currency=currency,
                    is_crypto=True,
                    is_delisted=False,
                )

            current_tickers = set(symbols_by_ticker.keys())
            symbols = list(symbols_by_ticker.values())

            # 종목 수가 많을 수 있어 건별 update_or_create 대신 일괄 upsert로 처리
            BATCH_SIZE = 500
            for i in range(0, len(symbols), BATCH_SIZE):
                Symbol.objects.bulk_create(
                    symbols[i:i + BATCH_SIZE],
                    update_conflicts=True,
                    unique_fields=['broker', 'ticker'],
                    update_fields=['name', 'currency', 'is_crypto', 'is_delisted'],
                )

            # 상장폐지 처리: 현재 목록에 없지만 DB에 있는 종목
            delisted_tickers = existing_tickers - current_tickers
            if delisted_tickers:
                Symbol.objects.filter(
                    ticker__in=delisted_tickers,
                    broker=broker,
                    is_crypto=True
                ).update(is_delisted=True, updated_at=timezone.now())
                logger.info(f"상장폐지 처리된 암호화폐 종목 수: {len(delisted_tickers)}")

            logger.info(f"암호화폐 종목 크롤링 완료: {len(current_tickers)}개 종목")
            
        except Exception as e:
            logger.error(f"암호화폐 종목 업데이트 실패: {str(e)}")


def crawl_all_symbols():
    """모든 종목 크롤링"""
    try:
        logger.info("전체 종목 크롤링 시작")
        
        total_cryptos = 0
        total_stocks = 0
        
        # 암호화폐 거래소 크롤링
        crypto_brokers = Broker.objects.filter(is_crypto_exchange=True)
        for broker in crypto_brokers:
            if 'upbit' in broker.name.lower():
                cryptos = CryptoCrawler.crawl_upbit_cryptos()
                CryptoCrawler.update_cryptos(broker, cryptos)
                total_cryptos += len(cryptos)
        
        # 주식 브로커 크롤링
        stock_brokers = Broker.objects.filter(is_crypto_exchange=False)
        for broker in stock_brokers:
            if broker.country == Country.KOREA:
                stocks = StockCrawler.crawl_korea_stocks()
                StockCrawler.update_stocks(broker, stocks)
                total_stocks += len(stocks)
            elif broker.country == Country.USA:
                stocks = StockCrawler.crawl_us_stocks()
                StockCrawler.update_stocks(broker, stocks)
                total_stocks += len(stocks)
            elif broker.country == Country.JAPAN:
                stocks = StockCrawler.crawl_japan_stocks()
                StockCrawler.update_stocks(broker, stocks)
                total_stocks += len(stocks)
        
        logger.info(f"전체 종목 크롤링 완료 - 암호화폐: {total_cryptos}개, 주식: {total_stocks}개")
        
    except Exception as e:
        logger.error(f"종목 크롤링 중 오류 발생: {str(e)}")
        raise

