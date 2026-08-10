"""
국내(KR) 종목 DART 공시 크롤링 모듈.

PRD-0004 / ARCH-0002 / ADR-0003 — `crawlers_congress.py`(미 의회 공시)와 완전히 별개 모듈이며
모델도 공유하지 않는다(`KrDisclosure` vs `CongressTrade`).

수집 전략: corp_code 없이 날짜 범위로 그날 전체 상장사 공시를 조회(list.json)한 뒤, 응답의
`stock_code`가 우리 시스템에 등록된 KR 심볼(Symbol.ticker)과 일치하는 건만 저장한다. 이 방식은
등록 심볼 수와 무관하게 API 호출 수가 상수이므로 심볼별 폴링보다 요청 한도에 안전하다(ADR-0003).
"""
import logging
from typing import Dict, List

from django.utils import timezone

from . import dart_client
from .models import Broker, Country, KrDisclosure, Symbol

logger = logging.getLogger(__name__)

DART_SOURCE_URL_TEMPLATE = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

# 이 보고서명이 포함된 공시가 감지되면 실적(KrFinancialFact) 즉시 재동기화 대상이다.
EARNINGS_REPORT_KEYWORDS = ("사업보고서", "분기보고서", "반기보고서")


def _kr_symbols_by_ticker() -> Dict[str, Symbol]:
    """국가가 KR인 브로커에 속한, 상장폐지되지 않은 심볼을 ticker -> Symbol로 반환."""
    kr_brokers = Broker.objects.filter(country=Country.KOREA)
    symbols = Symbol.objects.filter(broker__in=kr_brokers, is_delisted=False)
    return {s.ticker: s for s in symbols}


def sync_kr_corp_codes() -> int:
    """dart_corp_code가 비어있는 KR 심볼에 한해 corpCode.xml을 내려받아 매핑을 채운다.

    매핑 대상이 없으면 API를 호출하지 않는다(전종목 XML은 비용이 크므로).
    반환값: 새로 매핑된 심볼 수.
    """
    kr_brokers = Broker.objects.filter(country=Country.KOREA)
    unmapped = list(Symbol.objects.filter(broker__in=kr_brokers, dart_corp_code__isnull=True, is_delisted=False))
    if not unmapped:
        return 0

    corp_map = dart_client.fetch_corp_code_map()
    updated = 0
    for symbol in unmapped:
        entry = corp_map.get(symbol.ticker)
        if not entry:
            continue
        symbol.dart_corp_code = entry["corp_code"]
        symbol.save(update_fields=["dart_corp_code"])
        updated += 1
    logger.info("DART corp_code 매핑: 대상 %d건 중 %d건 매핑 완료", len(unmapped), updated)
    return updated


def crawl_kr_disclosures(target_date=None) -> dict:
    """지정일(기본 오늘)의 전체 공시를 조회해 등록된 KR 심볼과 매칭되는 건만 저장한다.

    반환값: {'new_disclosures': int, 'earnings_trigger_symbols': List[int]}
    """
    target_date = target_date or timezone.localdate()
    date_str = target_date.strftime("%Y%m%d")

    ticker_map = _kr_symbols_by_ticker()
    if not ticker_map:
        logger.info("등록된 KR 심볼이 없어 공시 크롤링을 건너뜁니다")
        return {"new_disclosures": 0, "earnings_trigger_symbols": []}

    new_count = 0
    earnings_trigger_symbol_ids: List[int] = []
    page_no = 1
    total_page = 1

    while page_no <= total_page:
        data = dart_client.get_disclosures(bgn_de=date_str, end_de=date_str, page_no=page_no, page_count=100)
        if data.get("status") == dart_client.STATUS_NO_DATA:
            break
        total_page = data.get("total_page", 1)

        for item in data.get("list", []):
            stock_code = (item.get("stock_code") or "").strip()
            symbol = ticker_map.get(stock_code)
            if not symbol:
                continue

            rcept_no = item["rcept_no"]
            report_name = item.get("report_nm", "")
            _, created = KrDisclosure.objects.update_or_create(
                rcept_no=rcept_no,
                defaults={
                    "symbol": symbol,
                    "corp_code": item.get("corp_code", ""),
                    "corp_name": item.get("corp_name", ""),
                    "report_name": report_name,
                    "rcept_dt": timezone.datetime.strptime(item["rcept_dt"], "%Y%m%d").date(),
                    "source_url": DART_SOURCE_URL_TEMPLATE.format(rcept_no=rcept_no),
                },
            )
            if created:
                new_count += 1
                if any(keyword in report_name for keyword in EARNINGS_REPORT_KEYWORDS):
                    earnings_trigger_symbol_ids.append(symbol.id)

        page_no += 1

    return {
        "new_disclosures": new_count,
        "earnings_trigger_symbols": earnings_trigger_symbol_ids,
    }
