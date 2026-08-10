"""
국내(KR) 종목 분기 실적(재무제표) 크롤링 모듈.

PRD-0004 / ARCH-0002 / ADR-0003. DART `fnlttSinglAcntAll.json`은 corp_code 1건당 1회 호출이
필요하므로, 등록된 KR 심볼에 한해 저빈도(주 1회 정기 + 신규 정기보고서 공시 감지 시 즉시)로만
호출한다.

계정과목명은 보고서 종류(분기/반기/사업보고서)에 따라 순이익 항목명이 달라진다(실측 확인,
2026-08-10): 분기="분기순이익", 반기="반기순이익", 사업보고서="당기순이익". 매출액/영업이익은
보고서 종류와 무관하게 이름이 안정적이다. 계정명이 매칭되지 않으면 해당 종목만 skip하고 전체
배치는 계속 진행한다(ARCH-0002 Risks).
"""
import datetime
import logging
from decimal import Decimal, InvalidOperation
from typing import List, Optional, Tuple

from django.utils import timezone

from . import dart_client
from .models import Broker, Country, KrFinancialFact, Symbol

logger = logging.getLogger(__name__)

REVENUE_NAMES = {"매출액", "수익(매출액)"}
OPERATING_PROFIT_NAMES = {"영업이익", "영업이익(손실)"}
NET_PROFIT_NAMES = {
    "당기순이익", "당기순이익(손실)",
    "반기순이익", "반기순이익(손실)",
    "분기순이익", "분기순이익(손실)",
}
INCOME_STATEMENT_SJ_NAMES = {"손익계산서", "포괄손익계산서"}

# (reprt_code, 제출기한 월, 제출기한 일) — 실제 법정기한 근사치(ARCH-0002 Assumptions)
_REPORT_DEADLINES: List[Tuple[str, int, int]] = [
    ("11013", 5, 15),   # 1분기보고서
    ("11012", 8, 14),   # 반기보고서
    ("11014", 11, 14),  # 3분기보고서
    ("11011", 3, 31),   # 사업보고서(전년도 결산, 익년 3/31 기한)
]

BACKFILL_PERIODS = 5


def _recent_report_periods(as_of: datetime.date, count: int = BACKFILL_PERIODS) -> List[Tuple[str, str]]:
    """as_of 시점 기준 이미 제출기한이 지난 분기를 최신순 count개 (bsns_year, reprt_code)로 반환."""
    candidates = []
    for year in (as_of.year, as_of.year - 1, as_of.year - 2):
        for reprt_code, dl_month, dl_day in _REPORT_DEADLINES:
            bsns_year = year - 1 if reprt_code == "11011" else year
            deadline = datetime.date(year, dl_month, dl_day)
            if deadline <= as_of:
                candidates.append((deadline, str(bsns_year), reprt_code))
    candidates.sort(key=lambda x: x[0], reverse=True)

    seen = set()
    result: List[Tuple[str, str]] = []
    for _, bsns_year, reprt_code in candidates:
        key = (bsns_year, reprt_code)
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
        if len(result) >= count:
            break
    return result


