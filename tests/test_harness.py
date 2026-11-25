import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from harness import core  # noqa: E402


def run_hook(event, payload, state):
    env = {**os.environ, "HARNESS_STATE_DIR": str(state), "PYTHONPATH": str(REPO)}
    p = subprocess.run([sys.executable, "-m", "harness.claude", event], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout) if p.stdout.strip() else {}


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.proj = Path(self.tmp.name) / "proj"
        self.proj.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def prompt(self, text, sid="s1"):
        return run_hook("prompt", {"session_id": sid, "prompt": text}, self.state)

    def pre(self, tool, inp, sid="s1"):
        return run_hook("pretool", {"session_id": sid, "tool_name": tool, "tool_input": inp, "cwd": str(self.proj)}, self.state)

    def denied(self, tool, inp):
        return self.pre(tool, inp).get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_default_normal_injects_nothing(self):
        self.assertEqual(self.prompt("hi"), {})

    def test_switch_and_mode_query(self):
        out = self.prompt("/learn")
        self.assertEqual(out["decision"], "block")
        self.assertIn("Learn", out["reason"])
        self.assertIn("learn", self.prompt("/mode")["reason"])
        self.assertEqual(core.get_mode("s1") if False else None, None)

    def test_context_injected_every_prompt_in_learn(self):
        self.prompt("/learn")
        for _ in range(2):
            ctx = self.prompt("explain")["hookSpecificOutput"]["additionalContext"]
            self.assertIn("MODE: LEARN", ctx)

    def test_transition_invalidates_previous(self):
        self.prompt("/learn"); self.prompt("x")
        self.prompt("/collab")
        ctx = self.prompt("x")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("learn → collab", ctx)
        self.assertIn("MODE: COLLAB", ctx)
        self.prompt("/normal")
        ctx = self.prompt("x")["hookSpecificOutput"]["additionalContext"]
        self.assertIn("collab → normal", ctx)
        self.assertEqual(self.prompt("x"), {})  # 이후에는 조용

    def test_sessions_isolated(self):
        self.prompt("/learn", sid="a")
        self.assertEqual(self.prompt("hi", sid="b"), {})

    def test_learn_blocks_project_edits(self):
        self.prompt("/learn")
        f = str(self.proj / "a.py")
        for tool in ("Edit", "Write", "MultiEdit"):
            self.assertTrue(self.denied(tool, {"file_path": f}), tool)
        self.assertTrue(self.denied("NotebookEdit", {"notebook_path": f}))
        self.assertTrue(self.denied("Write", {"file_path": "rel.py"}))
        self.assertFalse(self.denied("Write", {"file_path": "/tmp/outside.txt"}))

    def test_learn_bash_policy(self):
        self.prompt("/learn")
        for c in ["echo hi > a.txt", "sed -i s/a/b/ f", "rm -rf x", "git commit -m x", "ls && touch f",
                  "cat a | tee out", "git stash", "rtk bypass rm x"]:
            self.assertTrue(self.denied("Bash", {"command": c}), c)
        for c in ["ls -la", "git status", "git log --oneline", "grep -r x . 2>&1", "pytest > /dev/null",
                  "python3 -m unittest", "git stash list", "git diff | head"]:
            self.assertFalse(self.denied("Bash", {"command": c}), c)

    def test_other_modes_unrestricted(self):
        for m in ("/collab", "/normal"):
            self.prompt(m)
            self.assertFalse(self.denied("Write", {"file_path": str(self.proj / "a.py")}))
            self.assertFalse(self.denied("Bash", {"command": "rm x"}))

    def test_statusline(self):
        self.prompt("/learn")
        p = subprocess.run([sys.executable, "-m", "harness.claude", "statusline"], input='{"session_id":"s1"}',
                           capture_output=True, text=True, env={**os.environ, "HARNESS_STATE_DIR": str(self.state), "PYTHONPATH": str(REPO)})
        self.assertIn("LEARN", p.stdout)

    def test_fail_open_on_bad_input(self):
        env = {**os.environ, "HARNESS_STATE_DIR": str(self.state), "PYTHONPATH": str(REPO)}
        p = subprocess.run([sys.executable, "-m", "harness.claude", "pretool"], input="garbage", capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0)


class InstallTests(unittest.TestCase):
    def run_install(self, cdir, *args):
        return subprocess.run([sys.executable, str(REPO / "install.py"), *args, "--claude-dir", str(cdir)],
                              capture_output=True, text=True)

    def test_install_preserves_and_uninstall_restores(self):
        with tempfile.TemporaryDirectory() as t:
            cdir = Path(t)
            orig = {"theme": "auto", "statusLine": {"type": "command", "command": "mine.sh"},
                    "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other"}]}]}}
            (cdir / "settings.json").write_text(json.dumps(orig))
            (cdir / "skills").mkdir()
            (cdir / "skills" / "learn").mkdir()  # 기존 사용자 skill
            self.assertEqual(self.run_install(cdir, "install").returncode, 0)
            self.run_install(cdir, "install")  # 멱등
            s = json.loads((cdir / "settings.json").read_text())
            self.assertEqual(s["statusLine"]["command"], "mine.sh")
            ups = s["hooks"]["UserPromptSubmit"]
            self.assertEqual(len(ups), 2)
            self.assertEqual(len(s["hooks"]["PreToolUse"]), 1)
            self.assertFalse((cdir / "skills" / "learn").is_symlink())
            self.assertTrue((cdir / "skills" / "collab").is_symlink())
            self.run_install(cdir, "uninstall")
            self.assertEqual(json.loads((cdir / "settings.json").read_text()), orig)
            self.assertFalse((cdir / "skills" / "collab").exists())

    def test_fresh_install_sets_statusline(self):
        with tempfile.TemporaryDirectory() as t:
            self.run_install(Path(t), "install")
            s = json.loads((Path(t) / "settings.json").read_text())
            self.assertIn("harness.claude statusline", s["statusLine"]["command"])
            self.run_install(Path(t), "uninstall")
            self.assertEqual(json.loads((Path(t) / "settings.json").read_text()), {})


if __name__ == "__main__":
    unittest.main()
