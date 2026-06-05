# =====================================================================
#  quality.py  ―  [품질검사 단계] 창고 데이터가 멀쩡한지 점검
# ---------------------------------------------------------------------
#  데이터 엔지니어링에서 아주 중요한 부분. 쌓인 데이터에 구멍(빠진 봉)이나
#  이상(결측치·중복)이 없는지 자동으로 검사하고, 결과를 dq_checks 에 기록한다.
#
#  검사 항목:
#   1) missing_candles : 시간 간격에 빠진 봉(구멍)이 있는지
#   2) null_values     : open/high/low/close 에 빈 값이 있는지
#   3) freshness       : 가장 최근 봉이 얼마나 오래됐는지(데이터가 최신인지)
# =====================================================================

from datetime import datetime, timezone

import pandas as pd

import db
from config import CONFIG

# 캔들 단위 → 초 단위 길이 (구멍 검사에 사용)
TIMEFRAME_SECONDS = {
    "minute1": 60, "minute3": 180, "minute5": 300, "minute10": 600,
    "minute15": 900, "minute30": 1800, "minute60": 3600, "minute240": 14400,
    "day": 86400, "week": 604800,
}


def run_quality_checks(cfg):
    """
    창고 데이터의 품질을 검사하고 결과를 dq_checks 에 기록한다.
    반환: 검사 결과 리스트 [(검사명, 상태, 값, 설명), ...]
    """
    market = cfg["TICKER"]
    timeframe = cfg["INTERVAL"]
    db_file = cfg["DB_FILE"]

    raw = db.get_recent_candles(db_file, market, timeframe, limit=cfg["CANDLE_COUNT"])
    results = []

    if raw is None or len(raw) == 0:
        results.append(("data_exists", "FAIL", 0, "원본 캔들이 하나도 없음"))
        for name, status, val, detail in results:
            db.save_dq(db_file, market, timeframe, name, status, val, detail)
        return results

    # 1) 빠진 봉(구멍) 검사: 봉 시각 간격이 일정한지
    step = TIMEFRAME_SECONDS.get(timeframe, 3600)
    times = raw.index.to_series()
    gaps = times.diff().dt.total_seconds().dropna()
    # 정상 간격보다 1.5배 넘게 벌어진 구간 = 봉이 빠진 것으로 판단
    missing = int((gaps > step * 1.5).sum())
    results.append((
        "missing_candles",
        "PASS" if missing == 0 else "WARN",
        missing,
        "빠진 봉 없음" if missing == 0 else f"중간에 빠진 봉 {missing}곳 발견",
    ))

    # 2) 결측치 검사: 가격에 빈 값이 있는지
    null_count = int(raw[["open", "high", "low", "close"]].isna().sum().sum())
    results.append((
        "null_values",
        "PASS" if null_count == 0 else "FAIL",
        null_count,
        "결측치 없음" if null_count == 0 else f"빈 값 {null_count}개 발견",
    ))

    # 3) 신선도 검사: 마지막 봉이 너무 오래되지 않았는지 (정상 간격의 3배 이내면 OK)
    last_time = raw.index[-1].to_pydatetime()
    if last_time.tzinfo is None:
        age_sec = (datetime.now() - last_time).total_seconds()
    else:
        age_sec = (datetime.now(timezone.utc) - last_time).total_seconds()
    fresh_ok = age_sec <= step * 3
    results.append((
        "freshness",
        "PASS" if fresh_ok else "WARN",
        int(age_sec),
        f"마지막 봉 {int(age_sec//60)}분 전" + ("" if fresh_ok else " (오래됨)"),
    ))

    # 결과를 창고에 기록
    for name, status, val, detail in results:
        db.save_dq(db_file, market, timeframe, name, status, val, detail)
    return results


# 단독 실행:  python quality.py
if __name__ == "__main__":
    db.init_db(CONFIG["DB_FILE"])
    for name, status, val, detail in run_quality_checks(CONFIG):
        mark = "✅" if status == "PASS" else ("⚠️" if status == "WARN" else "❌")
        print(f"{mark} [{name}] {status} - {detail}")
