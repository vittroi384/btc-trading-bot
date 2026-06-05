# =====================================================================
#  pipeline.py  ―  [오케스트레이터] 파이프라인 전체를 순서대로 돌리는 '지휘자'
# ---------------------------------------------------------------------
#  실행:  python pipeline.py
#  하는 일(주기적으로 반복):
#     수집(ingest) → 가공(transform) → 품질검사(quality)
#  이 세 단계를 정해진 순서(=DE 용어로 'DAG')로 돌리고, 각 단계 실패해도
#  멈추지 않게 잡아서 로그를 남긴다. 텔레그램이 설정돼 있으면 문제 생길 때 알림도 보낸다.
#
#  ※ 실무에선 이 역할을 Airflow / Dagster 같은 전용 도구가 한다.
#     여기선 가볍게 직접 구현(원리는 동일: 순서·스케줄·재시도·로그).
# =====================================================================

import time
from datetime import datetime

import db
from config import CONFIG
from ingest import ingest_candles
from transform import transform
from quality import run_quality_checks
from notifier import TelegramNotifier


def log(msg):
    """화면에 시각과 함께 로그를 찍는다."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def run_once(cfg, notifier):
    """파이프라인 한 사이클: 수집 → 가공 → 품질검사. 각 단계는 실패해도 다음으로 넘어감."""
    # 1) 수집
    try:
        n, m = ingest_candles(cfg)
        log("수집: " + m)
    except Exception as e:
        log(f"수집 실패: {e}")
        notifier.send(f"⚠️ [파이프라인] 수집 단계 오류: {e}")
        return  # 수집 실패하면 뒤 단계는 의미 없으니 이번 사이클 종료

    # 2) 가공
    try:
        n, m = transform(cfg)
        log("가공: " + m)
    except Exception as e:
        log(f"가공 실패: {e}")
        notifier.send(f"⚠️ [파이프라인] 가공 단계 오류: {e}")
        return

    # 3) 품질검사
    try:
        results = run_quality_checks(cfg)
        bad = [r for r in results if r[1] != "PASS"]
        summary = " / ".join(f"{name}:{status}" for name, status, _, _ in results)
        log("품질검사: " + summary)
        # 문제(FAIL/WARN)가 있으면 텔레그램으로 알림
        if bad:
            detail = "\n".join(f"- {name}: {detail}" for name, status, _, detail in bad)
            notifier.send("🧪 [파이프라인] 데이터 품질 경고\n" + detail)
    except Exception as e:
        log(f"품질검사 실패: {e}")
        notifier.send(f"⚠️ [파이프라인] 품질검사 단계 오류: {e}")


def main():
    cfg = CONFIG
    db.init_db(cfg["DB_FILE"])   # 창고(테이블) 준비
    notifier = TelegramNotifier(cfg["TELEGRAM_BOT_TOKEN"], cfg["TELEGRAM_CHAT_ID"])
    log(f"파이프라인 시작 — {cfg['TICKER']} {cfg['INTERVAL']} / 창고: {cfg['DB_FILE']}")
    notifier.send(f"🛠 데이터 파이프라인 시작 ({cfg['TICKER']} {cfg['INTERVAL']})")

    # 정해진 주기로 계속 반복
    while True:
        run_once(cfg, notifier)
        time.sleep(cfg["PIPELINE_LOOP_SECONDS"])


if __name__ == "__main__":
    main()
