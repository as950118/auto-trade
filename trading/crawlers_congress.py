"""
미 하원(House of Representatives) 의원 공시 거래(PTR, Periodic Transaction Report) 크롤링 모듈.

데이터 출처는 하원 서기실(Clerk of the House) 공식 사이트로, STOCK Act에 따라 공개 의무화된 정보임:
  - 연간 벌크 인덱스: https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.txt
  - 개별 PTR PDF: https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{docid}.pdf

PTR PDF는 표 구분선이 없는 자유배치 폼이라 pdfplumber의 표 추출이 아닌, 텍스트 정규식 파싱을 사용한다.
상원(Senate)은 efdsearch.senate.gov가 봇 차단(Akamai)되어 있어 이번 크롤러 범위에서 제외한다.
"""
import logging
import re
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

INDEX_URL_TEMPLATE = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.txt"
PTR_PDF_URL_TEMPLATE = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{docid}.pdf"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# FilingType == 'P' 가 Periodic Transaction Report(실제 매매 공시). 그 외(O/C/D/W/X/A 등)는 연례/후보자 신고 등.
PTR_FILING_TYPE = "P"

TRANSACTION_TYPE_MAP = {
    "P": "BUY",
    "S": "SELL",
    "E": "EXCHANGE",
}

ROW_RE = re.compile(
    r"([PSE])\s*(?:\([a-zA-Z]+\))?\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+\$([\d,]+)"
    r"(?:\s*-\s*.{0,40}?\$([\d,]+))?"
)
TICKER_RE = re.compile(r"\(([A-Z]{1,6})\)")
TICKER_DISTANCE_THRESHOLD = 100


def _fetch_text(url: str, timeout: int = 20) -> Optional[str]:
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        logger.warning("congress crawl: fetch failed for %s: %s", url, e)
        return None


def _fetch_bytes(url: str, timeout: int = 30) -> Optional[bytes]:
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        logger.warning("congress crawl: fetch failed for %s: %s", url, e)
        return None


def fetch_ptr_index(year: int) -> List[Dict]:
    """지정 연도의 하원 공시 인덱스를 가져와 FilingType='P'(PTR)인 건만 반환한다."""
    url = INDEX_URL_TEMPLATE.format(year=year)
    text = _fetch_text(url)
    if not text:
        return []

    lines = text.splitlines()
    if not lines:
        return []

    header = lines[0].split("\t")
    entries = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) != len(header):
            continue
        row = dict(zip(header, cells))
        if row.get("FilingType") != PTR_FILING_TYPE:
            continue
        entries.append(row)
    return entries


def _parse_amount(amount_min_raw: str, amount_max_raw: Optional[str]) -> Tuple[Decimal, Decimal]:
    amount_min = Decimal(amount_min_raw.replace(",", ""))
    amount_max = Decimal(amount_max_raw.replace(",", "")) if amount_max_raw else amount_min
    return amount_min, amount_max


def parse_ptr_pdf(pdf_bytes: bytes) -> List[Dict]:
    """
    PTR PDF 바이트에서 거래 목록을 추출한다.
    반환: [{'raw_ticker': str, 'transaction_type': 'BUY'/'SELL'/'EXCHANGE',
            'transaction_date': 'MM/DD/YYYY', 'amount_min': Decimal, 'amount_max': Decimal}, ...]
    자산명(asset_description)은 폼 줄바꿈 위치가 문서마다 달라 신뢰 있게 복원할 수 없으므로
    별도로 파싱하지 않는다 — 포트폴리오 계산은 raw_ticker/symbol 매칭에만 의존한다.
    """
    import pdfplumber
    from io import BytesIO

    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
            text = " ".join((page.extract_text() or "") for page in pdf.pages)
    except Exception as e:
        logger.warning("congress crawl: pdf parse failed: %s", e)
        return []

    flat = re.sub(r"\s+", " ", text)
    tickers = [(m.group(1), m.start()) for m in TICKER_RE.finditer(flat)]
    rows = list(ROW_RE.finditer(flat))

    results = []
    for rm in rows:
        type_code, tx_date, _notif_date, amount_min_raw, amount_max_raw = rm.groups()
        best_ticker = None
        best_distance = None
        for ticker, pos in tickers:
            distance = abs(pos - rm.start())
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_ticker = ticker
        if best_distance is None or best_distance > TICKER_DISTANCE_THRESHOLD:
            best_ticker = None

        try:
            amount_min, amount_max = _parse_amount(amount_min_raw, amount_max_raw)
        except Exception:
            continue

        results.append({
            "raw_ticker": best_ticker or "",
            "transaction_type": TRANSACTION_TYPE_MAP.get(type_code, "BUY"),
            "transaction_date": tx_date,
            "amount_min": amount_min,
            "amount_max": amount_max,
        })
    return results


