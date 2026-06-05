# 실시간 암호화폐 데이터 파이프라인 + 자동매매 봇

업비트 시세를 **수집 → 저장 → 가공 → 품질검사 → 분석/시각화**하는 데이터 파이프라인과,
그 데이터를 활용해 규칙대로 매매하고 텔레그램으로 보고하는 자동매매 봇.

별도 인프라(DB 서버·Airflow 등) 없이 **Python + SQLite**만으로 단일 노드에서 동작하도록 구성했다.

> ⚠️ **꼭 읽기**
> - 투자 자문이 아니다. 기본 전략은 예시이며 수익을 보장하지 않는다.
> - 실거래 전 반드시 `DRY_RUN=true`(모의매매)로 충분히 검증할 것.
> - 이 프로젝트의 핵심은 *매매 수익*이 아니라 **데이터 파이프라인 설계·운영**이다.

---

## 아키텍처

```
   ┌─────────── 데이터 파이프라인 (pipeline.py) ──────────┐
   │                                                      │
업비트 API ──▶ [수집] ──▶ raw_candles ──▶ [가공] ──▶ indicators
 (ingest.py)              (원본 시세)    (transform.py)   (지표)
                                                  │
                                            [품질검사]
                                           (quality.py) ──▶ dq_checks
                                                  │
                          ┌───────────────────────┴───────────────────────┐
                          ▼                                                ▼
                   매매봇 (bot.py)                                 분석/시각화 (report.py)
                   - 지표로 매수/매도 판단                          - 요약 통계
                   - 텔레그램 알림                                  - 가격·지표·매매 차트(PNG)
                   - 매매기록 → trades 테이블
                          │
                   ┌──────┴──────┐
              데이터 창고 (SQLite, db.py): raw_candles / indicators / trades / dq_checks
```

- **수집(ingest)**: 거래소에서 캔들을 받아 원본 그대로 저장 (UPSERT → 중복 없음 = 멱등성)
- **가공(transform)**: 원본으로 지표(이평선·RSI·볼린저·일목) 계산해 저장
- **품질검사(quality)**: 빠진 봉·결측치·신선도 점검 → 문제 시 텔레그램 알림
- **오케스트레이션(pipeline)**: 위 단계를 순서대로(DAG) 주기 실행 + 실패 처리·로깅
- **서빙(report)**: 창고 데이터로 요약 + 차트 생성
- **소비자(bot)**: 데이터를 활용해 매매, 결과를 다시 창고에 적재

---

## 파일 구성

| 파일 | 역할 |
|------|------|
| **db.py** | 데이터 창고(SQLite) — 테이블 생성·적재·조회 |
| **ingest.py** | [수집] 업비트 → raw_candles |
| **transform.py** | [가공] raw_candles → indicators (지표 계산) |
| **quality.py** | [품질검사] 빠진 봉·결측치·신선도 점검 |
| **pipeline.py** | [오케스트레이터] 수집→가공→품질 순서 실행 (메인) |
| **report.py** | [분석/시각화] 요약 + 차트(report.png) |
| strategy.py | 지표 계산 + 매수/매도 규칙 (가공·봇이 공용) |
| trader.py | 주문 실행 / 모의매매 / 통계 / 매매기록 적재 |
| bot.py | 매매봇 (전략 신호로 매매 + 텔레그램) |
| notifier.py | 텔레그램 알림 + 명령 |
| backtest.py | 과거 데이터로 전략 검증 |
| config.py | `.env` 설정 로더 |
| .env.example | 설정 템플릿 (복사해서 `.env`) |
| .gitignore | 깃 업로드 시 제외 (비밀키·DB 등) |
| requirements.txt | 패키지 목록 |
| pipeline.service / trading-bot.service | 24시간 자동 실행(systemd) |

## 데이터 창고 스키마

| 테이블 | 내용 | 키 |
|--------|------|-----|
| `raw_candles` | 원본 시세(시가·고가·저가·종가·거래량) | (market, timeframe, candle_time) |
| `indicators` | 계산된 지표 | (market, timeframe, candle_time) |
| `trades` | 매매 기록(side·price·krw·pnl·mode) | id |
| `dq_checks` | 품질검사 결과 | id |

---

