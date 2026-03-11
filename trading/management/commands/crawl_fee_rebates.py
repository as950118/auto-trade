"""
거래소 수수료 환급(페이백) 크롤링 관리 명령
사용법: python manage.py crawl_fee_rebates [--url URL] [--seed]
"""
import logging

from django.core.management.base import BaseCommand

from trading.crawlers_fee_rebate import crawl_fee_rebates, seed_sample_fee_rebates

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "거래소 수수료 환급(페이백) 정보를 크롤링해 DB에 반영합니다. --seed 시 데이터가 없을 때만 샘플 데이터를 넣습니다."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            type=str,
            default=None,
            help="크롤링할 URL (기본값: tethermax.io/ko)",
        )
        parser.add_argument(
            "--seed",
            action="store_true",
            help="레코드가 없을 때 샘플 데이터 삽입",
        )

    def handle(self, *args, **options):
        url = options.get("url")
        do_seed = options.get("seed", False)

        if do_seed:
            n = seed_sample_fee_rebates()
            if n:
                self.stdout.write(self.style.SUCCESS(f"샘플 데이터 {n}건 생성됨"))
            else:
                self.stdout.write("이미 데이터가 있거나 샘플 생성 건수 0")

        count = crawl_fee_rebates(source_url=url)
        self.stdout.write(self.style.SUCCESS(f"크롤링 처리: {count}건 반영됨"))
