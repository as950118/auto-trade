"""
거래소 수수료 환급(페이백) 크롤링 모듈
"""
import logging
import re
from decimal import Decimal
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup
from django.utils import timezone

logger = logging.getLogger(__name__)

# 크롤링 대상 URL (예: tethermax 스타일 비교 페이지)
DEFAULT_SOURCE_URL = "https://tethermax.io/ko"

# User-Agent to avoid blocking
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


def _parse_decimal(text: Optional[str]) -> Optional[Decimal]:
    """문자열에서 숫자만 추출해 Decimal로 반환"""
    if not text or not isinstance(text, str):
        return None
    text = text.strip().replace(",", "").replace(" ", "")
    # 퍼센트, 만원 등 제거
    text = re.sub(r"[%\s만원원]", "", text)
    match = re.search(r"-?\d+\.?\d*", text)
    if match:
        try:
            return Decimal(match.group())
        except Exception:
            pass
    return None


def _parse_int(text: Optional[str]) -> Optional[int]:
    """문자열에서 정수 추출 (예: 183만원 -> 1830000)"""
    if not text or not isinstance(text, str):
        return None
    text = text.strip().replace(",", "").replace(" ", "")
    # "183만원" -> 183, "만" 있으면 * 10000
    if "만" in text:
        match = re.search(r"(\d+\.?\d*)\s*만", text)
        if match:
            try:
                return int(Decimal(match.group(1)) * 10000)
            except Exception:
                pass
    match = re.search(r"(\d+)", text)
    if match:
        try:
            return int(match.group(1))
        except Exception:
            pass
    return None


def _fetch_html(url: str, timeout: int = 15) -> Optional[str]:
    """URL에서 HTML 문자열 가져오기"""
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        logger.warning("fee rebate crawl fetch failed: %s", e)
        return None


def _parse_table_from_html(html: str) -> List[Dict]:
    """
    HTML에서 테이블을 파싱해 거래소별 페이백 데이터 리스트 반환.
    컬럼 순서: 거래소명, 페이백률, 거래할인율, 지정가, 시장가, 1인평균페이백, 태그 등.
    테이블이 없거나 구조가 다르면 빈 리스트 반환.
    """
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    rows_data = []

    for table in tables:
        thead = table.find("thead")
        tbody = table.find("tbody") or table
        header_cells = []
        if thead:
            header_row = thead.find("tr")
            if header_row:
                header_cells = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
        rows = tbody.find_all("tr") if tbody else []
        for tr in rows:
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            # 첫 컬럼이 거래소명(영문/한글), 이후 숫자들
            name = cells[0].strip()
            if not name or len(name) > 80:
                continue
            # 숫자만 있는 이름 스킵 (헤더 행일 수 있음)
            if re.match(r"^[\d.%\s,-]+$", name):
                continue
            rebate = _parse_decimal(cells[1]) if len(cells) > 1 else None
            discount = _parse_decimal(cells[2]) if len(cells) > 2 else None
            limit_fee = _parse_decimal(cells[3]) if len(cells) > 3 else None
            market_fee = _parse_decimal(cells[4]) if len(cells) > 4 else None
            avg_krw = _parse_int(cells[5]) if len(cells) > 5 else None
            if len(cells) > 6 and cells[6]:
                tags = [t.strip() for t in re.split(r"[,/]", cells[6]) if t.strip()]
            else:
                tags = []
            rows_data.append({
                "exchange_name": name[:100],
                "rebate_pct": rebate,
                "trading_discount_pct": discount,
                "limit_order_fee_pct": limit_fee,
                "market_order_fee_pct": market_fee,
                "avg_payback_krw": avg_krw,
                "tags": tags[:20],
            })
        if rows_data:
            break
    return rows_data


