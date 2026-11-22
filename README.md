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
| `POST /sessions/{session_id}/chat` | `{"message": "..."}`를 보내면 `reply` 반환 |
| `GET /sessions/{session_id}` | 대화 기록과 상태 |

`.env`의 `DEBUG=1`이면 응답에 판정 결과와 상태가 함께 나옵니다. 실제 서비스할 때는 `DEBUG=0`으로 바꾸세요.

## 5. 튜닝하기

- **말투나 설정:** `yoon_seoa_prompt.md`를 고치면 서버를 재시작했을 때 반영됩니다.
- **진행 속도:** `engine.py` 맨 위 상수를 바꾸세요. 예: 단계가 너무 빨리 오르면 `AFFECTION_CAP`이나 `_next_stage`의 기준값을 높이고, 사건이 너무 자주 나오면 `QUIET_TURNS_TRIGGER`를 늘리세요.
- **모델:** `.env`의 `CHAT_MODEL`, `JUDGE_MODEL`을 바꾸면 됩니다.

## 자주 나는 오류

| 증상 | 해결 |
| --- | --- |
| `.env 파일에 GEMINI_API_KEY가 없습니다` | `.env` 파일 이름과 위치(이 폴더 안)를 확인하세요 |
| `429` 또는 `ResourceExhausted` | 무료 한도 초과. 잠시 뒤 다시 시도하세요 |
| 서아가 "방금 뭐라고 했어"만 반복 | Gemini 안전 필터가 응답을 막은 경우입니다. 직전 메시지 수위를 낮춰 보세요 |
| `ModuleNotFoundError` | 가상 환경을 켰는지 확인하고 `pip install -r requirements.txt`를 다시 실행하세요 |

## 주의

- `.env`와 `seoa.db`는 GitHub에 올리지 마세요 (`.gitignore`에 이미 들어 있습니다).
- 무료 요금제 요청은 Google 모델 개선에 쓰일 수 있으니 실제 개인정보는 넣지 마세요.
- 공개 서비스로 내보내기 전에 성인 인증과 각 LLM 회사의 이용 정책을 확인하세요.
