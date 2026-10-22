"""Thin wrapper around the bundled `vrfkit.exe` binary."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "vrfkit.exe"


class VrfKitError(RuntimeError):
    pass


def binary_path() -> Path:
    return BIN


def available() -> bool:
    return BIN.exists()


PHASES: list[tuple[str, str]] = [
    ("reading", "Reading replay"),
    ("ReplayData", "Parsing network stream"),
    ("Checkpoint", "Parsing checkpoints"),
    ("fields.parquet", "Writing fields"),
    ("movement.parquet", "Writing movement"),
    ("Type overlay", "Finalising type overlay"),
]


def _phase_for(line: str) -> str | None:
    for token, label in PHASES:
        if token in line:
            return label
    return None


def export(
    replay_path: Path,
    out_dir: Path,
    checkpoints: bool = True,
    on_line: Callable[[str], None] | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> str:
    """Run `vrfkit export`. Raises VrfKitError on failure."""
    if not available():
        raise VrfKitError(f"vrfkit binary not found at {BIN}")
    if out_dir.exists() and any(out_dir.iterdir()):
        raise VrfKitError(f"export dir not empty: {out_dir}")
    out_dir.parent.mkdir(parents=True, exist_ok=True)

    cmd = [str(BIN), "export", str(replay_path), "--out", str(out_dir)]
    if checkpoints:
        cmd.append("--checkpoints")

    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=creationflags,
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        tail.append(line)
        if len(tail) > 200:
            tail.pop(0)
        if on_line:
            on_line(line)
        if on_phase:
            phase = _phase_for(line)
            if phase:
                on_phase(phase)
    code = proc.wait()
    if code != 0:
        # vrfkit can exit non-zero after a full successful write (e.g. stats
        # quirks); verify the manifest actually landed before failing.
        if not (out_dir / "manifest.json").exists():
            raise VrfKitError(
                "vrfkit export failed (exit %s)\n%s" % (code, "\n".join(tail[-40:]))
            )
    return "\n".join(tail[-40:])


def inspect(replay_path: Path) -> dict:
    if not available():
        raise VrfKitError(f"vrfkit binary not found at {BIN}")
    proc = subprocess.run(
        [str(BIN), "inspect", str(replay_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise VrfKitError(proc.stderr or proc.stdout or "inspect failed")
    return {"raw": proc.stdout}


def run_in_thread(fn: Callable[..., None], *args, **kwargs) -> threading.Thread:
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t
