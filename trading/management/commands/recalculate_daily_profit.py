"""
이미 저장된 일일 실현 손익(DailyRealizedProfit)을 현재 계산 로직으로 다시 계산하는 관리 명령
사용법: python manage.py recalculate_daily_profit --start 2026-08-01 --end 2026-09-22 [--dry-run] [--all-fields]

TASK-0016: total_buy_amount가 항상 0으로 저장되던 과거 행을 보정하기 위해 추가했다.
기존 행만 대상으로 하며 새 행은 만들지 않는다. 기본값으로는 total_buy_amount만 덮어쓰고,
나머지 필드(실현 손익 등)의 차이는 출력만 한다 — 덮어쓰려면 --all-fields를 준다.
"""
import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand, CommandError

from trading.models import DailyRealizedProfit
from trading.profit_calculator import ProfitCalculator

logger = logging.getLogger(__name__)

# 필드 -> 모델의 decimal_places
FIELDS = {'realized_profit': 2, 'realized_profit_rate': 4, 'total_buy_amount': 2, 'total_sell_amount': 2}
DEFAULT_WRITE_FIELDS = ('total_buy_amount',)


def as_stored(value: Decimal, places: int) -> Decimal:
    """DB(Postgres numeric)에 저장될 때와 같은 방식(half-up)으로 반올림한다. Python round()는 half-even이라 다르다."""
    return Decimal(value).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)


def _parse_date(value: str):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise CommandError(f"날짜 형식은 YYYY-MM-DD 입니다: {value}")


class Command(BaseCommand):
    help = (
        "기존 DailyRealizedProfit 행을 현재 계산 로직으로 재계산합니다. 기본값은 total_buy_amount만 갱신하고, "
        "--all-fields 시 모든 금액 필드를 갱신합니다. --dry-run 시 변경될 행만 출력합니다."
    )

    def add_arguments(self, parser):
        parser.add_argument("--start", required=True, help="시작일 (YYYY-MM-DD, 포함)")
        parser.add_argument("--end", required=True, help="종료일 (YYYY-MM-DD, 포함)")
        parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 변경될 행만 출력")
        parser.add_argument("--all-fields", action="store_true", help="total_buy_amount 외 필드도 덮어씀")

    def handle(self, *args, **options):
        start = _parse_date(options["start"])
        end = _parse_date(options["end"])
        if start > end:
            raise CommandError("--start가 --end보다 늦습니다.")
        dry_run = options["dry_run"]
        write_fields = tuple(FIELDS) if options["all_fields"] else DEFAULT_WRITE_FIELDS

        rows = DailyRealizedProfit.objects.filter(date__gte=start, date__lte=end).select_related('account')
        total = changed = other_field_diffs = failed = 0
        for row in rows:
            total += 1
            try:
                data = ProfitCalculator.calculate_daily_realized_profit(row.account, row.date)
                diffs = {
                    f: (getattr(row, f), as_stored(data[f], places))
                    for f, places in FIELDS.items()
                    if as_stored(getattr(row, f), places) != as_stored(data[f], places)
                }
                if not diffs:
                    continue
                if any(f not in write_fields for f in diffs):
                    other_field_diffs += 1
                to_write = [f for f in diffs if f in write_fields]
                self.stdout.write(f"account={row.account_id} date={row.date} " + ", ".join(
                    f"{f}: {old} -> {new}{'' if f in write_fields else ' (미갱신)'}"
                    for f, (old, new) in diffs.items()
                ))
                if not to_write:
                    continue
                changed += 1
                if not dry_run:
                    for f in to_write:
                        setattr(row, f, diffs[f][1])
                    row.save(update_fields=[*to_write, 'updated_at'])
            except Exception:
                failed += 1
                logger.exception('recalculate_daily_profit 실패: row=%s', row.pk)

        verb = "변경 예정" if dry_run else "갱신"
        self.stdout.write(self.style.SUCCESS(f"대상 {total}행 중 {changed}행 {verb} (갱신 필드: {', '.join(write_fields)})"))
        if other_field_diffs:
            self.stdout.write(self.style.WARNING(
                f"{other_field_diffs}행에서 갱신 대상이 아닌 필드도 차이가 있습니다(위 '(미갱신)' 표시). "
                "덮어쓰려면 --all-fields를 주되, 먼저 원인을 확인하세요."
            ))
        if failed:
            self.stdout.write(self.style.ERROR(f"{failed}행 처리 실패(로그 확인)"))
