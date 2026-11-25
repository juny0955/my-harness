#!/usr/bin/env python3
"""my-harness 설치기: install | uninstall | status  (Claude Code 전용, 표준 라이브러리만 사용)

- ~/.claude/settings.json 에 훅/상태표시줄을 병합한다. 우리 항목만 추가/제거하고 나머지는 건드리지 않는다.
- ~/.claude/skills/ 에 이 저장소의 skills/* 를 심볼릭 링크한다. 이미 있는 다른 항목은 덮어쓰지 않는다.
- 업데이트: 저장소를 git pull 한 뒤 `install` 을 다시 실행한다 (멱등).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
SKILLS = ("learn", "collab", "normal", "mode")
MARK = "harness.claude"  # 우리가 추가한 훅 command 식별자
EDIT_MATCHER = "Edit|Write|MultiEdit|NotebookEdit|Bash"


def claude_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()


def hook_cmd(event: str) -> str:
    return f"PYTHONPATH={shlex.quote(str(REPO))} python3 -m {MARK} {event}"


def desired_hooks() -> dict[str, list[dict]]:
    def entry(event: str, arg: str, matcher: str | None = None, timeout: int = 5) -> dict:
        e: dict = {"hooks": [{"type": "command", "command": hook_cmd(arg), "timeout": timeout}]}
        if matcher:
            e["matcher"] = matcher
        return e
    return {
        "SessionStart": [entry("SessionStart", "session-start")],
        "UserPromptSubmit": [entry("UserPromptSubmit", "prompt")],
        "PreToolUse": [entry("PreToolUse", "pretool", EDIT_MATCHER)],
    }


def is_ours(group: dict) -> bool:
    return any(MARK in h.get("command", "") for h in group.get("hooks", []))


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text() or "{}")
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: 최상위가 JSON 객체가 아니다")
    return data


def save(path: Path, data: dict, dry: bool) -> None:
    if dry:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.harness-bak-{time.strftime('%Y%m%d-%H%M%S')}"))
    tmp = path.with_name(path.name + ".harness-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def strip_ours(data: dict) -> None:
    hooks = data.get("hooks", {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not is_ours(g)]
        if not hooks[event]:
            del hooks[event]
    if "hooks" in data and not data["hooks"]:
        del data["hooks"]


def status_cmd() -> str:
    return hook_cmd("statusline")


def install(cdir: Path, dry: bool) -> None:
    settings = cdir / "settings.json"
    data = load(settings)
    strip_ours(data)  # 경로가 바뀌었을 수 있으므로 재설치 시 우리 항목을 먼저 제거
    for event, groups in desired_hooks().items():
        data.setdefault("hooks", {}).setdefault(event, []).extend(groups)
    sl = data.get("statusLine")
    if sl is None or MARK in (sl or {}).get("command", ""):
        data["statusLine"] = {"type": "command", "command": status_cmd()}
        print("statusLine: 설정됨")
    else:
        print("statusLine: 기존 설정이 있어 건드리지 않음. 모드를 표시하려면 기존 스크립트에서\n"
              f"  {status_cmd()}\n  의 출력을 이어 붙이세요 (stdin 으로 상태표시줄 JSON 전달).")
    save(settings, data, dry)
    print(f"hooks: {settings} 에 병합됨" + (" (dry-run)" if dry else ""))

    sdir = cdir / "skills"
    for name in SKILLS:
        link, target = sdir / name, REPO / "skills" / name
        if link.is_symlink() and Path(os.readlink(link)) == target:
            print(f"skill /{name}: 이미 설치됨")
        elif link.exists() or link.is_symlink():
            print(f"skill /{name}: {link} 가 이미 있어 건너뜀 (직접 정리 후 재실행)")
        else:
            if not dry:
                sdir.mkdir(parents=True, exist_ok=True)
                link.symlink_to(target)
            print(f"skill /{name}: 링크 생성")


def uninstall(cdir: Path, dry: bool, purge: bool) -> None:
    settings = cdir / "settings.json"
    if settings.exists():
        data = load(settings)
        strip_ours(data)
        if MARK in (data.get("statusLine") or {}).get("command", ""):
            del data["statusLine"]
        save(settings, data, dry)
        print("settings.json: 하네스 항목 제거")
    for name in SKILLS:
        link = cdir / "skills" / name
        if link.is_symlink() and Path(os.readlink(link)) == REPO / "skills" / name:
            if not dry:
                link.unlink()
            print(f"skill /{name}: 링크 제거")
    state = cdir / "harness"
    if purge and state.exists():
        if not dry:
            shutil.rmtree(state)
        print(f"{state}: 삭제")


def status(cdir: Path) -> None:
    data = load(cdir / "settings.json")
    for event in ("SessionStart", "UserPromptSubmit", "PreToolUse"):
        ok = any(is_ours(g) for g in data.get("hooks", {}).get(event, []))
        print(f"hook {event}: {'OK' if ok else '없음'}")
    print("statusLine:", "OK" if MARK in (data.get("statusLine") or {}).get("command", "") else "하네스 아님/없음")
    for name in SKILLS:
        link = cdir / "skills" / name
        ok = link.is_symlink() and Path(os.readlink(link)) == REPO / "skills" / name
        print(f"skill /{name}: {'OK' if ok else '없음'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--claude-dir", help="기본: $CLAUDE_CONFIG_DIR 또는 ~/.claude")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--purge", action="store_true", help="uninstall 시 상태 파일도 삭제")
    a = ap.parse_args()
    cdir = claude_dir(a.claude_dir)
    if a.action == "install":
        install(cdir, a.dry_run)
    elif a.action == "uninstall":
        uninstall(cdir, a.dry_run, a.purge)
    else:
        status(cdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
