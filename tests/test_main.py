import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from datetime import datetime, timezone
import pytest
import main
# 12

def test_mask():
    t = "key=sk-abcdefghijklmnopqrst a@b.com 010-1234-5678 password = hunter2222"
    m = main.mask(t)
    assert "sk-abc" not in m and "a@b.com" not in m and "1234-5678" not in m and "hunter" not in m


def test_limit_diff():
    d = "".join(f"diff --git a/f{i} b/f{i}\n+x\n+y\n" for i in range(15))
    out, tr = main.limit_diff(d, 10, 200)
    assert tr and out.count("diff --git") == 10
    out, tr = main.limit_diff(d, 10, 5)
    assert tr and len(out.splitlines()) <= 5


def test_validate_and_fix_pr():
    assert main.validate_pr("t", "## Why\n- a\n## What\n- b\n## How to Test\n- c") == []
    title, body = main.fix_pr("x" * 100, "## Why\n- a")
    assert len(title) <= 80 and not main.validate_pr(title, body)


def test_commit_title():
    assert main.validate_commit("a" * 80)
    assert len(main.fix_commit("a" * 80 + "\n\n- b").splitlines()[0]) <= 72


def test_model_lock():
    with pytest.raises(main.GenError):
        main.ensure_model_allowed("gpt-4o")
    main.ensure_model_allowed("solar-pro3")


def test_expiry(monkeypatch):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 4, 2, tzinfo=timezone.utc)
    monkeypatch.setattr(main, "datetime", FakeDT)
    with pytest.raises(main.GenError):
        main.ensure_model_allowed("solar-pro3")


def test_commit_blank_line_and_trailing_spaces():
    out = main.fix_commit("feat: 제목  \n- 하나  \n- 둘  ")
    assert out == "feat: 제목\n\n- 하나\n- 둘"
