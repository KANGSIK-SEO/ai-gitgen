# ai-gitgen — AI 커밋 메시지 / PR 초안 생성기

`git status` / `git diff`를 수집해 **Upstage `solar-pro3`** API를 1회 호출하고, 커밋 메시지와 PR 초안(Why/What/How to Test)을 터미널에 출력하는 Python CLI입니다. 출력까지만 하며 `git push`, PR 생성은 하지 않습니다.

## 설치
Python 3.10+ 필요, 외부 패키지 없음(표준 라이브러리만 사용).
```bash
git clone <this-repo> && cd ai-gitgen
```

## API Key 설정
```bash
export UPSTAGE_API_KEY="YOUR_KEY"   # AI_API_KEY도 인식
```
또는 `~/Desktop/.env`(또는 이 폴더의 `.env`)에 `UPSTAGE_API_KEY=YOUR_KEY` 한 줄을 넣으면 자동으로 읽습니다(환경변수가 우선). 코드/리포지토리에 키를 넣지 마세요(`.env`는 `.gitignore` 처리됨).

## 사용법
분석 대상 Git 프로젝트 **루트**에서 실행합니다.
```bash
python /path/to/ai-gitgen/main.py commit
python /path/to/ai-gitgen/main.py pr --note "로그인 실패 시 재시도 추가"
python main.py commit --safe-mode --temperature 0.2 --max-tokens 400
```
| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--model` | `solar-pro3` | solar-pro3만 허용(그 외 거부) |
| `--temperature` | 0.3 | 낮을수록 일관적/보수적, 높을수록 다양 |
| `--max-tokens` | 600 | 출력 길이 상한 (너무 작으면 잘림) |
| `--safe-mode` | off | 민감정보 마스킹 + diff 전송 제한 |
| `--max-files` / `--max-lines` | 10 / 200 | safe-mode 전송 상한 |
| `--note` | - | 변경 이유/요구사항 컨텍스트 |
| `--no-retry` | off | 검증 실패 시 재생성 없이 후처리만 |

### 짧은 명령어 (선택)
```bash
ln -s "$PWD/main.py" ~/bin/gitgen        # gitgen commit / gitgen pr (초안 출력 전용)
ln -s "$PWD/ai_git.py" ~/bin/ai-gitgen   # ai-gitgen <모든 git 명령>
```
`ai-gitgen commit`은 메시지 생성 후 `[y/N]` 확인을 거쳐 `add -A` + 로컬 커밋을 하고, `ai-gitgen pr`은 초안 출력, 그 외(`status`, `diff`, `push` …)는 그대로 git에 위임합니다. `main.py` 본체는 과제 범위대로 초안 출력만 합니다.

## 출력 예시
```
[INFO] 현재 브랜치: feature/commit-pr-generator
[INFO] Git status 수집 완료: 3개 파일 변경 감지
[INFO] Git diff 수집 완료: 128줄
[INFO] AI API 요청 중... (호출 1회)
[INFO] AI API 호출 횟수: 1회
[DONE] 커밋 메시지 생성 완료

--- Commit Message ---
feat: Git 변경 사항 기반 커밋 메시지 자동 생성 기능 추가

- main.py: git diff를 수집해 AI 입력 컨텍스트로 전달
- API Key 미설정 시 안내 메시지 및 에러 처리 개선
----------------------
```
```
--- PR Title ---
feat: 커밋/PR 자동 생성 기능 추가

--- PR Body ---
## Why
- 커밋/PR 설명 작성 시간을 줄이고 형식을 일관되게 하기 위해
## What
- git status/diff 수집 후 AI 컨텍스트로 전달
- commit / pr 명령 추가
## How to Test
- export UPSTAGE_API_KEY="YOUR_KEY"
- python main.py pr
```
변경 없음: `[INFO] 변경 사항이 없습니다. 생성하지 않고 종료합니다.`
키 없음: `[ERROR] UPSTAGE_API_KEY 환경변수가 설정되지 않았습니다.`

## 동작 원리
1. `git status --porcelain`, `git diff HEAD`(HEAD 없으면 staged+unstaged) 수집 (변경 없으면 종료)
2. 시스템 프롬프트(Conventional Commits/PR 템플릿 규칙) + status + diff + `--note`를 REST(`/v1/chat/completions`)로 전송
3. **검증**: 커밋 제목 ≤72자(권장 50), PR 제목 ≤80자, PR 본문 Why/What/How to Test 각 ≥1 불릿
4. 위반 시 피드백과 함께 **1회 재생성**(총 최대 2회), 그래도 위반이면 후처리(제목 절단·섹션 보완)
5. 오류(네트워크/401·403 인증/429 한도/타임아웃)는 원인을 포함해 출력

## 주의사항 / 운영
- **민감정보**: diff에 키·이메일·전화·주민번호·비밀키가 있을 수 있습니다. `--safe-mode`는 정규식으로 마스킹하고 최대 10파일/200줄만 전송합니다(초과분은 잘림 표시). 미사용 시 diff 원문이 외부 API로 전송됩니다.
- **비용/횟수**: 1회 실행당 API 1회(재생성 시 최대 2회), 호출 횟수를 로그로 출력합니다.
- **모델 제한**: `solar-pro3`만 사용합니다. **2027-04-01 이후에는 모든 모델이 유료화되어 실행이 차단**됩니다(`MODEL_FREE_UNTIL`).
- 생성 결과는 최종 정답이 아니므로 반드시 검토 후 적용하세요.

## 테스트
```bash
pip install pytest && pytest -q tests
```
