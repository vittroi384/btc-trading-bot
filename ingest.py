# =====================================================================
#  ingest.py  ―  [수집 단계] 거래소에서 시세를 받아 '창고'에 넣기
# ---------------------------------------------------------------------
#  파이프라인의 첫 단계. 업비트에서 캔들(봉)을 받아 raw_candles 테이블에 저장한다.
#  같은 봉은 덮어쓰기(UPSERT)라서 몇 번 돌려도 중복이 안 쌓인다.
# =====================================================================

import pyupbit

import db


def ingest_candles(cfg):
    """
    업비트에서 최근 캔들을 받아 창고(raw_candles)에 적재한다.
    반환: (저장한 행 수, 메시지)
    """
    market = cfg["TICKER"]
    timeframe = cfg["INTERVAL"]
    count = cfg["CANDLE_COUNT"]

    # 업비트에서 캔들 가져오기 (open/high/low/close/volume 표)
    candles = pyupbit.get_ohlcv(market, interval=timeframe, count=count)
    if candles is None or len(candles) == 0:
        return 0, "캔들을 받지 못함(인터넷/티커 확인)"

    # 창고에 저장 (UPSERT)
    n = db.upsert_candles(cfg["DB_FILE"], market, timeframe, candles)
    return n, f"{market} {timeframe} 캔들 {n}건 적재 완료"


# 단독 실행해서 한 번만 수집해 보고 싶을 때:  python ingest.py
if __name__ == "__main__":
    from config import CONFIG
    db.init_db(CONFIG["DB_FILE"])
    n, msg = ingest_candles(CONFIG)
    print(msg)
