"""
대시보드 지수 카드용 주요 지수 요약 (TASK-0018 2단계, ADR-0005).

- 요청 경로에서는 외부 호출을 하지 않는다. 스케줄러 job(refresh_market_indices)이 주기적으로 갱신하고,
  API는 캐시만 읽는다. 서버는 gunicorn sync 워커 1개라, 요청 중 외부 소스가 멈추면 모든 API가 멈추기 때문이다.
- 외부 호출은 Yahoo chart API를 requests로 직접 하고 timeout을 건다. FinanceDataReader의 Yahoo/Naver reader는
  timeout이 없어 쓰지 않는다.
- 갱신에 실패한 지수는 직전 정상값을 유지하고 stale=True로 표시한다. 처음부터 실패한 지수는 응답에서 빠진다.
- 무료 소스에 분 단위 데이터가 없어 추이(series)는 최근 거래일 일봉 종가다.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone as dt_timezone

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

INDICES = [
    {'code': 'KOSPI', 'name': '코스피', 'symbol': '^KS11'},
    {'code': 'KOSDAQ', 'name': '코스닥', 'symbol': '^KQ11'},
    {'code': 'NASDAQ', 'name': '나스닥', 'symbol': '^IXIC'},
    {'code': 'SP500', 'name': 'S&P 500', 'symbol': '^GSPC'},
]

YAHOO_CHART_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
REQUEST_TIMEOUT = (3, 5)  # (연결, 응답) 초
# Yahoo는 브라우저가 아닌 User-Agent에 429를 주는 경우가 있어 브라우저 형식을 쓴다(로컬 실측과 같은 형식)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/124.0 Safari/537.36'
}

# 스케줄러가 덮어쓰므로 만료 없이 둔다(프로세스 재시작 시 비고, 다음 job 실행에서 채워진다)
CACHE_KEY = 'market_indices:v2'
SERIES_POINTS = 30


def fetch_yahoo_daily_closes(symbol):
    """Yahoo chart API에서 최근 3개월 일봉 종가를 [(거래일, 종가), ...] (오래된 → 최근)로 받는다. 종가 없는 행은 뺀다."""
    response = requests.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={'range': '3mo', 'interval': '1d'},
        headers=HEADERS,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()['chart']['result'][0]
    offset = result.get('meta', {}).get('gmtoffset') or 0
    closes = result['indicators']['quote'][0]['close']
    return [
        (datetime.fromtimestamp(ts + offset, tz=dt_timezone.utc).date(), float(close))
        for ts, close in zip(result.get('timestamp') or [], closes)
        if close is not None
    ]


def summarize(spec, pairs):
    """(거래일, 종가) 목록으로 카드 데이터를 만든다. 2개 미만이면 None. as_of는 값과 같은 행의 거래일이다."""
    pairs = [(d, c) for d, c in pairs if c == c]  # NaN 제거
    if len(pairs) < 2:
        return None
    (as_of, last), (_, prev) = pairs[-1], pairs[-2]
    change = last - prev
    return {
        'code': spec['code'],
        'name': spec['name'],
        'value': round(last, 2),
        'change': round(change, 2),
        'change_rate': round(change / prev * 100, 2) if prev else 0.0,
        'as_of': as_of.isoformat(),
        'series': [round(c, 2) for _, c in pairs[-SERIES_POINTS:]],
        'stale': False,
    }


def _fetch_one(spec, fetch):
    try:
        return summarize(spec, fetch(spec['symbol']))
    except Exception:
        logger.warning('지수 조회 실패: %s (%s)', spec['code'], spec['symbol'], exc_info=True)
        return None


def refresh_market_indices(fetch=None):
    """
    스케줄러에서 호출한다. 네 지수를 병렬로 받아 캐시를 갱신하고, 실패한 지수는 직전 값을 stale로 유지한다.
    각 요청에 timeout이 있어 한 번 실행은 대략 연결+응답 timeout 안에 끝난다.
    """
    fetch = fetch or fetch_yahoo_daily_closes
    previous = {item['code']: item for item in (cache.get(CACHE_KEY) or [])}

    with ThreadPoolExecutor(max_workers=len(INDICES)) as pool:
        fresh = list(pool.map(lambda spec: _fetch_one(spec, fetch), INDICES))

    indices = []
    for spec, item in zip(INDICES, fresh):
        if item is not None:
            indices.append(item)
        elif spec['code'] in previous:
            indices.append({**previous[spec['code']], 'stale': True})

    cache.set(CACHE_KEY, indices, timeout=None)
    return indices


def get_market_indices():
    """요청 경로용: 캐시만 읽는다. 아직 갱신 전이면 빈 목록(프론트는 지수 줄을 숨긴다)."""
    return cache.get(CACHE_KEY) or []
