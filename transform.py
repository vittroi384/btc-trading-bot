# =====================================================================
#  transform.py  ―  [가공 단계] 원본 시세 → 지표 계산해서 저장
# ---------------------------------------------------------------------
#  창고의 raw_candles(원본)을 읽어, 지표(이평선·RSI·볼린저·일목)를 계산해서
#  indicators 테이블에 저장한다. (실무로 치면 'staging → mart' 변환 단계)
#  지표 계산 로직은 strategy.py 의 함수를 그대로 재사용한다.
# =====================================================================

import db
from config import CONFIG
from strategy import compute_indicators


def transform(cfg):
    """
    창고의 원본 캔들을 읽어 지표를 계산하고 indicators 에 저장한다.
    반환: (저장한 행 수, 메시지)
    """
    market = cfg["TICKER"]
    timeframe = cfg["INTERVAL"]

    # 1) 원본 캔들 읽어오기
    raw = db.get_recent_candles(cfg["DB_FILE"], market, timeframe, limit=cfg["CANDLE_COUNT"])
    if raw is None or len(raw) == 0:
        return 0, "원본 캔들이 없음 (먼저 ingest 필요)"

    # 2) 지표 계산 (strategy.py 재사용)
    ind = compute_indicators(raw, cfg)

    # 3) 지표를 창고에 저장 (UPSERT)
    n = db.save_indicators(cfg["DB_FILE"], market, timeframe, ind)
    return n, f"{market} {timeframe} 지표 {n}건 계산·저장 완료"


# 단독 실행:  python transform.py
if __name__ == "__main__":
    db.init_db(CONFIG["DB_FILE"])
    n, msg = transform(CONFIG)
    print(msg)
