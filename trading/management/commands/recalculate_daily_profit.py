"""
이미 저장된 일일 실현 손익(DailyRealizedProfit)을 현재 계산 로직으로 다시 계산하는 관리 명령
사용법: python manage.py recalculate_daily_profit --start 2026-08-01 --end 2026-09-22 [--dry-run]

TASK-0016: total_buy_amount가 항상 0으로 저장되던 과거 행을 보정하기 위해 추가했다.
기존 행만 대상으로 하며 새 행은 만들지 않는다.
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from trading.models import DailyRealizedProfit
from trading.profit_calculator import ProfitCalculator

# 필드 -> 모델의 decimal_places (저장 시 반올림되는 자릿수까지만 비교)
FIELDS = {'realized_profit': 2, 'realized_profit_rate': 4, 'total_buy_amount': 2, 'total_sell_amount': 2}


def _parse_date(value: str):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise CommandError(f"날짜 형식은 YYYY-MM-DD 입니다: {value}")


class Command(BaseCommand):
    help = "기존 DailyRealizedProfit 행을 현재 계산 로직으로 재계산합니다. --dry-run 시 변경될 행만 출력합니다."

    def add_arguments(self, parser):
        parser.add_argument("--start", required=True, help="시작일 (YYYY-MM-DD, 포함)")
        parser.add_argument("--end", required=True, help="종료일 (YYYY-MM-DD, 포함)")
        parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 변경될 행만 출력")

    def handle(self, *args, **options):
        start = _parse_date(options["start"])
        end = _parse_date(options["end"])
        if start > end:
            raise CommandError("--start가 --end보다 늦습니다.")
        dry_run = options["dry_run"]

        rows = DailyRealizedProfit.objects.filter(date__gte=start, date__lte=end).select_related('account')
        changed = 0
        for row in rows:
            data = ProfitCalculator.calculate_daily_realized_profit(row.account, row.date)
            diffs = {
                f: (getattr(row, f), data[f])
                for f, places in FIELDS.items()
                if round(getattr(row, f), places) != round(data[f], places)
            }
            if not diffs:
                continue
            changed += 1
            self.stdout.write(f"account={row.account_id} date={row.date} " + ", ".join(
                f"{f}: {old} -> {new}" for f, (old, new) in diffs.items()
            ))
            if not dry_run:
                ProfitCalculator.update_daily_realized_profit(row.account, row.date)

        verb = "변경 예정" if dry_run else "갱신"
        self.stdout.write(self.style.SUCCESS(f"대상 {rows.count()}행 중 {changed}행 {verb}"))
