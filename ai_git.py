#!/usr/bin/env python3
"""ai-gitgen <git 명령어>: 모든 git 명령어를 그대로 실행하되, commit/pr은 AI가 처리한다.

  ai-gitgen commit   -> AI가 메시지 생성 -> 확인(y) -> add -A + git commit
  ai-gitgen pr       -> AI가 PR 제목/본문 초안 출력
  ai-gitgen status / diff / log / push ...  -> 그대로 git 실행
  ai-gitgen commit -m "직접"                 -> 그대로 git 실행
(과제 본체 main.py는 초안 출력 전용. 이 래퍼는 사용자 확인 후 로컬 커밋만 수행하며 push는 하지 않는다.)
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import main as gen  # noqa: E402


def passthrough(argv):
    return subprocess.call(["git", *argv])


def ai_commit(ns):
    status, diff, branch = gen.collect_git()
    if not status.strip() and not diff.strip():
        gen.info("변경 사항이 없습니다. 커밋하지 않고 종료합니다.")
        return 0
    gen.info(f"Git status 수집 완료: {len(status.splitlines())}개 파일 변경 감지")
    truncated = False
    if ns.safe_mode:
        diff, status = gen.mask(diff), gen.mask(status)
        diff, truncated = gen.limit_diff(diff, ns.max_files, ns.max_lines)
        gen.info("safe-mode 적용")
    gen.get_api_key()
    msg = gen.fix_commit(gen.generate("commit", status, diff, branch, ns, truncated))
    print("\n--- Commit Message ---\n" + msg + "\n----------------------")
    if input("이 메시지로 모든 변경을 add 후 커밋할까요? [y/N] ").strip().lower() != "y":
        print("취소했습니다. (커밋 안 함)")
        return 0
    title, _, body = msg.partition("\n\n")
    cmd = ["git", "commit", "-m", title] + (["-m", body] if body else [])
    subprocess.check_call(["git", "add", "-A"])
    return subprocess.call(cmd)


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] not in ("commit", "pr"):
        return passthrough(argv)
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("command")
    p.add_argument("--model", default=gen.ALLOWED_MODEL)
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--max-tokens", type=int, default=600)
    p.add_argument("--safe-mode", action="store_true")
    p.add_argument("--max-files", type=int, default=gen.SAFE_MAX_FILES)
    p.add_argument("--max-lines", type=int, default=gen.SAFE_MAX_LINES)
    p.add_argument("--note", default="")
    p.add_argument("--no-retry", action="store_true")
    ns, rest = p.parse_known_args(argv)
    if rest:  # git 자체 옵션(-m, --amend 등)이 섞여 있으면 git에 위임
        return passthrough(argv)
    try:
        if ns.command == "pr":
            return gen.main(argv)
        gen.ensure_model_allowed(ns.model)
        return ai_commit(ns)
    except gen.GenError as e:
        gen.error(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
