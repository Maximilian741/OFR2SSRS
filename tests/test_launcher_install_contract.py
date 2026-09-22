"""The launchers START the app and never install; the docs must say so.

What a first-time user actually hit: the README promised "the launcher
installs dependencies", the launchers deliberately do not (a locked-down
work machine must never see implicit install activity), so a fresh clone
died on a raw ``ModuleNotFoundError: No module named 'lxml'`` from Flask's
import chain and the user had to guess at ``pip install -r
requirements.txt`` themselves. Three things are pinned here so it cannot
recur:

  * no launcher runs pip (the no-install rule is a hard constraint);
  * every launcher PROBES the imports first and, when one is missing,
    prints the exact one-time command instead of a traceback;
  * no document claims the launcher installs, and every quick-start names
    the one-time install step.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

LAUNCHERS = ["run.sh", "run.bat"]
QUICKSTARTS = ["README.md", "CONTRIBUTING.md", "docs/COWORKER_DEMO.md",
               "docs/RUN_GUIDE.md"]
INSTALL_CMD = "pip install -r requirements.txt"
PROBE_MODULES = ("flask", "lxml", "docx", "werkzeug")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("rel", LAUNCHERS)
def test_launcher_never_installs(rel):
    src = _read(rel)
    # Only lines that could EXECUTE count: comments and the echo lines that
    # print the one-time command to the user are not runs.
    live = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().lower().startswith(("#", "rem", "echo")))
    assert not re.search(r"pip\s+install", live), (
        f"{rel} runs pip install: launchers must only start the app")


@pytest.mark.parametrize("rel", LAUNCHERS)
def test_launcher_probes_imports_before_starting(rel):
    """A missing package must be named by the launcher, not by a traceback."""
    src = _read(rel)
    assert re.search(r'-c\s+"import\s+' + r",\s*".join(PROBE_MODULES) + '"', src), (
        f"{rel} does not probe the required imports before starting")
    assert INSTALL_CMD in src, (
        f"{rel} must print the exact one-time install command on a miss")
    assert "never installs" in src, (
        f"{rel} must tell the user the launcher itself never installs")


def test_probe_covers_every_hard_requirement():
    """The probe names the packages the app cannot import without; the
    requirements file is the source of truth for that list."""
    req = _read("requirements.txt").lower()
    for pkg in ("flask", "lxml", "python-docx", "werkzeug"):
        assert pkg in req, f"{pkg} disappeared from requirements.txt"


@pytest.mark.parametrize("rel", QUICKSTARTS)
def test_no_document_claims_the_launcher_installs(rel):
    text = _read(rel)
    assert not re.search(r"launcher\s+installs", text, re.IGNORECASE), (
        f"{rel} still promises that the launcher installs dependencies -- "
        f"it does not, and a first-time user followed that promise into a "
        f"ModuleNotFoundError")


@pytest.mark.parametrize("rel", QUICKSTARTS)
def test_every_quick_start_names_the_one_time_install(rel):
    assert INSTALL_CMD in _read(rel), (
        f"{rel} must show the one-time `{INSTALL_CMD}` step")


def test_readme_shows_the_error_and_its_fix_together():
    """The symptom the user sees and the command that fixes it, side by side."""
    text = _read("README.md")
    assert "ModuleNotFoundError" in text and INSTALL_CMD in text


# ---------------------------------------------------------------------------
# End to end: run each launcher with a dependency genuinely missing
# ---------------------------------------------------------------------------

def _run_launcher_with_missing_module(argv, tmp_path):
    """Shadow ``lxml`` with a module that fails to import and run the
    launcher. The probe must stop it BEFORE the app starts, printing the
    one-time command; ``exit != 0`` and no lingering server."""
    import os
    import subprocess
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "lxml.py").write_text('raise ImportError("simulated missing lxml")\n',
                                    encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(shadow)
    proc = subprocess.run(argv, cwd=str(ROOT), env=env, capture_output=True,
                          text=True, timeout=60)
    return proc


@pytest.mark.skipif(not (ROOT / "run.sh").exists(), reason="no run.sh")
def test_run_sh_names_the_fix_instead_of_a_traceback(tmp_path):
    import shutil
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    proc = _run_launcher_with_missing_module([bash, "run.sh"], tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert INSTALL_CMD in out and "never installs" in out, out
    assert "Traceback" not in out, "the raw traceback leaked: " + out[-400:]


@pytest.mark.skipif(sys.platform != "win32", reason="run.bat is Windows-only")
def test_run_bat_names_the_fix_instead_of_a_traceback(tmp_path):
    """Also proves run.bat PARSES: its .env loader once used a substring
    expression on a FOR variable, which batch rejects with 'The syntax of
    the command is incorrect.' on every machine that had a .env."""
    env_file = ROOT / ".env"
    made_env = False
    if not env_file.exists():
        # exercise the .env loader branch on machines without one
        env_file.write_text("# comment line\nO2S_SMOKE_VAR=1\n", encoding="utf-8")
        made_env = True
    try:
        proc = _run_launcher_with_missing_module(
            ["cmd", "/c", str(ROOT / "run.bat")], tmp_path)
    finally:
        if made_env:
            env_file.unlink()
    out = proc.stdout + proc.stderr
    assert "syntax of the command is incorrect" not in out.lower(), out
    assert proc.returncode != 0, out
    assert INSTALL_CMD in out and "never installs" in out, out
    # The FIRST line must print whole. Inside a parenthesised block cmd can
    # drop a caret escape, so "-^> SSRS" became a REDIRECTION: the line was
    # cut at the arrow and an empty file named "SSRS" appeared in the repo
    # root on every run (measured). No arrow, no stray file.
    assert "a required Python package is not installed" in out, out
    assert not (ROOT / "SSRS").exists(), \
        "run.bat created a stray file: an echo line is being parsed as a redirection"


def test_run_bat_echo_lines_carry_no_redirection_characters():
    """Static twin of the smoke above: an echo inside a parenthesised block
    must never contain ``^>`` / ``^<`` -- cmd may drop the caret."""
    for ln in _read("run.bat").splitlines():
        if ln.lstrip().lower().startswith("echo") and re.search(r"\^[<>]", ln):
            raise AssertionError("redirection character in a run.bat echo: " + ln)
