"""
대시보드 지수 카드용 주요 지수 요약 (TASK-0018 2단계, ADR-0005).

FinanceDataReader로 일봉 종가를 받아 현재값·전일 대비·최근 N거래일 추이를 만든다.
- KRX 직접 소스(KS11/KQ11)는 로그인 요구(`LOGOUT`)로 실패하므로 Yahoo를 1순위로 두고, 지수마다 대체 소스를 둔다.
- 외부 호출이라 느리고 실패할 수 있다: 네 지수를 병렬로 받고, 결과는 10분 캐시한다. 실패한 지수는 응답에서 뺀다.
- 무료 소스에 분 단위 데이터가 없어 추이(series)는 일봉 종가다.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from django.core.cache import cache

logger = logging.getLogger(__name__)

INDICES = [
    {'code': 'KOSPI', 'name': '코스피', 'sources': ['YAHOO:^KS11', 'NAVER:KOSPI']},
    {'code': 'KOSDAQ', 'name': '코스닥', 'sources': ['YAHOO:^KQ11', 'NAVER:KOSDAQ']},
    {'code': 'NASDAQ', 'name': '나스닥', 'sources': ['YAHOO:^IXIC', 'IXIC']},
    {'code': 'SP500', 'name': 'S&P 500', 'sources': ['YAHOO:^GSPC', 'US500']},
]

CACHE_KEY = 'market_indices:v1'
CACHE_TTL_SEC = 10 * 60
# 전부 실패했을 때는 짧게만 캐시해 외부 장애가 풀리면 곧 다시 시도한다
FAILURE_CACHE_TTL_SEC = 60
LOOKBACK_DAYS = 60
SERIES_POINTS = 30


def _default_reader(source, start):
    import FinanceDataReader as fdr

    return fdr.DataReader(source, start)


def summarize_closes(closes):
    """종가 목록(오래된 → 최근)으로 현재값·전일 대비·등락률·추이를 만든다. 2개 미만이면 None."""
    values = [float(v) for v in closes if v == v]  # NaN 제거
    if len(values) < 2:
        return None
    last, prev = values[-1], values[-2]
    change = last - prev
    return {
        'value': round(last, 2),
        'change': round(change, 2),
        'change_rate': round(change / prev * 100, 2) if prev else 0.0,
        'series': [round(v, 2) for v in values[-SERIES_POINTS:]],
    }


def fetch_index(spec, reader=None, today=None):
    reader = reader or _default_reader
    start = ((today or date.today()) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    for source in spec['sources']:
        try:
            df = reader(source, start)
            if df is None or df.empty or 'Close' not in df:
                continue
            summary = summarize_closes(df['Close'].tolist())
            if summary is None:
                continue
            return {
                'code': spec['code'],
                'name': spec['name'],
                'as_of': df.index[-1].date().isoformat(),
                'source': source,
                **summary,
            }
        except Exception:
            logger.warning('지수 조회 실패: %s (%s)', spec['code'], source, exc_info=True)
    return None


def get_market_indices(reader=None):
    # reader는 호출 시점에 모듈 전역에서 찾는다(기본값으로 고정하면 테스트에서 교체할 수 없다)
    reader = reader or _default_reader
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    with ThreadPoolExecutor(max_workers=len(INDICES)) as pool:
        results = list(pool.map(lambda spec: fetch_index(spec, reader), INDICES))
    indices = [r for r in results if r is not None]

    cache.set(CACHE_KEY, indices, CACHE_TTL_SEC if indices else FAILURE_CACHE_TTL_SEC)
    return indices
