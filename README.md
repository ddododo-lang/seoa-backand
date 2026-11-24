# 윤서아 캐릭터챗 백엔드

Gemini API로 윤서아와 대화하는 최소 백엔드입니다. 매 턴 프롬프트 조립, 서아 응답, 상태 판정, 단계 상승, 사건 발동, 대화 요약을 자동으로 처리합니다.

## 파일 구성

| 파일 | 하는 일 |
| --- | --- |
| `yoon_seoa_prompt.md` | 서아의 캐릭터 프롬프트 (설정을 바꾸려면 이 파일을 고치세요) |
| `prompt_loader.py` | 프롬프트 파일을 블록별로 잘라서 불러옴 |
| `llm.py` | Gemini 호출. 다른 모델로 바꿀 때 이 파일만 고치면 됨 |
| `db.py` | SQLite에 세션 상태와 대화 기록 저장 |
| `engine.py` | 한 턴의 전체 흐름과 게임 규칙 (단계 조건, 호감도 상한, 사건 목록) |
| `server.py` | 웹 API 서버 (프런트엔드가 호출하는 곳) |
| `play.py` | 터미널에서 바로 대화해 보는 테스트 스크립트 |

## 1. 처음 설치 (한 번만)

Python 3.10 이상이 필요합니다. 터미널(Windows는 PowerShell)을 열고 이 폴더로 이동하세요.

```bash
cd seoa_backend

# 가상 환경 만들기 (이 프로젝트 전용 파이썬 공간)
python -m venv .venv

# 가상 환경 켜기
# Windows:
.venv\Scripts\activate
# Mac / Linux:
source .venv/bin/activate

# 필요한 라이브러리 설치
pip install -r requirements.txt
```

## 2. API 키 넣기

`.env.example` 파일을 복사해서 이름을 `.env`로 바꾸고, `GEMINI_API_KEY=` 뒤에 발급받은 키를 붙여 넣으세요.

```
GEMINI_API_KEY=AIza로_시작하는_내_키
LLM_PROVIDER=gemini
```

> 키 없이 흐름만 먼저 보고 싶으면 `LLM_PROVIDER=fake`로 두세요. 가짜 응답이 나오지만 단계 상승과 사건 발동은 그대로 확인할 수 있습니다.

## 3. 터미널에서 서아와 대화해 보기

```bash
python play.py
```

- 오프닝이 나오면 바로 대화하면 됩니다.
- `/state` 입력: 현재 단계, 호감도, 플래그 등 상태 보기
- `/quit` 입력: 종료. 마지막에 나오는 세션 ID로 `python play.py 세션ID` 하면 이어서 할 수 있어요.
- 응답 아래 괄호에 호감도 변화, 단계 상승, 사건 발동이 표시됩니다.

## 4. API 서버로 실행하기

```bash
uvicorn server:app --reload
```

브라우저에서 http://127.0.0.1:8000/docs 를 열면 API를 바로 눌러 보며 테스트할 수 있습니다.

| 요청 | 설명 |
| --- | --- |
| `POST /sessions` | 새 대화 시작. `session_id`와 `opening` 반환 |
| `POST /sessions/{session_id}/chat` | `{"message": "..."}`를 보내면 `reply` 반환 (테스트용, SQLite 저장) |
| `GET /sessions/{session_id}` | 대화 기록과 상태 |

### moti 앱용 API (v2, 상태 없음)

DB 없이 동작합니다. 이야기 상태는 앱(localStorage)이 보관해서 매 요청에 보내고, 서버는 아무것도 저장하지 않습니다.

| 요청 | 보내는 것 | 돌려주는 것 |
| --- | --- | --- |
| `POST /chat` | `char`, `message`, `history`(최근 16턴), `state`, `motion_list`(해금된 몸짓, 직전 몸짓 제외) | `{action, dialogue, motion_id, emotion, location}` |
| `POST /judge` | `char`, `state`, `last_user`, `last_reply`, `need_summary`(6턴마다), `recent`(요약용 최근 대화) | `{state, stage_up, flags, pending_event}` |

