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
                    stock_list = fdr.StockListing('KRX')
                    
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
            # 미국 주식 종목은 공개 API나 데이터베이스 사용
            # 예: Alpha Vantage, Yahoo Finance 등
            
            logger.info("미국 주식 종목 크롤링 시작")
            
            # 실제 구현 필요
            return []
            
        except Exception as e:
            logger.error(f"미국 주식 종목 크롤링 실패: {str(e)}")
            return []
    
    @staticmethod
    def update_stocks(broker: Broker, stocks: List[Dict]):
        """주식 종목 업데이트"""
        try:
            existing_tickers = set(
                Symbol.objects.filter(broker=broker, is_crypto=False)
                .values_list('ticker', flat=True)
            )
            
            current_tickers = set()
            
            for stock in stocks:
                ticker = stock.get('ticker')
                name = stock.get('name')
                currency = stock.get('currency', 'KRW' if broker.country == Country.KOREA else 'USD')
                
                if not ticker or not name:
                    continue
                
                current_tickers.add(ticker)
                
                symbol, created = Symbol.objects.update_or_create(
                    ticker=ticker,
                    broker=broker,
                    defaults={
                        'name': name,
                        'currency': currency,
                        'is_crypto': False,
                        'is_delisted': False,
                        'updated_at': timezone.now()
                    }
                )
                
                if created:
                    logger.info(f"새 주식 종목 추가: {ticker} ({name})")
                else:
                    logger.debug(f"주식 종목 업데이트: {ticker} ({name})")
            
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
            markets = pyupbit.get_market_all()
            
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
            
            current_tickers = set()
            
            for crypto in cryptos:
                ticker = crypto.get('ticker')
                name = crypto.get('name')
                currency = crypto.get('currency', Currency.USDT)
                
                if not ticker or not name:
                    continue
                
                current_tickers.add(ticker)
                
                symbol, created = Symbol.objects.update_or_create(
                    ticker=ticker,
                    broker=broker,
                    defaults={
                        'name': name,
                        'currency': currency,
                        'is_crypto': True,
                        'is_delisted': False,
                        'updated_at': timezone.now()
                    }
                )
                
                if created:
                    logger.info(f"새 암호화폐 종목 추가: {ticker} ({name})")
                else:
                    logger.debug(f"암호화폐 종목 업데이트: {ticker} ({name})")
            
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
        
        logger.info(f"전체 종목 크롤링 완료 - 암호화폐: {total_cryptos}개, 주식: {total_stocks}개")
        
    except Exception as e:
        logger.error(f"종목 크롤링 중 오류 발생: {str(e)}")
        raise