def _resolve_symbol(ticker: str):
    from .models import Country, Symbol

    if not ticker:
        return None
    return (
        Symbol.objects.filter(ticker=ticker, broker__country=Country.USA, is_crypto=False, is_delisted=False)
        .first()
    )


def ingest_house_trades(years: Optional[List[int]] = None) -> Tuple[int, Set[int]]:
    """
    하원 PTR을 크롤링하여 신규 CongressTrade를 반영한다.
    반환: (신규 반영 건수, 영향받은 CongressMember id 집합)
    """
    from datetime import datetime

    from .models import CongressChamber, CongressMember, CongressTrade

    if years is None:
        current_year = timezone.now().year
        years = [current_year, current_year - 1]

    new_trade_count = 0
    touched_member_ids: Set[int] = set()

    for year in years:
        entries = fetch_ptr_index(year)
        logger.info("congress crawl: %s PTR filings found for year=%s", len(entries), year)

        for entry in entries:
            doc_id = entry.get("DocID", "").strip()
            if not doc_id:
                continue
            pdf_url = PTR_PDF_URL_TEMPLATE.format(year=year, docid=doc_id)

            if CongressTrade.objects.filter(source_url=pdf_url).exists():
                continue

            pdf_bytes = _fetch_bytes(pdf_url)
            if not pdf_bytes:
                continue

            transactions = parse_ptr_pdf(pdf_bytes)
            if not transactions:
                continue

            first_name = entry.get("First", "").strip()
            last_name = entry.get("Last", "").strip()
            full_name = f"{first_name} {last_name}".strip()
            state = entry.get("StateDst", "")[:2]

            member, _ = CongressMember.objects.get_or_create(
                name=full_name,
                chamber=CongressChamber.HOUSE,
                defaults={"state": state},
            )

            disclosure_date = None
            filing_date_raw = entry.get("FilingDate", "").strip()
            if filing_date_raw:
                try:
                    disclosure_date = datetime.strptime(filing_date_raw, "%m/%d/%Y").date()
                except ValueError:
                    disclosure_date = None

            now = timezone.now()
            for tx in transactions:
                try:
                    transaction_date = datetime.strptime(tx["transaction_date"], "%m/%d/%Y").date()
                except ValueError:
                    continue

                symbol = _resolve_symbol(tx["raw_ticker"])
                _, created = CongressTrade.objects.get_or_create(
                    member=member,
                    raw_ticker=tx["raw_ticker"],
                    transaction_type=tx["transaction_type"],
                    transaction_date=transaction_date,
                    amount_min=tx["amount_min"],
                    amount_max=tx["amount_max"],
                    defaults={
                        "symbol": symbol,
                        "disclosure_date": disclosure_date,
                        "source_url": pdf_url,
                        "crawled_at": now,
                    },
                )
                if created:
                    new_trade_count += 1
                    touched_member_ids.add(member.id)

    return new_trade_count, touched_member_ids


def crawl_congress_trades() -> Dict:
    """스케줄러/관리커맨드에서 호출하는 진입점. 신규 거래 반영 + 영향받은 의원 포트폴리오 동기화."""
    from .services.congress_portfolio import sync_member_portfolio_resilient
    from .models import CongressMember

    new_trades, touched_member_ids = ingest_house_trades()

    synced_members = 0
    for member in CongressMember.objects.filter(id__in=touched_member_ids):
        try:
            sync_member_portfolio_resilient(member)
            synced_members += 1
        except Exception:
            logger.exception("congress crawl: portfolio sync failed for member=%s", member.id)

    return {"new_trades": new_trades, "synced_members": synced_members}
