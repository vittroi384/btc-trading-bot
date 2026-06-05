# =====================================================================
#  notifier.py  ―  텔레그램으로 '알림 보내기'와 '명령 받기'를 담당
# ---------------------------------------------------------------------
#  - 봇이 매수/매도 했을 때, 또는 정기적으로 텔레그램 메시지를 보냅니다.
#  - 내가 텔레그램에서 /status, /pause 같은 명령을 보내면 그걸 알아듣고 처리합니다.
#  - 무거운 라이브러리 없이, 텔레그램이 제공하는 기본 통신(Bot API)만 사용합니다.
# =====================================================================

import threading   # threading: 두 가지 일을 '동시에' 하기 위한 도구
                   #            (매매 루프와 '명령 듣기'를 동시에 돌리는 데 씁니다)
import time        # 시간 관련 (잠깐 쉬기 등)
import requests    # requests: 인터넷으로 데이터를 주고받는 도구 (텔레그램 서버와 통신)


class TelegramNotifier:
    """텔레그램 알림/명령을 담당하는 '비서'입니다. (class = 기능 묶음 설계도)"""

    def __init__(self, token, chat_id):
        """비서를 만들 때 한 번 실행. token=봇 열쇠, chat_id=내 채팅 번호."""
        self.token = token
        self.chat_id = str(chat_id)                          # 숫자를 글자로 바꿔 보관
        self.base = f"https://api.telegram.org/bot{token}"   # 텔레그램 서버 주소
        self.enabled = bool(token and chat_id)               # 토큰/번호가 둘 다 있어야 작동
        self._offset = None        # 이미 읽은 메시지를 다시 안 읽기 위한 표시
        self._stop_flag = False    # 명령 듣기를 멈출지 여부

        # 아래 4개는 '특정 명령이 오면 실행할 함수'를 담아둘 자리입니다.
        # 실제 함수는 bot.py 에서 채워 넣습니다. (지금은 비어 있음)
        self.on_status = None
        self.on_pause = None
        self.on_resume = None
        self.on_stop = None

    def send(self, text):
        """텔레그램으로 메시지(text)를 보냅니다."""
        if not self.enabled:
            print("[텔레그램 미설정] " + text)  # 설정이 없으면 그냥 화면에 출력
            return
        try:
            # 텔레그램 서버에 "이 채팅으로 이 글을 보내줘" 라고 요청합니다.
            requests.post(
                self.base + "/sendMessage",
                data={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
        except Exception as e:
            print("텔레그램 전송 실패:", e)  # 인터넷 문제 등으로 실패해도 봇은 멈추지 않습니다

    # -------------------- 명령 듣기 (백그라운드) --------------------
    def start_command_listener(self):
        """
        '명령 듣기'를 별도로 동시에 시작합니다.
        이렇게 해두면 매매를 돌리는 와중에도 내 명령(/status 등)을 받을 수 있습니다.
        """
        if not self.enabled:
            return
        # daemon=True : 메인 프로그램이 끝나면 이 듣기도 같이 끝난다는 뜻입니다.
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()

    def stop(self):
        """명령 듣기를 멈추라는 신호."""
        self._stop_flag = True

    def _poll_loop(self):
        """
        텔레그램 서버에 '새 메시지 왔어?' 라고 계속 물어보는 반복문입니다.
        새 메시지가 오면 명령인지 확인해서 처리합니다.
        """
        while not self._stop_flag:       # 멈추라는 신호가 오기 전까지 계속 반복
            try:
                params = {"timeout": 30}             # 최대 30초 기다리며 메시지 확인
                if self._offset is not None:
                    params["offset"] = self._offset  # 이미 본 메시지는 건너뛰기
                r = requests.get(self.base + "/getUpdates", params=params, timeout=40)
                for upd in r.json().get("result", []):   # 새로 온 메시지들을 하나씩
                    self._offset = upd["update_id"] + 1  # 다음엔 이 다음 것부터 보도록 표시
                    msg = upd.get("message") or upd.get("channel_post")
                    if not msg:
                        continue
                    # 다른 사람이 보낸 건 무시하고, '내 채팅'에서 온 것만 처리 (보안)
                    if str(msg.get("chat", {}).get("id")) != self.chat_id:
                        continue
                    # 받은 글을 소문자로 정리해서 명령 처리로 넘깁니다.
                    self._handle_command((msg.get("text") or "").strip().lower())
            except Exception:
                time.sleep(5)  # 오류가 나도 5초 쉬고 계속 시도

    def _handle_command(self, text):
        """받은 글이 어떤 명령인지 보고, 거기에 맞는 함수를 실행합니다."""
        if text in ("/status", "/stats"):
            if self.on_status:
                self.on_status()
        elif text == "/pause":
            if self.on_pause:
                self.on_pause()
        elif text == "/resume":
            if self.on_resume:
                self.on_resume()
        elif text == "/stop":
            if self.on_stop:
                self.on_stop()
