#!/usr/bin/env python3
"""CashCannon helper for AI agents: install → configure → generate → print result block.

Exit codes: 0 success, 10 needs input (missing credentials), 1 generation failed, 2 bad usage.
Never prints secrets.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = "https://github.com/upsalesvideo/cashcannon.git"
HOME = Path(os.environ.get("CANNON_HOME", Path.home() / "cashcannon")).expanduser()
REQUIRED = ["GENOSAI_API_KEY"]
OPTIONAL = ["PEXELS_API_KEY", "PIXABAY_API_KEY"]


def sh(cmd: list[str], cwd: Path | None = None, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=capture)


def which(name: str) -> str | None:
    return shutil.which(name)


def needs_input(missing: list[str]) -> int:
    print("CANNON_NEEDS_INPUT")
    for m in missing:
        print(f"MISSING={m}")
    print("HINT=Get a Genosai key at https://genosai.io (API section). Export it and re-run the same command.")
    return 10


def ensure_project() -> None:
    if (HOME / "pyproject.toml").is_file():
        try:
            sh(["git", "-C", str(HOME), "pull", "--ff-only", "-q"], check=False)
        except Exception:
            pass
        return
    HOME.parent.mkdir(parents=True, exist_ok=True)
    print(f"[cannon] cloning CashCannon into {HOME}", file=sys.stderr)
    sh(["git", "clone", "--depth", "1", REPO, str(HOME)])


def ensure_env_file() -> list[str]:
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    env_path = HOME / ".env"
    existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    if missing and all(f"{k}=" in existing and not existing.split(f"{k}=", 1)[1].split("\n", 1)[0].strip() == "" for k in missing):
        missing = []  # already configured on disk
    if missing:
        return missing
    lines = []
    for k in REQUIRED + OPTIONAL:
        v = os.environ.get(k)
        if v:
            lines.append(f"{k}={v}")
    if lines:
        merged = {l.split("=", 1)[0]: l for l in existing.splitlines() if "=" in l and not l.startswith("#")}
        for l in lines:
            merged[l.split("=", 1)[0]] = l
        env_path.write_text("\n".join(merged.values()) + "\n", encoding="utf-8")
    return []


def venv_python() -> Path:
    venv = HOME / ".venv"
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if py.exists():
        return py
    if which("uv"):
        sh(["uv", "venv", "-q", str(venv)], cwd=HOME)
        sh(["uv", "pip", "install", "-q", "--python", str(py), "-e", "."], cwd=HOME)
    else:
        sh([sys.executable, "-m", "venv", str(venv)])
        sh([str(py), "-m", "pip", "install", "-q", "-e", "."], cwd=HOME)
    return py


def ensure_remotion() -> None:
    rem = HOME / "remotion"
    if not (rem / "node_modules").is_dir():
        if not which("npm"):
            print("CANNON_ERROR=node/npm not found — install Node.js 20+ (https://nodejs.org) and re-run")
            sys.exit(1)
        print("[cannon] installing Remotion dependencies (one-time)", file=sys.stderr)
        sh(["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=rem)


def run_make(py: Path, topic: str, extra: list[str], resume: str | None) -> int:
    cmd = [str(py), "-m", "cashcannon.cli"]
    if resume:
        cmd += ["resume", resume, "--json"]
    else:
        cmd += ["make", topic, "--yes", "--json", *extra]
    proc = subprocess.run(cmd, cwd=HOME, text=True, capture_output=True)
    sys.stderr.write(proc.stderr[-6000:])
    if proc.returncode != 0:
        print(f"CANNON_ERROR={(proc.stderr.strip().splitlines() or ['unknown error'])[-1][:500]}")
        return 1
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        print("CANNON_ERROR=could not parse cannon output")
        print(proc.stdout[-2000:])
        return 1
    return print_result(data)


def print_result(data: dict) -> int:
    task_dir = Path(data.get("task_dir", ""))
    res = data.get("result") or {}
    if data.get("status") != "succeeded" or not res.get("video"):
        print(f"CANNON_ERROR={data.get('error') or 'task did not succeed'} (task_id={data.get('task_id')})")
        return 1
    print("CANNON_RESULT")
    print(f"VIDEO_FILE={task_dir / res['video']}")
    if res.get("cover"):
        print(f"COVER_FILE={task_dir / res['cover']}")
    print(f"TASK_DIR={task_dir}")
    print(f"CREDITS_SPENT={data.get('cost_credits', 0)}")
    if res.get("title"):
        print(f"TITLE={res['title']}")
    return 0


def run_batch(py: Path, manifest: Path, extra: list[str]) -> int:
    cmd = [str(py), "-m", "cashcannon.cli", "batch", str(manifest.resolve()), "--yes", *extra]
    proc = subprocess.run(cmd, cwd=HOME, text=True, capture_output=True)
    sys.stderr.write(proc.stderr[-6000:])
    try:
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        print("CANNON_ERROR=could not parse batch output")
        return 1
    rc = 0
    for t in summary.get("tasks", []):
        if print_result(t) != 0:
            rc = 1
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="CashCannon agent helper")
    ap.add_argument("--topic", help="video topic / idea")
    ap.add_argument("--batch", help="JSONL/JSON manifest with tasks")
    ap.add_argument("--resume", help="task_id to resume")
    ap.add_argument("extra", nargs=argparse.REMAINDER, help="extra `cannon make` options after --")
    a = ap.parse_args()
    extra = [x for x in a.extra if x != "--"]
    if not (a.topic or a.batch or a.resume):
        ap.print_help()
        return 2
    for tool in ("git", "ffmpeg"):
        if not which(tool):
            print(f"CANNON_ERROR={tool} not found on PATH — install it and re-run")
            return 1
    ensure_project()
    missing = ensure_env_file()
    if missing:
        return needs_input(missing)
    py = venv_python()
    ensure_remotion()
    if a.batch:
        return run_batch(py, Path(a.batch), extra)
    return run_make(py, a.topic or "", extra, a.resume)


if __name__ == "__main__":
    sys.exit(main())
