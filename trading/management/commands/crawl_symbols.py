"""
종목(주식/암호화폐) 크롤링 수동 실행 명령
사용법:
  python manage.py crawl_symbols                # 전체 크롤링 (암호화폐 + 국가별 주식)
  python manage.py crawl_symbols --country JP    # 일본 주식만
  python manage.py crawl_symbols --country US    # 미국 주식만
  python manage.py crawl_symbols --country KR    # 한국 주식만
  python manage.py crawl_symbols --crypto        # 암호화폐만
"""
import logging

from django.core.management.base import BaseCommand

from trading.models import Broker, Country
from trading.crawlers import StockCrawler, CryptoCrawler, crawl_all_symbols

logger = logging.getLogger(__name__)

COUNTRY_CRAWLERS = {
    Country.KOREA: StockCrawler.crawl_korea_stocks,
    Country.USA: StockCrawler.crawl_us_stocks,
    Country.JAPAN: StockCrawler.crawl_japan_stocks,
}

# 크롤링 대상 국가에 브로커가 하나도 없을 때 자동으로 만들어줄 기본 브로커
# (브로커가 없으면 크롤러 자체는 정상 동작해도 결과를 저장할 곳이 없어 조용히 0건 처리됨)
COUNTRY_DEFAULT_BROKER = {
    Country.KOREA: ('KR_MARKET', '한국 주식시장'),
    Country.USA: ('US_MARKET', '미국 주식시장'),
    Country.JAPAN: ('JP_MARKET', '일본 주식시장'),
}


class Command(BaseCommand):
    help = "종목(주식/암호화폐) 목록을 수동으로 크롤링하여 DB에 반영합니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--country",
            type=str,
            choices=[Country.KOREA, Country.USA, Country.JAPAN],
            default=None,
            help="특정 국가의 주식만 크롤링 (KR/US/JP). --crypto와 함께 생략하면 전체를 크롤링합니다.",
        )
        parser.add_argument(
            "--crypto",
            action="store_true",
            help="암호화폐 종목만 크롤링",
        )

    def handle(self, *args, **options):
        country = options.get("country")
        crypto_only = options.get("crypto", False)

        if not country and not crypto_only:
            self.stdout.write("전체 종목(암호화폐 + 국가별 주식) 크롤링을 시작합니다...")
            crawl_all_symbols()
            self.stdout.write(self.style.SUCCESS("전체 종목 크롤링 완료"))
            return

        if crypto_only:
            crypto_brokers = Broker.objects.filter(is_crypto_exchange=True)
            for broker in crypto_brokers:
                if 'upbit' in broker.name.lower():
                    cryptos = CryptoCrawler.crawl_upbit_cryptos()
                    CryptoCrawler.update_cryptos(broker, cryptos)
                    self.stdout.write(self.style.SUCCESS(f"[{broker.name}] 암호화폐 {len(cryptos)}개 반영"))

        if country:
            crawler_fn = COUNTRY_CRAWLERS[country]
            brokers = list(Broker.objects.filter(country=country, is_crypto_exchange=False))

            if not brokers:
                code, name = COUNTRY_DEFAULT_BROKER[country]
                broker = Broker.objects.create(
                    code=code, name=name, country=country, is_crypto_exchange=False,
                )
                brokers = [broker]
                self.stdout.write(self.style.WARNING(
                    f"'{country}' 국가용 브로커가 없어 새로 생성했습니다: {broker.name} ({broker.code})"
                ))

            stocks = crawler_fn()
            if not stocks:
                self.stdout.write(self.style.ERROR(f"'{country}' 주식 종목을 수집하지 못했습니다. 로그를 확인하세요."))
                return

            for broker in brokers:
                StockCrawler.update_stocks(broker, stocks)
                self.stdout.write(self.style.SUCCESS(f"[{broker.name}] 주식 {len(stocks)}개 반영"))
