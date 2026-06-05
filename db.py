# =====================================================================
#  db.py  ―  '데이터 창고'(SQLite) 계층
# ---------------------------------------------------------------------
#  이 파일이 데이터 엔지니어링(DE)의 핵심이다. 받은 시세를 그냥 쓰고 버리지 않고,
#  파일 하나(market.db)에 차곡차곡 '저장'해 두는 역할을 한다.
#
#  왜 SQLite? → 별도 DB 서버 설치 없이 파이썬에 기본 내장돼서, 무료 VM에서 바로 돌아감.
#  (실무에선 보통 PostgreSQL+TimescaleDB 같은 걸 쓰는데, 구조는 똑같고 여기선 가볍게 SQLite로.)
#
#  테이블(=엑셀 시트 같은 표) 4개를 둔다:
#    raw_candles : 거래소에서 받은 '원본 시세'(가공 안 한 날것)
#    indicators  : 원본으로 계산한 '지표'(이평선·RSI·볼린저·일목)
#    trades      : 봇이 실제로 사고판 '매매 기록'
#    dq_checks   : '데이터 품질 검사' 결과 (구멍/결측/중복 등)
# =====================================================================

import sqlite3
from contextlib import contextmanager

import pandas as pd


@contextmanager
def get_conn(db_file):
    """
    DB 파일에 연결하는 통로를 열어주고, 다 쓰면 자동으로 닫아준다.
    WAL 모드로 켜서, 한쪽(파이프라인)이 쓰는 동안 다른 쪽(봇/리포트)이 읽어도 안 막히게 한다.
    'with get_conn(...) as conn:' 형태로 사용.
    """
    conn = sqlite3.connect(db_file, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")   # 동시 읽기/쓰기에 강한 모드
    try:
        yield conn
        conn.commit()                          # 작업이 끝나면 변경사항 저장(커밋)
    finally:
        conn.close()


def init_db(db_file):
    """
    테이블이 없으면 만든다. (이미 있으면 그냥 넘어감 → 몇 번 실행해도 안전)
    프로그램 시작할 때 한 번 불러주면 된다.
    """
    with get_conn(db_file) as conn:
        c = conn.cursor()
        # 1) 원본 시세 (market+timeframe+candle_time 조합이 '고유 키' → 같은 봉은 한 줄만)
        c.execute("""
            CREATE TABLE IF NOT EXISTS raw_candles (
                market      TEXT NOT NULL,
                timeframe   TEXT NOT NULL,
                candle_time TEXT NOT NULL,
                open        REAL,
                high        REAL,
                low         REAL,
                close       REAL,
                volume      REAL,
                ingested_at TEXT,
                PRIMARY KEY (market, timeframe, candle_time)
            )
        """)
        # 2) 지표
        c.execute("""
            CREATE TABLE IF NOT EXISTS indicators (
                market      TEXT NOT NULL,
                timeframe   TEXT NOT NULL,
                candle_time TEXT NOT NULL,
                close       REAL,
                ma_short    REAL,
                ma_long     REAL,
                rsi         REAL,
                bb_mid      REAL,
                bb_upper    REAL,
                bb_lower    REAL,
                ichi_tenkan REAL,
                ichi_kijun  REAL,
                ichi_span_a REAL,
                ichi_span_b REAL,
                computed_at TEXT,
                PRIMARY KEY (market, timeframe, candle_time)
            )
        """)
        # 3) 매매 기록
        c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                ts     TEXT,
                market TEXT,
                side   TEXT,
                price  REAL,
                krw    REAL,
                amount REAL,
                pnl    REAL,
                mode   TEXT
            )
        """)
        # 4) 데이터 품질 검사 결과
        c.execute("""
            CREATE TABLE IF NOT EXISTS dq_checks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                checked_at TEXT,
                market     TEXT,
                timeframe  TEXT,
                check_name TEXT,
                status     TEXT,
                value      INTEGER,
                detail     TEXT
            )
        """)


# ----------------------- 적재(저장) -----------------------

def upsert_candles(db_file, market, timeframe, df):
    """
    캔들(가격 표 df)을 raw_candles 에 저장한다.
    'UPSERT' = 같은 봉이 이미 있으면 덮어쓰고(update), 없으면 새로 넣음(insert).
    → 파이프라인을 몇 번 돌려도 중복이 안 쌓인다(멱등성). DE에서 중요한 개념.
    반환: 처리한 행 수.
    """
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for ts, r in df.iterrows():
        rows.append((
            market, timeframe, str(ts),
            float(r["open"]), float(r["high"]), float(r["low"]),
            float(r["close"]), float(r["volume"]), now,
        ))
    with get_conn(db_file) as conn:
        conn.executemany("""
            INSERT INTO raw_candles
                (market, timeframe, candle_time, open, high, low, close, volume, ingested_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(market, timeframe, candle_time) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume, ingested_at=excluded.ingested_at
        """, rows)
    return len(rows)


def save_indicators(db_file, market, timeframe, df):
    """지표가 계산된 표(df)를 indicators 에 저장(UPSERT). 반환: 행 수."""
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    cols = ["close", "ma_short", "ma_long", "rsi", "bb_mid", "bb_upper", "bb_lower",
            "ichi_tenkan", "ichi_kijun", "ichi_span_a", "ichi_span_b"]
    rows = []
    for ts, r in df.iterrows():
        vals = []
        for col in cols:
            v = r.get(col)
            vals.append(None if pd.isna(v) else float(v))
        rows.append((market, timeframe, str(ts), *vals, now))
    with get_conn(db_file) as conn:
        conn.executemany("""
            INSERT INTO indicators
                (market, timeframe, candle_time, close, ma_short, ma_long, rsi,
                 bb_mid, bb_upper, bb_lower, ichi_tenkan, ichi_kijun, ichi_span_a, ichi_span_b, computed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(market, timeframe, candle_time) DO UPDATE SET
                close=excluded.close, ma_short=excluded.ma_short, ma_long=excluded.ma_long,
                rsi=excluded.rsi, bb_mid=excluded.bb_mid, bb_upper=excluded.bb_upper,
                bb_lower=excluded.bb_lower, ichi_tenkan=excluded.ichi_tenkan,
                ichi_kijun=excluded.ichi_kijun, ichi_span_a=excluded.ichi_span_a,
                ichi_span_b=excluded.ichi_span_b, computed_at=excluded.computed_at
        """, rows)
    return len(rows)


def save_trade(db_file, market, side, price, krw, amount, pnl, mode, ts=None):
    """
    매매 한 건을 trades 에 기록한다. (봇이 사고팔 때마다 호출)
    ts 를 주면 그 시각으로, 안 주면 '지금' 시각으로 기록한다.
    (실거래는 지금=캔들시각이라 생략 OK. 백테스트/시뮬은 캔들시각을 넣으면 차트에 제 위치로 찍힘)
    """
    from datetime import datetime
    if ts is None:
        ts = datetime.now().isoformat(timespec="seconds")
    with get_conn(db_file) as conn:
        conn.execute("""
            INSERT INTO trades (ts, market, side, price, krw, amount, pnl, mode)
            VALUES (?,?,?,?,?,?,?,?)
        """, (str(ts), market, side, price, krw, amount, pnl, mode))


def save_dq(db_file, market, timeframe, check_name, status, value, detail):
    """데이터 품질 검사 결과 한 줄을 dq_checks 에 기록한다."""
    from datetime import datetime
    with get_conn(db_file) as conn:
        conn.execute("""
            INSERT INTO dq_checks (checked_at, market, timeframe, check_name, status, value, detail)
            VALUES (?,?,?,?,?,?,?)
        """, (datetime.now().isoformat(timespec="seconds"),
              market, timeframe, check_name, status, value, detail))


# ----------------------- 조회(읽기) -----------------------

def get_recent_candles(db_file, market, timeframe, limit=200):
    """최근 캔들 limit개를 시간 오름차순 표(df)로 돌려준다."""
    with get_conn(db_file) as conn:
        df = pd.read_sql_query("""
            SELECT candle_time, open, high, low, close, volume
            FROM raw_candles
            WHERE market=? AND timeframe=?
            ORDER BY candle_time DESC
            LIMIT ?
        """, conn, params=(market, timeframe, limit))
    if len(df):
        df = df.iloc[::-1].reset_index(drop=True)          # 다시 오래된 → 최신 순으로
        df["candle_time"] = pd.to_datetime(df["candle_time"])
        df = df.set_index("candle_time")
    return df


def get_recent_indicators(db_file, market, timeframe, limit=200):
    """최근 지표 limit개를 시간 오름차순 표(df)로 돌려준다. (봇이 신호 판단할 때 사용)"""
    with get_conn(db_file) as conn:
        df = pd.read_sql_query("""
            SELECT * FROM indicators
            WHERE market=? AND timeframe=?
            ORDER BY candle_time DESC
            LIMIT ?
        """, conn, params=(market, timeframe, limit))
    if len(df):
        df = df.iloc[::-1].reset_index(drop=True)
        df["candle_time"] = pd.to_datetime(df["candle_time"])
        df = df.set_index("candle_time")
    return df


def get_trades(db_file, limit=1000):
    """매매 기록을 표(df)로 돌려준다."""
    with get_conn(db_file) as conn:
        return pd.read_sql_query(
            "SELECT * FROM trades ORDER BY ts DESC LIMIT ?", conn, params=(limit,))


def get_stats(db_file, market, timeframe):
    """창고 현황 요약: 캔들 수, 가장 최근 캔들 시각 등."""
    with get_conn(db_file) as conn:
        cur = conn.execute(
            "SELECT COUNT(*), MIN(candle_time), MAX(candle_time) FROM raw_candles WHERE market=? AND timeframe=?",
            (market, timeframe))
        n, first, last = cur.fetchone()
    return {"candle_count": n or 0, "first": first, "last": last}
