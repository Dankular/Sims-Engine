from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_script(script_rel: str, timeout_s: int = 1200) -> dict:
    script = ROOT / script_rel
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"{script_rel} failed (code={proc.returncode})\n"
            f"STDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
        )

    # Parse final JSON payload from stdout (boot logs may precede it)
    out = (proc.stdout or "").strip()
    start = out.rfind("{")
    while start > 0:
        try:
            payload = json.loads(out[start:])
            if isinstance(payload, dict):
                return payload
        except Exception:
            start = out.rfind("{", 0, start)
            continue
    raise AssertionError(f"No JSON payload found in stdout for {script_rel}\n{out}")


def test_online_smoke_suite() -> None:
    payload = _run_script("tools/online_smoke_test.py")
    assert payload.get("ok") is True
    assert payload.get("buy", {}).get("ok") is True
    assert payload.get("use", {}).get("ok") is True
    assert payload.get("session_stats", {}).get("archived_count", 0) >= 1


def test_online_extended_suite() -> None:
    payload = _run_script("tools/online_extended_test.py")
    assert payload.get("ok") is True
    assert payload.get("park_events", 0) >= 2
    assert payload.get("buy", {}).get("ok") is True
    assert payload.get("use", {}).get("ok") is True
    ws_probe = payload.get("ws_probe", {})
    if ws_probe.get("supported"):
        recv = ws_probe.get("recv_counts", {})
        assert all(int(v) >= 1 for v in recv.values())