def crawl_fee_rebates(source_url: Optional[str] = None) -> int:
    """
    지정 URL에서 거래소 수수료 환급 정보를 크롤링해 DB에 반영(upsert)합니다.
    exchange_name 기준으로 기존 레코드가 있으면 업데이트, 없으면 생성합니다.
    반환값: 생성/업데이트된 레코드 수.
    """
    from .models import ExchangeFeeRebate

    url = source_url or DEFAULT_SOURCE_URL
    html = _fetch_html(url)
    if not html:
        logger.warning("fee rebate crawl: no html from %s", url)
        return 0

    rows = _parse_table_from_html(html)
    if not rows:
        logger.info("fee rebate crawl: no table data parsed from %s (page may be JS-rendered)", url)
        return 0

    now = timezone.now()
    updated_count = 0
    for row in rows:
        name = row.get("exchange_name")
        if not name:
            continue
        defaults = {
            "rebate_pct": row.get("rebate_pct"),
            "trading_discount_pct": row.get("trading_discount_pct"),
            "limit_order_fee_pct": row.get("limit_order_fee_pct"),
            "market_order_fee_pct": row.get("market_order_fee_pct"),
            "avg_payback_krw": row.get("avg_payback_krw"),
            "tags": row.get("tags") or [],
            "source_url": url,
            "crawled_at": now,
            "is_active": True,
        }
        _, created = ExchangeFeeRebate.objects.update_or_create(
            exchange_name=name,
            defaults=defaults,
        )
        if created:
            updated_count += 1
        else:
            updated_count += 1
    logger.info("fee rebate crawl: %s rows processed, %s updated/created", len(rows), updated_count)
    return updated_count


def seed_sample_fee_rebates() -> int:
    """
    샘플 거래소 페이백 데이터를 DB에 넣습니다.
    레코드가 하나도 없을 때만 사용하거나, 테스트/데모용으로 호출합니다.
    반환값: 생성된 레코드 수.
    """
    from .models import ExchangeFeeRebate

    if ExchangeFeeRebate.objects.exists():
        return 0

    samples = [
        {
            "exchange_name": "Binance",
            "rebate_pct": Decimal("40"),
            "trading_discount_pct": Decimal("10"),
            "limit_order_fee_pct": Decimal("0.02"),
            "market_order_fee_pct": Decimal("0.04"),
            "avg_payback_krw": 1200000,
            "tags": ["최상위 거래소"],
        },
        {
            "exchange_name": "OKX",
            "rebate_pct": Decimal("65"),
            "trading_discount_pct": Decimal("20"),
            "limit_order_fee_pct": Decimal("0.02"),
            "market_order_fee_pct": Decimal("0.05"),
            "avg_payback_krw": 2580000,
            "tags": ["신규 제휴"],
        },
        {
            "exchange_name": "Bybit",
            "rebate_pct": Decimal("50"),
            "trading_discount_pct": Decimal("15"),
            "limit_order_fee_pct": Decimal("0.01"),
            "market_order_fee_pct": Decimal("0.06"),
            "avg_payback_krw": 1830000,
            "tags": ["부스트 혜택"],
        },
        {
            "exchange_name": "Bitget",
            "rebate_pct": Decimal("54"),
            "trading_discount_pct": Decimal("0"),
            "limit_order_fee_pct": Decimal("0.02"),
            "market_order_fee_pct": Decimal("0.06"),
            "avg_payback_krw": None,
            "tags": [],
        },
        {
            "exchange_name": "Gate.io",
            "rebate_pct": Decimal("75"),
            "trading_discount_pct": Decimal("25"),
            "limit_order_fee_pct": Decimal("0.015"),
            "market_order_fee_pct": Decimal("0.05"),
            "avg_payback_krw": None,
            "tags": ["최상위 거래소"],
        },
    ]
    for s in samples:
        ExchangeFeeRebate.objects.get_or_create(
            exchange_name=s["exchange_name"],
            defaults={
                **s,
                "source_url": "",
                "crawled_at": None,
                "is_active": True,
            },
        )
    logger.info("fee rebate seed: %s sample records created", len(samples))
    return len(samples)
