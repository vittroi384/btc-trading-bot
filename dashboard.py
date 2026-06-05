# =====================================================================
#  dashboard.py  ―  브라우저로 보는 웹 대시보드 (현황/차트 보기 + 일시정지·재개)
# ---------------------------------------------------------------------
#  실행:  python dashboard.py   (보통은 systemd 서비스로 24시간 띄움)
#  접속:  브라우저에서  http://<서버IP>/  → 아이디/비번 입력 후 현황 확인.
#
#  - 보여주는 것: 현재가 / 실현손익·수익률 / 승률 / 매매횟수 / 보유상태 /
#                가격·지표·매매 차트(report.png) / 최근 매매 기록
#  - 조작: ⏸ 일시정지 / ▶️ 재개  (control.json 으로 봇과 신호를 주고받음)
#  - 보안: 기본 인증(아이디/비번)으로 잠가둠. .env 의 DASH_USER / DASH_PASS 사용.
#          (HTTP라 통신 자체는 암호화 안 되니, 비번을 반드시 설정해야 시작됩니다.)
# =====================================================================

import json
import os
import threading
import time
from functools import wraps

from flask import Flask, request, Response, redirect, send_file
from waitress import serve
from dotenv import load_dotenv

load_dotenv()                  # .env 읽기 (DASH_USER / DASH_PASS / DASH_PORT 등)
from config import CONFIG      # 거래 설정(TICKER, STATE_FILE, DB_FILE ...)

CONTROL_FILE = "control.json"  # bot.py 와 공유하는 일시정지/재개 신호 파일
REPORT_PNG = "report.png"      # 차트 이미지 파일

DASH_USER = os.getenv("DASH_USER", "")
DASH_PASS = os.getenv("DASH_PASS", "")
DASH_PORT = int(os.getenv("DASH_PORT", "80"))

app = Flask(__name__)
_chart_lock = threading.Lock()  # 차트 생성을 한 번에 하나만 (matplotlib 동시 사용 방지)

# ---------- 색상 (report.py 차트와 통일된 다크 테마) ----------
COL = {
    "bg": "#0f1320", "panel": "#141a2b", "card": "#192032", "edge": "#283250",
    "price": "#3ec6ff", "green": "#22d39a", "red": "#ff5470",
    "text": "#e7ebf5", "muted": "#8b93a7",
}


# ---------- 비밀번호(HTTP 기본 인증) ----------
def _check(u, p):
    """아이디/비번이 .env 에 설정된 값과 정확히 일치하는지 확인."""
    return bool(DASH_USER) and bool(DASH_PASS) and u == DASH_USER and p == DASH_PASS


