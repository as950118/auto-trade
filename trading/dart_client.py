"""
DART(전자공시) OpenAPI 클라이언트.

PRD-0004 / ARCH-0002 / ADR-0003 기준:
  - 공시(list.json)는 corp_code 없이 날짜 범위로 그날 전체 공시를 조회한다(전수조회, 종목 수와
    무관한 상수 비용). 응답의 `stock_code`로 우리 시스템의 Symbol.ticker와 직접 매칭 가능하다.
  - 재무제표(fnlttSinglAcntAll.json)는 corp_code 1건당 1회 호출이 필요하다.
  - corp_code 매핑은 정적 corpCode.xml(전종목)을 내려받아 캐시한다(자주 갱신하지 않음).

`CongressTrade`/`crawlers_congress.py`와 모델·모듈을 공유하지 않는다(ADR-0003).
"""
import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from typing import Dict, List, Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DART_BASE_URL = "https://opendart.fss.or.kr/api"
DEFAULT_TIMEOUT = 20

# list.json 상태 코드: 000=정상, 013=조회된 데이터 없음(에러 아님)
STATUS_OK = "000"
STATUS_NO_DATA = "013"


class DartAPIError(Exception):
    """DART API가 정상/무결과 이외의 상태코드를 반환했을 때(인증 실패, 사용한도 초과 등)"""

    def __init__(self, status: str, message: str):
        self.status = status
        self.message = message
        super().__init__(f"DART API error {status}: {message}")


def _get_api_key() -> str:
    api_key = getattr(settings, "DART_API_KEY", "") or ""
    if not api_key:
        raise DartAPIError("NO_KEY", "DART_API_KEY가 설정되지 않았습니다 (.env 확인)")
    return api_key


def get_disclosures(bgn_de: str, end_de: str, page_no: int = 1, page_count: int = 100) -> dict:
    """지정 날짜 범위의 전체 상장사 공시를 조회한다 (corp_code 없이 전수조회).

    bgn_de/end_de: 'YYYYMMDD' 형식.
    반환값은 DART 원본 응답 dict(status/message/page_no/total_page/list 등).
    """
    params = {
        "crtfc_key": _get_api_key(),
        "bgn_de": bgn_de,
        "end_de": end_de,
        "page_no": page_no,
        "page_count": page_count,
    }
    resp = requests.get(f"{DART_BASE_URL}/list.json", params=params, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status")
    if status not in (STATUS_OK, STATUS_NO_DATA):
        raise DartAPIError(status, data.get("message", ""))
    return data


def get_financial_facts(corp_code: str, bsns_year: str, reprt_code: str, fs_div: str = "CFS") -> List[dict]:
    """단일회사 전체 재무제표를 조회한다. 데이터 없음(013)이면 빈 리스트를 반환한다."""
    params = {
        "crtfc_key": _get_api_key(),
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }
    resp = requests.get(f"{DART_BASE_URL}/fnlttSinglAcntAll.json", params=params, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status")
    if status == STATUS_NO_DATA:
        return []
    if status != STATUS_OK:
        raise DartAPIError(status, data.get("message", ""))
    return data.get("list", [])


def fetch_corp_code_map() -> Dict[str, dict]:
    """DART 정적 corpCode.xml(전종목 매핑, zip)을 내려받아 stock_code -> {corp_code, corp_name}로 반환.

    상장종목만 stock_code가 채워져 있고, 비상장/펀드 등은 공백이라 제외한다.
    호출 비용이 크므로(전종목 XML) 자주 호출하지 않는다 — crawlers_kr_disclosure.sync_kr_corp_codes()가
    매핑이 없는 Symbol이 있을 때만 호출한다.
    """
    params = {"crtfc_key": _get_api_key()}
    resp = requests.get(f"{DART_BASE_URL}/corpCode.xml", params=params, timeout=60)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "")
    if "json" in content_type:
        # 정상 응답이 아니라 에러(JSON)가 온 경우
        data = resp.json()
        raise DartAPIError(data.get("status", "UNKNOWN"), data.get("message", ""))

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_bytes = zf.read(zf.namelist()[0])

    root = ET.fromstring(xml_bytes)
    result: Dict[str, dict] = {}
    for el in root.findall("list"):
        stock_code = (el.findtext("stock_code") or "").strip()
        if not stock_code:
            continue
        result[stock_code] = {
            "corp_code": (el.findtext("corp_code") or "").strip(),
            "corp_name": (el.findtext("corp_name") or "").strip(),
        }
    logger.info("DART corpCode.xml 파싱 완료: 상장종목 %d건", len(result))
    return result
