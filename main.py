#!/usr/bin/env python3
"""AI 기반 Git 커밋 메시지 / PR 초안 생성기 (Upstage solar-pro3 전용).

git status / git diff 결과만 수집해 AI API를 1회 호출하고 초안을 출력한다.
git push, PR 생성 같은 원격 반영은 하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# ---- 모델 정책: solar-pro3 외 모델은 과금되므로 사용 금지 -------------------
ALLOWED_MODEL = "solar-pro3"
MODEL_FREE_UNTIL = datetime(2027, 4, 1, tzinfo=timezone.utc)  # 이후 모든 모델 무효
API_URL = "https://api.upstage.ai/v1/chat/completions"
KEY_ENVS = ("UPSTAGE_API_KEY", "AI_API_KEY")

COMMIT_TITLE_MAX = 72
COMMIT_TITLE_RECOMMENDED = 50
PR_TITLE_MAX = 80
PR_SECTIONS = ("Why", "What", "How to Test")

SAFE_MAX_FILES = 10
SAFE_MAX_LINES = 200

MASK_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[MASKED_PRIVATE_KEY]"),
    (re.compile(r"\b(?:sk|up|ghp|gho|ghs|xox[abp])[-_][A-Za-z0-9_\-]{16,}"), "[MASKED_API_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[MASKED_AWS_KEY]"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|passwd|password)\b(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}\3"),
     r"\1\2[MASKED]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[MASKED_EMAIL]"),
    (re.compile(r"\b01[016789][-\s]?\d{3,4}[-\s]?\d{4}\b"), "[MASKED_PHONE]"),
    (re.compile(r"\b\d{6}-[1-4]\d{6}\b"), "[MASKED_RRN]"),
]


class GenError(Exception):
    pass


def info(msg): print(f"[INFO] {msg}")
def done(msg): print(f"[DONE] {msg}")
def error(msg): print(f"[ERROR] {msg}", file=sys.stderr)


# ---- Git ------------------------------------------------------------------
def run_git(*args: str) -> str:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise GenError("git 명령을 찾을 수 없습니다. Git을 설치하세요.")
    if r.returncode != 0:
        raise GenError(f"git {' '.join(args)} 실패: {r.stderr.strip()}")
    return r.stdout


def collect_git():
    if run_git("rev-parse", "--is-inside-work-tree").strip() != "true":
        raise GenError("Git 저장소가 아닙니다. 프로젝트 루트에서 실행하세요.")
    status = run_git("status", "--porcelain")
    has_head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], capture_output=True).returncode == 0
    diff = run_git("diff", "HEAD") if has_head else run_git("diff", "--cached") + run_git("diff")
    branch = run_git("branch", "--show-current").strip() or "(detached)"
    return status, diff, branch


# ---- 안전 모드 -------------------------------------------------------------
def mask(text: str) -> str:
    for pat, rep in MASK_PATTERNS:
        text = pat.sub(rep, text)
    return text


def limit_diff(diff: str, max_files: int, max_lines: int):
    """파일/줄 수 제한. (잘린 diff, 잘렸는지) 반환."""
    chunks = re.split(r"(?m)^(?=diff --git )", diff)
    chunks = [c for c in chunks if c]
    truncated = len(chunks) > max_files
    out, lines = [], 0
    for c in chunks[:max_files]:
        cl = c.splitlines()
        room = max_lines - lines
        if room <= 0:
            truncated = True
            break
        if len(cl) > room:
            cl, truncated = cl[:room], True
        out.append("\n".join(cl))
        lines += len(cl)
    return "\n".join(out), truncated


# ---- 프롬프트 --------------------------------------------------------------
COMMIT_SYSTEM = f"""너는 시니어 개발자다. 주어진 git status/diff로 커밋 메시지를 한국어로 작성한다.
규칙:
- 첫 줄: Conventional Commits 형식 '<type>: <제목>' (type: feat/fix/docs/refactor/test/chore). {COMMIT_TITLE_RECOMMENDED}자 이내 권장, 최대 {COMMIT_TITLE_MAX}자.
- 빈 줄 후 본문: 핵심 변경 1~3개를 '- ' 불릿으로, 변경된 파일/모듈을 1~3개 언급.
- diff에 없는 내용은 지어내지 않는다. 마크다운 코드펜스, 설명, 인사말 없이 커밋 메시지만 출력한다."""

PR_SYSTEM = f"""너는 시니어 개발자다. 주어진 git status/diff로 Pull Request 초안을 한국어로 작성한다.
출력 형식(정확히 준수, 다른 텍스트 금지):
TITLE: <{PR_TITLE_MAX}자 이내 1줄 제목>
BODY:
## Why
- ...
## What
- ...
## How to Test
- ...
규칙: 세 섹션 모두 최소 1개 '- ' 불릿. diff에 없는 내용은 지어내지 않는다. 코드펜스 금지."""


def build_user_prompt(status, diff, branch, note, truncated):
    parts = [f"현재 브랜치: {branch}", f"[git status --porcelain]\n{status.strip()}"]
    if note:
        parts.append(f"[변경 이유/요구사항]\n{note}")
    parts.append(f"[git diff{' (일부만 전송됨)' if truncated else ''}]\n{diff}")
    return "\n\n".join(parts)


# ---- API -------------------------------------------------------------------
def ensure_model_allowed(model: str):
    if datetime.now(timezone.utc) >= MODEL_FREE_UNTIL:
        raise GenError("2027-04 이후에는 모든 모델이 유료이므로 실행이 차단됩니다(과금 방지).")
    if model != ALLOWED_MODEL:
        raise GenError(f"'{model}' 모델은 과금될 수 있어 사용할 수 없습니다. {ALLOWED_MODEL}만 허용됩니다.")


def load_dotenv():
    """스크립트 폴더 또는 ~/Desktop/.env에서 키를 읽는다(이미 설정된 환경변수가 우선)."""
    paths = [os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
             os.path.expanduser("~/Desktop/.env")]
    for path in paths:
        if not os.path.exists(path):
            continue
        for line in open(path, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith("UPSTAGE_API_KEY=") or line.startswith("AI_API_KEY="):
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ.setdefault(k, v.strip().strip("'\""))


def get_api_key() -> str:
    load_dotenv()
    for name in KEY_ENVS:
        v = os.environ.get(name, "").strip()
        if v:
            return v
    raise GenError('UPSTAGE_API_KEY 환경변수가 설정되지 않았습니다.\n## 예) export UPSTAGE_API_KEY="YOUR_KEY"')


def call_api(messages, model, temperature, max_tokens, timeout=120) -> str:
    ensure_model_allowed(model)
    key = get_api_key()
    body = json.dumps({"model": model, "messages": messages,
                       "temperature": temperature, "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        hint = " (인증 실패: API Key를 확인하세요)" if e.code in (401, 403) else \
               " (요청 한도 초과: 잠시 후 재시도하세요)" if e.code == 429 else ""
        raise GenError(f"API 오류 HTTP {e.code}{hint}: {detail}")
    except urllib.error.URLError as e:
        raise GenError(f"네트워크 오류: {e.reason}")
    except TimeoutError:
        raise GenError("API 응답 시간 초과")
    except json.JSONDecodeError:
        raise GenError("API 응답을 해석할 수 없습니다.")
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise GenError(f"예상치 못한 API 응답 형식: {str(data)[:200]}")


# ---- 검증 / 후처리 ------------------------------------------------------------
def strip_fence(text: str) -> str:
    text = re.sub(r"^```\w*\n|\n```$", "", text.strip())
    return text.strip()


def validate_commit(msg: str) -> list[str]:
    problems = []
    title = msg.splitlines()[0] if msg.strip() else ""
    if not title:
        problems.append("커밋 제목이 없습니다")
    if len(title) > COMMIT_TITLE_MAX:
        problems.append(f"커밋 제목이 {len(title)}자로 {COMMIT_TITLE_MAX}자를 초과합니다")
    return problems


def parse_pr(text: str):
    m = re.search(r"TITLE:\s*(.+)", text)
    title = m.group(1).strip() if m else ""
    b = re.search(r"BODY:\s*\n?([\s\S]*)", text)
    body = b.group(1).strip() if b else ""
    return title, body


def validate_pr(title: str, body: str) -> list[str]:
    problems = []
    if not title or "\n" in title:
        problems.append("PR 제목은 1줄이어야 합니다")
    if len(title) > PR_TITLE_MAX:
        problems.append(f"PR 제목이 {len(title)}자로 {PR_TITLE_MAX}자를 초과합니다")
    for sec in PR_SECTIONS:
        m = re.search(rf"(?mi)^##\s*{re.escape(sec)}\s*$([\s\S]*?)(?=^##\s|\Z)", body)
        if not m:
            problems.append(f"'## {sec}' 섹션이 없습니다")
        elif not re.search(r"(?m)^\s*[-*]\s+\S", m.group(1)):
            problems.append(f"'## {sec}' 섹션에 불릿이 없습니다")
    return problems


def trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def fix_commit(msg: str) -> str:
    lines = msg.splitlines() or [""]
    lines[0] = trunc(lines[0], COMMIT_TITLE_MAX)
    return "\n".join(lines)


def fix_pr(title: str, body: str):
    title = trunc(title.splitlines()[0] if title else "chore: 변경 사항 정리", PR_TITLE_MAX)
    for sec in PR_SECTIONS:
        m = re.search(rf"(?mi)^##\s*{re.escape(sec)}\s*$([\s\S]*?)(?=^##\s|\Z)", body)
        if not m:
            body += f"\n\n## {sec}\n- (내용 보완 필요)"
        elif not re.search(r"(?m)^\s*[-*]\s+\S", m.group(1)):
            body = body.replace(m.group(0), m.group(0).rstrip() + "\n- (내용 보완 필요)")
    return title, body.strip()


# ---- 명령 ------------------------------------------------------------------
def generate(cmd, status, diff, branch, args, truncated):
    system = COMMIT_SYSTEM if cmd == "commit" else PR_SYSTEM
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": build_user_prompt(status, diff, branch, args.note, truncated)}]
    calls = 1
    info("AI API 요청 중... (호출 1회)")
    text = strip_fence(call_api(messages, args.model, args.temperature, args.max_tokens))
    if cmd == "commit":
        problems = validate_commit(text)
    else:
        problems = validate_pr(*parse_pr(text))
    if problems and not args.no_retry:
        info(f"형식 위반 감지, 1회 재생성: {'; '.join(problems)}")
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "다음 문제를 고쳐 같은 형식으로 다시 출력하라: " + "; ".join(problems)}]
        text = strip_fence(call_api(messages, args.model, args.temperature, args.max_tokens))
        calls += 1
    info(f"AI API 호출 횟수: {calls}회")
    return text


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="main.py", description="AI 커밋 메시지/PR 초안 생성기 (solar-pro3 전용)")
    p.add_argument("command", choices=["commit", "pr"])
    p.add_argument("--model", default=ALLOWED_MODEL, help=f"기본/유일 허용: {ALLOWED_MODEL}")
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--max-tokens", type=int, default=600)
    p.add_argument("--safe-mode", action="store_true", help="민감정보 마스킹 + diff 전송 제한")
    p.add_argument("--max-files", type=int, default=SAFE_MAX_FILES)
    p.add_argument("--max-lines", type=int, default=SAFE_MAX_LINES)
    p.add_argument("--note", default="", help="변경 이유/요구사항 메모")
    p.add_argument("--no-retry", action="store_true", help="검증 실패 시 재생성하지 않고 후처리만")
    args = p.parse_args(argv)

    try:
        ensure_model_allowed(args.model)
        status, diff, branch = collect_git()
        if not status.strip() and not diff.strip():
            info("변경 사항이 없습니다. 생성하지 않고 종료합니다.")
            return 0
        info(f"현재 브랜치: {branch}")
        info(f"Git status 수집 완료: {len(status.splitlines())}개 파일 변경 감지")
        info(f"Git diff 수집 완료: {len(diff.splitlines())}줄")
        truncated = False
        if args.safe_mode:
            diff = mask(diff)
            diff, truncated = limit_diff(diff, args.max_files, args.max_lines)
            status = mask(status)
            info(f"safe-mode: 마스킹 적용, 최대 {args.max_files}파일/{args.max_lines}줄"
                 f"{' (일부 잘림)' if truncated else ''}")
        get_api_key()
        text = generate(args.command, status, diff, branch, args, truncated)
    except GenError as e:
        error(str(e))
        return 1

    if args.command == "commit":
        msg = fix_commit(text)
        done("커밋 메시지 생성 완료")
        print("\n--- Commit Message ---\n" + msg + "\n----------------------")
    else:
        title, body = fix_pr(*parse_pr(text))
        done("PR 초안 생성 완료")
        print("\n--- PR Title ---\n" + title + "\n\n--- PR Body ---\n" + body + "\n----------------")
    print("※ AI 초안입니다. 반드시 검토 후 적용하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
