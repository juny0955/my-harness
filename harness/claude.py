"""Claude Code 어댑터: hook/statusLine의 stdin JSON ↔ 코어 호출.

사용: python3 -m harness.claude <prompt|pretool|session-start|statusline>
"""
from __future__ import annotations

import json
import sys

from . import core

LABEL = {"learn": "🎓 LEARN", "collab": "🤝 COLLAB", "normal": "NORMAL"}
SUMMARY = {
    "learn": "Learn 모드: 설명과 힌트 중심, 프로젝트 파일 수정은 차단됩니다.",
    "collab": "Collab 모드: 설계 토론·리뷰 중심, 명시적으로 요청할 때만 구현합니다.",
    "normal": "Normal 모드: 하네스 제약 없이 기본 Claude Code로 동작합니다.",
}


def on_prompt(data: dict) -> dict:
    sid = data.get("session_id", "")
    cmd = core.parse_command(data.get("prompt", ""))
    if cmd == "mode":
        mode = core.get_mode(sid)
        return _block(f"현재 모드: {mode}\n{SUMMARY[mode]}\n전환: /learn /collab /normal")
    if cmd:
        core.set_mode(sid, cmd)
        return _block(f"✔ {SUMMARY[cmd]}")
    mode = core.get_mode(sid)
    context = core.build_context(mode, core.get_announced(sid))
    core.mark_announced(sid, mode)
    if not context:
        return {}
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}


def _block(message: str) -> dict:
    # 모델을 호출하지 않고 사용자에게만 메시지를 보여준다.
    return {"decision": "block", "reason": message}


def on_pretool(data: dict) -> dict:
    if core.get_mode(data.get("session_id", "")) != "learn":
        return {}
    reason = core.learn_denial(
        data.get("tool_name", ""), data.get("tool_input") or {}, data.get("cwd", ""),
    )
    if not reason:
        return {}
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason + " — 수정이 필요하면 사용자에게 /collab 또는 /normal 전환을 안내하라.",
    }}


def on_session_start(data: dict) -> dict:
    core.prune_state()
    return {}


def statusline(data: dict) -> str:
    return LABEL[core.get_mode(data.get("session_id", ""))]


def main(argv: list[str]) -> int:
    event = argv[1] if len(argv) > 1 else ""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        data = {}
    try:
        if event == "statusline":
            print(statusline(data))
            return 0
        handler = {"prompt": on_prompt, "pretool": on_pretool, "session-start": on_session_start}.get(event)
        out = handler(data) if handler else {}
    except Exception as e:  # 하네스 오류가 세션을 막지 않도록 fail-open
        print(f"harness: {event} failed: {e}", file=sys.stderr)
        return 0
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