def _to_decimal(raw: Optional[str]) -> Optional[Decimal]:
    if not raw:
        return None
    try:
        return Decimal(raw.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _period_amount(item: dict) -> Optional[Decimal]:
    """분기/반기 보고서(IS)는 `thstrm_amount`가 해당 3개월 단독 금액이고, 그 분기까지의
    누적 금액은 `thstrm_add_amount`에 별도로 온다(실측 확인, 2026-08-10 — 예: 2025 반기보고서
    매출액 thstrm_amount=74.57조(2분기 단독)이지만 thstrm_add_amount=153.7조(상반기 누적)).
    "반기 실적"/"3분기 실적"은 통상 누적 기준을 의미하므로 add_amount를 우선한다. 사업보고서
    (연간)나 1분기보고서는 add_amount가 thstrm_amount와 같거나 비어 있어 폴백이 안전하다.
    """
    add_amount = _to_decimal(item.get("thstrm_add_amount"))
    if add_amount is not None:
        return add_amount
    return _to_decimal(item.get("thstrm_amount"))


def _extract_facts(items: List[dict]) -> dict:
    """fnlttSinglAcntAll.json 응답 list에서 매출액/영업이익/당기순이익을 추출한다.

    손익계산서/포괄손익계산서 항목만 대상으로 하여 자본변동표 등에서의 동명 중복을 배제하고,
    분기 단독이 아닌 누적 금액(`_period_amount`)을 사용한다.
    """
    facts = {"revenue": None, "operating_profit": None, "net_profit": None}
    for item in items:
        if item.get("sj_nm") not in INCOME_STATEMENT_SJ_NAMES:
            continue
        account_nm = item.get("account_nm", "")
        amount = _period_amount(item)
        if amount is None:
            continue
        if account_nm in REVENUE_NAMES and facts["revenue"] is None:
            facts["revenue"] = amount
        elif account_nm in OPERATING_PROFIT_NAMES and facts["operating_profit"] is None:
            facts["operating_profit"] = amount
        elif account_nm in NET_PROFIT_NAMES and facts["net_profit"] is None:
            facts["net_profit"] = amount
    return facts


def sync_kr_earnings_for_symbol(symbol: Symbol, periods: Optional[List[Tuple[str, str]]] = None) -> int:
    """단일 심볼의 실적을 동기화한다. 반환값: upsert된 기간 수."""
    if not symbol.dart_corp_code:
        return 0

    periods = periods if periods is not None else _recent_report_periods(timezone.localdate())
    synced = 0
    for bsns_year, reprt_code in periods:
        items = dart_client.get_financial_facts(symbol.dart_corp_code, bsns_year, reprt_code, fs_div="CFS")
        if not items:
            # 연결재무제표 미제출(중소/비연결 대상 법인 등) 시 별도재무제표로 fallback
            items = dart_client.get_financial_facts(symbol.dart_corp_code, bsns_year, reprt_code, fs_div="OFS")
            fs_div = "OFS"
        else:
            fs_div = "CFS"

        if not items:
            continue  # 아직 제출되지 않은 분기 — 에러 아님

        facts = _extract_facts(items)
        if facts["revenue"] is None and facts["operating_profit"] is None and facts["net_profit"] is None:
            logger.warning(
                "DART 실적 계정과목 매칭 실패, skip: %s %s %s", symbol.ticker, bsns_year, reprt_code
            )
            continue

        KrFinancialFact.objects.update_or_create(
            symbol=symbol,
            bsns_year=bsns_year,
            reprt_code=reprt_code,
            defaults={"fs_div": fs_div, **facts},
        )
        synced += 1
    return synced


def sync_all_kr_earnings(symbol_ids: Optional[List[int]] = None) -> dict:
    """등록된 KR 심볼 전체(또는 지정된 symbol_ids만) 실적을 동기화한다.

    개별 심볼 실패는 전체 배치를 막지 않는다(ARCH-0002 NFR).
    """
    kr_brokers = Broker.objects.filter(country=Country.KOREA)
    queryset = Symbol.objects.filter(broker__in=kr_brokers, is_delisted=False, dart_corp_code__isnull=False)
    if symbol_ids is not None:
        queryset = queryset.filter(id__in=symbol_ids)

    synced_symbols = 0
    failed_symbols = 0
    for symbol in queryset:
        try:
            count = sync_kr_earnings_for_symbol(symbol)
            if count:
                synced_symbols += 1
        except dart_client.DartAPIError:
            raise  # 인증/한도 등 API 레벨 오류는 배치 전체를 중단(ARCH-0002 NFR)
        except Exception:
            logger.exception("KR 실적 동기화 실패, skip: %s", symbol.ticker)
            failed_symbols += 1

    return {"synced_symbols": synced_symbols, "failed_symbols": failed_symbols}
