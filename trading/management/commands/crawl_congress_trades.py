"""
미 하원의원 공시 거래(PTR) 크롤링 관리 명령
사용법: python manage.py crawl_congress_trades
"""
import logging

from django.core.management.base import BaseCommand

from trading.crawlers_congress import crawl_congress_trades

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "미 하원의원 공시 거래(PTR)를 크롤링해 DB에 반영하고, 영향받은 의원의 포트폴리오를 동기화합니다."

    def handle(self, *args, **options):
        result = crawl_congress_trades()
        self.stdout.write(self.style.SUCCESS(
            f"신규 거래: {result['new_trades']}건 / 갱신된 포트폴리오: {result['synced_members']}개"
        ))
