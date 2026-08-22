"""
TASK-0007: alert_sizing 파라미터 백테스트 (PRD-0005)
사용법:
  python manage.py backtest_alert_sizing --strategy-link 3 \
      --from 2026-06-01 --to 2026-08-01 --initial-cash 5000000 \
      --trade-percent 15 --max-position-weight-percent 30 --output report.json
"""
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from trading.models import StrategyLink
from trading.services.backtest import ConfigOverride, run_alert_sizing_backtest


def _parse_date(value: str, *, end_of_day: bool = False) -> datetime:
    dt = parse_datetime(value) or (
        datetime.combine(datetime.strptime(value, '%Y-%m-%d').date(), datetime.min.time())
    )
    if end_of_day and dt.time() == datetime.min.time():
        dt = dt.replace(hour=23, minute=59, second=59)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


class Command(BaseCommand):
    help = "지정한 StrategyLink의 과거 AlertTradePlan 이력을 재생해 사이징 파라미터 변경 효과를 비교합니다."

    def add_arguments(self, parser):
        parser.add_argument('--strategy-link', type=int, required=True, help='StrategyLink ID')
        parser.add_argument('--from', dest='from_date', required=True, help='YYYY-MM-DD (포함)')
        parser.add_argument('--to', dest='to_date', required=True, help='YYYY-MM-DD (포함)')
        parser.add_argument('--initial-cash', type=str, required=True, help='시뮬레이션 시작 현금')
        parser.add_argument('--trade-percent', type=str, default=None, help='override: 매매당 비중(%%)')
        parser.add_argument(
            '--max-position-weight-percent', type=str, default=None, help='override: 최대 포지션 비중(%%)'
        )
        parser.add_argument('--split-count', type=int, default=None, help='override: 분할 횟수')
        parser.add_argument('--no-mark-price', action='store_true', help='최종 평가액 조회(FDR/pyupbit) 생략')
        parser.add_argument('--output', type=str, default=None, help='JSON 결과 저장 경로')

    def handle(self, *args, **options):
        try:
            strategy_link = StrategyLink.objects.select_related('strategy', 'account').get(
                pk=options['strategy_link']
            )
        except StrategyLink.DoesNotExist:
            raise CommandError(f"StrategyLink {options['strategy_link']}을(를) 찾을 수 없습니다.")

        try:
            initial_cash = Decimal(options['initial_cash'])
            override = ConfigOverride(
                trade_percent=Decimal(options['trade_percent']) if options['trade_percent'] else None,
                max_position_weight_percent=(
                    Decimal(options['max_position_weight_percent'])
                    if options['max_position_weight_percent']
                    else None
                ),
                split_count=options['split_count'],
            )
        except InvalidOperation as exc:
            raise CommandError(f"숫자 파싱 실패: {exc}")

        start = _parse_date(options['from_date'])
        end = _parse_date(options['to_date'], end_of_day=True)

        report = run_alert_sizing_backtest(
            strategy_link,
            start,
            end,
            initial_cash,
            override=override,
            fetch_mark_prices=not options['no_mark_price'],
        )

        self.stdout.write(
            f"StrategyLink {strategy_link.id} ({strategy_link.strategy.title}) "
            f"| Plan {len(report.replays)}건 재생 ({start.date()} ~ {end.date()})"
        )
        self.stdout.write(f"  실제 경로 최종 평가액: {report.actual_final_value}")
        self.stdout.write(f"  시뮬 경로 최종 평가액: {report.sim_final_value}")
        errors = [r for r in report.replays if r.sim_error]
        if errors:
            self.stdout.write(self.style.WARNING(f"  시뮬레이션 실패/스킵 {len(errors)}건"))
            for r in errors:
                self.stdout.write(f"    - Plan {r.plan_id} ({r.symbol_ticker}): {r.sim_error}")

        if options['output']:
            with open(options['output'], 'w', encoding='utf-8') as f:
                json.dump(report.as_dict(), f, ensure_ascii=False, indent=2)
            self.stdout.write(self.style.SUCCESS(f"리포트 저장: {options['output']}"))