def require_auth(fn):
    """이 표시가 붙은 화면은 로그인(기본 인증)을 통과해야 볼 수 있게 합니다."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        a = request.authorization
        if not a or not _check(a.username, a.password):
            return Response("로그인이 필요합니다.", 401,
                            {"WWW-Authenticate": 'Basic realm="btc-trading-bot"'})
        return fn(*args, **kwargs)
    return wrapper


# ---------- 데이터 읽기 ----------
def read_state():
    """봇이 저장한 state.json(보유상태·손익·매매기록)을 읽어옵니다. (없으면 빈 값)"""
    try:
        with open(CONFIG["STATE_FILE"], "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return {}


def read_paused():
    """control.json 을 읽어 '일시정지 중인지' 돌려줍니다. (없으면 False)"""
    try:
        with open(CONTROL_FILE, "r", encoding="utf-8") as fp:
            return bool(json.load(fp).get("paused", False))
    except Exception:
        return False


def write_paused(value):
    """일시정지/재개 상태를 control.json 에 기록합니다. (봇이 매 루프 읽어서 따름)"""
    with open(CONTROL_FILE, "w", encoding="utf-8") as fp:
        json.dump({"paused": bool(value)}, fp)


def current_price():
    """업비트에서 현재가를 가져옵니다. (공개 시세라 키 불필요)"""
    try:
        import pyupbit
        return pyupbit.get_current_price(CONFIG["TICKER"])
    except Exception:
        return None


def compute_stats(st, price):
    """state 로부터 누적 수익률·승률·평가손익(%)을 계산합니다. (trader.stats 와 동일 공식)"""
    trades = st.get("trades", [])
    invested = sum(t.get("krw", 0) for t in trades if t.get("side") == "buy")
    roi = (st.get("realized_pnl", 0.0) / invested * 100) if invested > 0 else 0.0
    sells = st.get("sell_count", 0)
    win_rate = (st.get("win_count", 0) / sells * 100) if sells > 0 else 0.0
    unreal = None
    if st.get("holding") and price and st.get("entry_price", 0) > 0:
        unreal = (price - st["entry_price"]) / st["entry_price"] * 100
    return roi, win_rate, unreal


def ensure_chart():
    """차트(report.png)가 없거나 60초보다 오래됐으면 새로 만든다. (한 번에 하나만)"""
    try:
        if os.path.exists(REPORT_PNG) and (time.time() - os.path.getmtime(REPORT_PNG) < 60):
            return
        with _chart_lock:
            # 자물쇠를 잡는 사이 다른 요청이 이미 만들었을 수 있으니 다시 확인
            if os.path.exists(REPORT_PNG) and (time.time() - os.path.getmtime(REPORT_PNG) < 60):
                return
            import db
            from report import make_chart
            db.init_db(CONFIG["DB_FILE"])
            make_chart(CONFIG, REPORT_PNG)
    except Exception as e:
        print("차트 생성 실패:", e)


# ---------- 화면(HTML) 만들기 ----------
def _won(x):
    """숫자를 '1,234,000' 처럼 콤마 찍어 돌려줍니다."""
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return "-"


CSS = """
<style>
  * { box-sizing: border-box; }
  body { margin:0; background:#0f1320; color:#e7ebf5;
         font-family: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace; }
  .wrap { max-width:960px; margin:0 auto; padding:22px 16px 60px; }
  .top { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:18px; }
  .top h1 { font-size:18px; letter-spacing:1.5px; margin:0; }
  .badge { font-size:12px; padding:3px 10px; border-radius:999px; border:1px solid #283250; }
  .b-live { color:#ff5470; border-color:#ff5470; }
  .b-dry  { color:#8b93a7; }
  .b-run  { color:#22d39a; border-color:#22d39a; }
  .b-pause{ color:#ffb454; border-color:#ffb454; }
  .upd { margin-left:auto; color:#8b93a7; font-size:12px; }
  .grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:14px; }
  .card { background:#192032; border:1px solid #283250; border-radius:12px; padding:14px 16px; }
  .card .k { color:#8b93a7; font-size:12px; margin-bottom:8px; }
  .card .v { font-size:20px; font-weight:700; }
  .hold { background:#141a2b; border:1px solid #283250; border-radius:12px;
          padding:14px 16px; margin-bottom:14px; }
  .ctrl { display:flex; gap:10px; margin-bottom:18px; }
  .ctrl form { flex:1; margin:0; }
  .ctrl button { width:100%; padding:13px; border-radius:12px; border:1px solid #283250;
                 background:#192032; color:#e7ebf5; font-size:15px; font-family:inherit; cursor:pointer; }
  .ctrl button.on-pause { border-color:#ffb454; color:#ffb454; }
  .ctrl button.on-resume{ border-color:#22d39a; color:#22d39a; }
  .ctrl button:disabled { opacity:0.35; cursor:default; }
  img.chart { width:100%; border-radius:12px; border:1px solid #283250; display:block; margin-bottom:18px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:right; padding:8px 10px; border-bottom:1px solid #222a40; }
  th:first-child, td:first-child { text-align:left; }
  th { color:#8b93a7; font-weight:600; }
  .green { color:#22d39a; } .red { color:#ff5470; } .muted { color:#8b93a7; }
  .empty { color:#8b93a7; padding:18px 4px; }
  @media (max-width:640px){ .grid{ grid-template-columns:repeat(2,1fr); } }
</style>
"""


@app.route("/")
@require_auth
def index():
    st = read_state()
    price = current_price()
    paused = read_paused()
    roi, win_rate, unreal = compute_stats(st, price)
    live = not CONFIG["DRY_RUN"]

    mode_badge = ('<span class="badge b-live">실거래</span>' if live
                  else '<span class="badge b-dry">모의(DRY_RUN)</span>')
    state_badge = ('<span class="badge b-pause">⏸ 일시정지</span>' if paused
                   else '<span class="badge b-run">▶️ 매매중</span>')

    pnl = st.get("realized_pnl", 0.0)
    pnl_cls = "green" if pnl >= 0 else "red"
    roi_cls = "green" if roi >= 0 else "red"

    if st.get("holding") and st.get("entry_price", 0) > 0:
        if unreal is not None:
            u_txt = f'<span class="{"green" if unreal >= 0 else "red"}">{unreal:+.2f}%</span>'
        else:
            u_txt = '<span class="muted">-</span>'
        hold_html = (f'보유중 · 평균매수 <b>{_won(st["entry_price"])}원</b> · '
                     f'수량 {st.get("coin_amount", 0):.8f} · 평가손익 {u_txt}')
    else:
        hold_html = '<span class="muted">보유 없음 (현금)</span>'

    rows = ""
    for t in reversed(st.get("trades", [])[-15:]):
        side = t.get("side")
        side_html = ('<span class="green">매수</span>' if side == "buy"
                     else '<span class="red">매도</span>')
        when = str(t.get("time", "")).replace("T", " ")
        if side == "sell" and t.get("pnl") is not None:
            pl_html = f'<span class="{"green" if t["pnl"] >= 0 else "red"}">{t["pnl"]:+,.0f}</span>'
        else:
            pl_html = '<span class="muted">-</span>'
        rows += (f"<tr><td>{when}</td><td>{side_html}</td><td>{_won(t.get('price'))}</td>"
                 f"<td>{_won(t.get('krw'))}</td><td>{pl_html}</td></tr>")
    if rows:
        trades_block = ("<table><tr><th>시각</th><th>구분</th><th>가격</th>"
                        "<th>금액(원)</th><th>손익(원)</th></tr>" + rows + "</table>")
    else:
        trades_block = '<div class="empty">아직 매매 기록이 없어요. 신호가 뜨면 여기에 쌓입니다.</div>'

    price_txt = (_won(price) + "원") if price else "-"

    body = f"""
    <div class="wrap">
      <div class="top">
        <h1>BTC TRADING BOT</h1>
        {mode_badge}{state_badge}
        <span class="upd">{CONFIG['TICKER']} · {CONFIG['INTERVAL']} · 갱신 {time.strftime('%H:%M:%S')}</span>
      </div>

      <div class="grid">
        <div class="card"><div class="k">현재가</div><div class="v" style="color:{COL['price']}">{price_txt}</div></div>
        <div class="card"><div class="k">실현손익</div><div class="v {pnl_cls}">{pnl:+,.0f}원</div></div>
        <div class="card"><div class="k">누적 수익률</div><div class="v {roi_cls}">{roi:+.2f}%</div></div>
        <div class="card"><div class="k">매매 / 승률</div><div class="v">{st.get('trade_count', 0)}회 · {win_rate:.0f}%</div></div>
      </div>

      <div class="hold">{hold_html}</div>

      <div class="ctrl">
        <form method="post" action="/pause"><button class="on-pause" {'disabled' if paused else ''}>⏸ 일시정지</button></form>
        <form method="post" action="/resume"><button class="on-resume" {'disabled' if not paused else ''}>▶️ 재개</button></form>
      </div>

      <img class="chart" src="/chart.png?t={int(time.time())}" alt="차트 로딩 중...">

      {trades_block}
    </div>
    """

    html = ("<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<meta http-equiv='refresh' content='20'>"
            "<title>BTC Bot</title>" + CSS + "</head><body>" + body + "</body></html>")
    return html


@app.route("/chart.png")
@require_auth
def chart_png():
    ensure_chart()
    if os.path.exists(REPORT_PNG):
        return send_file(REPORT_PNG, mimetype="image/png")
    return Response("차트 준비 중 (데이터가 더 쌓이면 표시됩니다)", 503)


@app.route("/pause", methods=["POST"])
@require_auth
def pause():
    write_paused(True)
    return redirect("/")


@app.route("/resume", methods=["POST"])
@require_auth
def resume():
    write_paused(False)
    return redirect("/")


if __name__ == "__main__":
    # 비밀번호가 없으면 시작하지 않습니다 (실수로 인터넷에 무방비 공개되는 것 방지).
    if not DASH_USER or not DASH_PASS:
        raise SystemExit(
            "[보안 중단] .env 에 DASH_USER / DASH_PASS 가 없습니다.\n"
            "비밀번호 없이는 대시보드를 열 수 없어요. .env 에 아래를 추가하세요:\n"
            "    DASH_USER=admin\n    DASH_PASS=원하는_강한_비밀번호\n"
        )
    print(f"대시보드 시작 → http://0.0.0.0:{DASH_PORT}  (아이디: {DASH_USER})")
    serve(app, host="0.0.0.0", port=DASH_PORT)
