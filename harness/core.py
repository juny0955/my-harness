"""에이전트 비종속 코어: 모드 상태, 프롬프트 로딩, Learn 모드 정책.

Claude Code에 대한 지식은 harness/claude.py에만 둔다.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import time
from pathlib import Path

MODES = ("learn", "collab", "normal")
DEFAULT_MODE = "normal"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
STATE_TTL_DAYS = 30


def state_dir() -> Path:
    return Path(os.environ.get("HARNESS_STATE_DIR") or Path.home() / ".claude" / "harness" / "state")


def _safe(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", session_id or "unknown")[:128]


def _read(session_id: str) -> dict:
    try:
        data = json.loads((state_dir() / _safe(session_id)).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(session_id: str, data: dict) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{_safe(session_id)}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(data))
    tmp.replace(d / _safe(session_id))  # atomic


def get_mode(session_id: str) -> str:
    mode = _read(session_id).get("mode")
    return mode if mode in MODES else DEFAULT_MODE


def get_announced(session_id: str) -> str:
    """모델에게 마지막으로 알린 모드."""
    mode = _read(session_id).get("announced")
    return mode if mode in MODES else DEFAULT_MODE


def set_mode(session_id: str, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"unknown mode: {mode}")
    data = _read(session_id)
    data["mode"] = mode
    _write(session_id, data)


def mark_announced(session_id: str, mode: str) -> None:
    data = _read(session_id)
    data["announced"] = mode
    _write(session_id, data)


def prune_state(now: float | None = None) -> None:
    cutoff = (now or time.time()) - STATE_TTL_DAYS * 86400
    try:
        for p in state_dir().iterdir():
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
    except OSError:
        pass


def prompt_for(mode: str) -> str:
    return (PROMPTS_DIR / f"{mode}.md").read_text().strip()


def build_context(mode: str, previous: str | None) -> str:
    """매 프롬프트에 주입할 지침. 모드가 바뀐 직후에는 이전 지침 무효화를 명시한다."""
    parts = []
    if previous and previous != mode:
        parts.append(f"[HARNESS] 모드가 {previous} → {mode}로 전환되었다. 대화에 남아 있는 {previous} 모드 지침은 더 이상 유효하지 않다.")
    if mode != "normal" or previous not in (None, "normal"):
        parts.append(prompt_for(mode))
    return "\n\n".join(parts)


# ---- 슬래시 명령 해석 -------------------------------------------------------

_CMD = re.compile(r"^\s*/(learn|collab|normal|mode)\s*$")


def parse_command(prompt: str) -> str | None:
    m = _CMD.match(prompt or "")
    return m.group(1) if m else None


# ---- Learn 모드 정책 --------------------------------------------------------

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _inside(path: str, root: str) -> bool:
    try:
        p = Path(os.path.expanduser(path))
        if not p.is_absolute():
            p = Path(root) / p
        p = p.resolve()
        r = Path(root).resolve()
        return p == r or r in p.parents
    except (OSError, RuntimeError):
        return True  # 판단 불가 시 보수적으로 프로젝트 내부로 간주


# 프로젝트를 변경할 가능성이 높은 셸 패턴 (best-effort, 샌드박스가 아니다)
_MUTATING_CMD = {
    "rm", "rmdir", "mv", "cp", "touch", "mkdir", "ln", "chmod", "chown", "truncate", "dd", "install", "patch",
}
_GIT_MUTATING = {
    "add", "commit", "checkout", "switch", "restore", "reset", "revert", "merge", "rebase", "cherry-pick",
    "apply", "am", "clean", "rm", "mv", "pull", "push",
}
_REDIRECT = re.compile(r"(?<![0-9&<>])>>?(?!&)\s*(?P<target>[^\s;|&]+)")
_SEPARATORS = re.compile(r"\|\||&&|;|\||\n")


def bash_mutation(command: str) -> str | None:
    """프로젝트를 수정할 것으로 보이는 명령이면 사유 문자열, 아니면 None."""
    for m in _REDIRECT.finditer(command):
        t = m.group("target").strip("'\"")
        if t not in ("/dev/null", "/dev/stderr", "/dev/stdout") and not t.startswith(("&", "(")):
            return f"리다이렉션으로 파일 쓰기 ({t})"
    for seg in _SEPARATORS.split(command):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        while words and re.match(r"^\w+=", words[0]):  # FOO=bar cmd
            words = words[1:]
        while words and words[0] in ("sudo", "command", "env", "rtk", "nohup", "time"):
            words = words[1:]
            if words and words[0] == "bypass":
                words = words[1:]
        if not words:
            continue
        cmd = os.path.basename(words[0])
        if cmd in _MUTATING_CMD:
            return f"파일 변경 명령 ({cmd})"
        if cmd == "tee":
            return "tee로 파일 쓰기"
        if cmd == "sed" and any(w == "-i" or w.startswith("-i") or w == "--in-place" for w in words[1:]):
            return "sed -i 로 파일 수정"
        if cmd == "perl" and any(re.match(r"^-\w*i", w) for w in words[1:]):
            return "perl -i 로 파일 수정"
        if cmd == "git" and len(words) > 1:
            sub = next((w for w in words[1:] if not w.startswith("-")), "")
            if sub in _GIT_MUTATING:
                return f"git {sub}"
            if sub == "stash" and not any(w in ("list", "show") for w in words[2:]):
                return "git stash"
    return None


def learn_denial(tool_name: str, tool_input: dict, cwd: str, project_dir: str | None = None) -> str | None:
    """Learn 모드에서 도구 호출을 거부해야 하면 사유를 반환한다."""
    root = project_dir or cwd or os.getcwd()
    if tool_name in EDIT_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if path and not _inside(path, root):
            return None  # 프로젝트 밖(메모리, 임시 폴더 등)은 허용
        return f"Learn 모드에서는 프로젝트 파일을 수정할 수 없다 ({tool_name}: {path or '?'})"
    if tool_name == "Bash":
        why = bash_mutation(tool_input.get("command", ""))
        if why:
            return f"Learn 모드에서는 프로젝트를 수정하는 명령을 실행할 수 없다: {why}"
    return None