- `/chat`은 Gemini에 JSON 응답(스키마)을 강제합니다. 파싱에 실패하면 1번 더 시도하고, 그래도 안 되면 `"…"` 대답을 돌려줍니다. 목록에 없는 `motion_id`, 허용 밖의 `emotion`·`location`은 각각 `null`, `평온`, `null`로 바꿉니다.
- `/judge`는 판정 후 `engine.py`의 규칙(호감도 상한, 단계 상승, 비밀 공개, 출발일, 사건 발동)을 적용하고, 다음 턴에 쓸 사건을 `state.pending_event`에 넣어 돌려줍니다. 앱은 다음 `/chat`에 그대로 보내고, 응답을 받으면 `null`로 돌립니다.
- 예전 `/sessions` API와 `play.py`는 테스트용으로 그대로 남아 있습니다 (이쪽만 SQLite를 씁니다).

**앱에서 시험하기:** 서버를 켠 뒤 `https://bii0411.github.io/Moti/?api=http://localhost:8000` 처럼 주소 뒤에 `?api=`를 붙이면 그 서버로 연결됩니다. `LLM_PROVIDER=fake`로 켜면 키 없이도 단계 상승·사건·해금 흐름을 볼 수 있습니다.

`.env`의 `DEBUG=1`이면 응답에 판정 결과와 상태가 함께 나옵니다. 실제 서비스할 때는 `DEBUG=0`으로 바꾸세요.

## 5. 튜닝하기

- **말투나 설정:** `yoon_seoa_prompt.md`를 고치면 서버를 재시작했을 때 반영됩니다.
- **진행 속도:** `engine.py` 맨 위 상수를 바꾸세요. 예: 단계가 너무 빨리 오르면 `AFFECTION_CAP`이나 `_next_stage`의 기준값을 높이고, 사건이 너무 자주 나오면 `QUIET_TURNS_TRIGGER`를 늘리세요.
- **모델:** `.env`의 `CHAT_MODELS`, `JUDGE_MODELS`에 쉼표로 순서대로 적습니다. 지금 어떤 모델이 답하는지는 `python check_models.py`로 확인하세요.

## Google이 바쁠 때 (503·429·시간 초과)

- 503(과부하)이면 같은 모델로 1초, 2초 쉬고 최대 2번 다시 시도합니다. 429(한도 초과), 시간 초과, 404(없는 모델)는 기다리지 않고 바로 다음 모델로 넘깁니다.
- 그래도 안 되면 `CHAT_MODELS`(판정은 `JUDGE_MODELS`)의 다음 모델로 넘어갑니다.
- 요청 하나는 `LLM_BUDGET_SEC`(기본 15초) 안에 끝납니다. 모든 모델이 실패하면 서아가 「…」로 대신 답합니다.
- 서버 로그에 `[llm] chat 응답 모델=gemini-3.5-flash 2.1초 (넘김: gemini-3.6-flash 503)`처럼 한 줄씩 남습니다 (키는 찍지 않음).
- 키 없이 도는 테스트: `python -m unittest discover -s tests -v`
- 실제 사용처럼 점검하기: `python eval_chat.py --pause 20` (턴 사이 20초 쉬기. 몰아서 보내면 실제보다 429가 많이 납니다)

## 자주 나는 오류

| 증상 | 해결 |
| --- | --- |
| `.env 파일에 GEMINI_API_KEY가 없습니다` | `.env` 파일 이름과 위치(이 폴더 안)를 확인하세요 |
| `429` 또는 `ResourceExhausted` | 무료 한도 초과. 잠시 뒤 다시 시도하세요 |
| `503 UNAVAILABLE` / `404 NOT_FOUND` | 모델이 바쁘거나 더 이상 제공되지 않음. `python check_models.py`로 되는 모델을 찾아 `CHAT_MODELS` 앞에 두세요 |
| 서아가 "방금 뭐라고 했어"만 반복 | Gemini 안전 필터가 응답을 막은 경우입니다. 직전 메시지 수위를 낮춰 보세요 |
| `ModuleNotFoundError` | 가상 환경을 켰는지 확인하고 `pip install -r requirements.txt`를 다시 실행하세요 |

## 주의

- `.env`와 `seoa.db`는 GitHub에 올리지 마세요 (`.gitignore`에 이미 들어 있습니다).
- 무료 요금제 요청은 Google 모델 개선에 쓰일 수 있으니 실제 개인정보는 넣지 마세요.
- 공개 서비스로 내보내기 전에 성인 인증과 각 LLM 회사의 이용 정책을 확인하세요.
