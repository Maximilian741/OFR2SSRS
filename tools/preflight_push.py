"""Pre-push safety check — run this ONE command before every git push:

    python tools/preflight_push.py

It answers one question: "is it safe to push?" by checking:
  1. The full test suite passes.
  2. No .env (or other env file) is tracked by git.
  3. No API keys / obvious secrets in any file git would publish.
  4. Nothing from your PRIVATE watchlist appears in publishable files.
     (Optional file `.canary_local` in the repo root, one term per line,
      gitignored — put client names, agency terms, report names there.
      The list itself never ships; that's the whole point.)

Exit code 0 + "SAFE TO PUSH" means push. Anything else: fix first.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Windows consoles/pipes often default to cp1252 -- never let an encoding
# error kill the verdict line.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent

# Generic secret shapes that must never ship, regardless of project.
SECRET_PATTERNS = [
    (r"sk-ant-api[0-9a-zA-Z_-]{8,}", "Anthropic API key"),
    (r"sk-[a-zA-Z0-9]{40,}", "API secret key"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key"),
    (r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----", "private key"),
]

SKIP_SUFFIXES = {".png", ".gif", ".jpg", ".jpeg", ".ico", ".pdf", ".zip",
                 ".dll", ".exe", ".woff", ".woff2"}


def _git(*args) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT)
    return r.stdout or ""


def main() -> int:
    problems = []

    # 1) Tests.
    print("[1/4] running the test suite ...")
    t = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = (t.stdout or "").strip().splitlines()
    print("      " + (tail[-1] if tail else "(no output)"))
    if t.returncode != 0:
        problems.append("test suite FAILED — fix tests before pushing")

    # 2) Tracked env files.
    print("[2/4] checking no env file is tracked ...")
    tracked_env = [f for f in _git("ls-files").splitlines()
                   if f.endswith(".env") and not f.endswith(".env.example")]
    if tracked_env:
        problems.append(f"env file(s) TRACKED by git: {tracked_env} "
                        f"(run: git rm --cached <file>)")

    # 3+4) Scan everything git would publish: tracked + untracked-not-ignored.
    print("[3/4] scanning publishable files for secrets ...")
    files = set(_git("ls-files").splitlines())
    files |= set(_git("ls-files", "--others", "--exclude-standard").splitlines())
    watch = []
    canary = ROOT / ".canary_local"
    if canary.exists():
        watch = [w.strip() for w in
                 canary.read_text(encoding="utf-8", errors="replace").splitlines()
                 if w.strip() and not w.startswith("#")]
        print(f"[4/4] scanning for {len(watch)} private watchlist term(s) ...")
    else:
        print("[4/4] no .canary_local watchlist found — skipping "
              "(create one with your private terms, one per line)")

    for rel in sorted(files):
        p = ROOT / rel
        if not p.is_file() or p.suffix.lower() in SKIP_SUFFIXES:
            continue
        if rel.endswith(".env"):
            continue  # already reported in step 2 if tracked
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pat, label in SECRET_PATTERNS:
            for m in re.finditer(pat, text):
                ln = text.count("\n", 0, m.start()) + 1
                problems.append(f"{label} in {rel}:{ln}")
        for term in watch:
            for m in re.finditer(re.escape(term), text, re.IGNORECASE):
                ln = text.count("\n", 0, m.start()) + 1
                problems.append(f"watchlist term '{term}' in {rel}:{ln}")

    print()
    if problems:
        print("DO NOT PUSH - fix these first:")
        for pr in problems[:40]:
            print("  [X]", pr)
        if len(problems) > 40:
            print(f"  ... and {len(problems) - 40} more")
        return 1
    print("SAFE TO PUSH  (tests green, no secrets, no watchlist terms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