## 설치 (오라클 클라우드 VM, 우분투)

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git fonts-nanum
cd ~/btc-trading-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
> `fonts-nanum` 은 리포트 차트(`report.py`)에 한글이 깨지지 않게 표시하기 위한 한글 폰트다.
> (없어도 차트는 영어 라벨로 정상 생성된다.)

## 설정

```bash
cp .env.example .env
nano .env     # 텔레그램 토큰/chat id, (실거래 시) 업비트 키, 거래 설정 입력
```
- 텔레그램 봇: `@BotFather` 로 토큰, `@userinfobot` 으로 chat id. **봇한테 메시지 한 번 먼저 보낼 것.**
- 처음엔 `DRY_RUN=true` 유지.

---

## 실행 순서

**1) 데이터 파이프라인 (먼저 켠다 — 창고를 채워야 함)**
```bash
python pipeline.py     # 수집→가공→품질을 주기적으로 반복
```

**2) 단계별로 따로 돌려보고 싶을 때**
```bash
python ingest.py       # 수집만
python transform.py    # 가공만
python quality.py      # 품질검사만
```

**3) 리포트 (창고 데이터로 요약 + 차트)**
```bash
python report.py       # 콘솔에 요약 출력 + report.png 생성
```

**4) 매매봇 (모의매매부터)**
```bash
python backtest.py     # 전략 과거 성적 확인
python bot.py          # 매매 시작 (DRY_RUN=true 면 연습)
```

## 24시간 자동 실행 (systemd)

파이프라인과 봇을 각각 서비스로 등록 (경로/User 수정 후):
```bash
sudo cp pipeline.service trading-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pipeline trading-bot
sudo systemctl status pipeline trading-bot
journalctl -u pipeline -f      # 파이프라인 로그 실시간
```

## 텔레그램 명령 (봇)
- `/status` `/stats` — 매매 횟수·실현손익·수익률·승률·보유상태
- `/pause` `/resume` — 매매 일시정지/재개
- `/stop` — 봇 종료

---

## 이 프로젝트 ↔ 실무 데이터 엔지니어링 도구

가볍게 직접 구현했지만, 각 부분은 실무 도구와 1:1로 대응한다. (면접/포트폴리오 설명용)

| 이 프로젝트 | 실무 대응 | 개념 |
|---|---|---|
| SQLite (db.py) | PostgreSQL + TimescaleDB | 시계열 데이터 창고 |
| pipeline.py 루프 | Airflow / Dagster / Prefect | 오케스트레이션(DAG·스케줄·재시도) |
| transform.py | dbt (SQL 모델링) | staging → mart 변환 |
| quality.py | Great Expectations / dbt tests | 데이터 품질 검증 |
| UPSERT | merge / idempotent write | 멱등성 |
| report.py | Grafana / Metabase | BI 대시보드 |
| (선택) 웹소켓 수집 | Kafka / Redpanda | 스트리밍 |

**레벨업 아이디어**: Docker로 컨테이너화 → TimescaleDB로 교체 → Airflow DAG로 전환 →
dbt 모델 도입 → Grafana 대시보드 → 웹소켓 실시간 수집(스트리밍). 한 번에 다 하지 말고 하나씩.

## 포트폴리오 팁
- GitHub에 올릴 때 `.gitignore` 덕분에 `.env`(비밀키)·`*.db`는 제외된다. **`.env`는 절대 커밋 금지.**
- 위 아키텍처 그림, 스키마 표, `report.png`(실제 돌린 차트), 텔레그램 알림 스샷을 README에 넣으면 강하다.
- 어필 포인트: "트레이딩 전략"보다 **"데이터 수집·적재·변환·품질·서빙 파이프라인을 직접 설계·운영"**.

---

## 참고
- 봇/파이프라인 상태는 `state.json`·`market.db`에 저장돼 재시작해도 이어진다.
- 한국은 가상자산 소득 과세 도입 예정이라 거래 기록 보관 권장.
- 전략 수정: `strategy.py`의 `generate_signal()` 만 고치면 된다. 수정 후 `backtest.py`로 확인.

## 라이선스
MIT License (`LICENSE` 파일 참고). 사용 전 `LICENSE` 안의 `<YOUR NAME>` 을 본인 이름으로 바꿀 것.

## CI
`.github/workflows/ci.yml` — 푸시할 때마다 코드 컴파일·임포트를 자동 검사한다. (실거래는 돌리지 않음)
