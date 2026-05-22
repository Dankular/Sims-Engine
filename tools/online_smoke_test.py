from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
import urllib.error


BASE = "http://127.0.0.1:8091"


def req(method: str, path: str, payload: dict | None = None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    r = urllib.request.Request(BASE + path, method=method, data=data, headers=headers)
    with urllib.request.urlopen(r, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_ready(timeout: float = 60.0):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req("GET", "/state")
            return
        except Exception:
            time.sleep(1.0)
    raise RuntimeError("server did not become ready")


def main() -> int:
    proc = subprocess.Popen(
        [
            sys.executable,
            "server.py",
            "--host",
            "127.0.0.1",
            "--port",
            "8091",
            "--sims",
            "5",
            "--no-datasets",
        ],
    )
    try:
        wait_ready()
        state = req("GET", "/state")
        sims = [s["id"] for s in state.get("sims", [])[:3]]
        assert len(sims) == 3, "need 3 sims"

        a = req("POST", "/online/connect", {"username": "alice", "sim_id": sims[0]})
        b = req("POST", "/online/connect", {"username": "bob", "sim_id": sims[1]})
        c = req("POST", "/online/connect", {"username": "cara", "sim_id": sims[2]})

        req(
            "POST",
            "/online/action",
            {"token": a["token"], "command": "move", "args": {"room_id": "park"}},
        )
        req(
            "POST",
            "/online/action",
            {"token": b["token"], "command": "move", "args": {"room_id": "park"}},
        )
        req(
            "POST",
            "/online/action",
            {"token": c["token"], "command": "move", "args": {"room_id": "park"}},
        )

        req(
            "POST",
            "/online/action",
            {"token": a["token"], "command": "chat", "args": {"text": "hello room"}},
        )
        req(
            "POST",
            "/online/action",
            {"token": b["token"], "command": "chat", "args": {"text": "hey alice"}},
        )

        room = req("GET", "/online/room/park")
        events = room.get("events", [])
        assert any("hello room" in str(e.get("text", "")) for e in events), (
            "chat missing"
        )

        req(
            "POST",
            "/online/action",
            {
                "token": a["token"],
                "command": "interact",
                "args": {"target_sim_id": sims[1], "action": "chat"},
            },
        )

        req(
            "POST",
            "/online/action",
            {
                "token": a["token"],
                "command": "move",
                "args": {"room_id": "shopping_center"},
            },
        )
        stock = req("GET", "/items/lot/shopping_center").get("items", [])
        assert stock, "shopping center stock missing"
        a_profile = req("GET", f"/profile/{sims[0]}")
        funds = float(a_profile.get("simoleons", 0.0))
        affordable = [
            it for it in stock if float(it.get("current_price", 10**9)) <= funds
        ]
        assert affordable, "no affordable item for sim"
        oid = -1
        buy = {"ok": False}
        for item in sorted(
            affordable, key=lambda x: float(x.get("current_price", 0.0))
        )[:12]:
            oid = int(item["id"])
            buy = req(
                "POST",
                "/online/action",
                {
                    "token": a["token"],
                    "command": "buy",
                    "args": {
                        "lot_id": "shopping_center",
                        "object_id": oid,
                        "qty": 1,
                    },
                },
            )
            if buy.get("ok") is True:
                break
        assert buy.get("ok") is True, f"buy failed after retries: {buy}"

        # Try to feed/use item (may fail if bought item not usable; that's acceptable if command path works)
        used = req(
            "POST",
            "/online/action",
            {"token": a["token"], "command": "use", "args": {"object_id": oid}},
        )

        req("POST", "/online/sessions/ttl", {"ttl_seconds": 1.0})
        req("POST", "/online/disconnect", {"token": c["token"]})
        time.sleep(3.0)
        stats = req("GET", "/online/sessions")

        print(
            json.dumps(
                {
                    "ok": True,
                    "connected_users": [a["player_id"], b["player_id"], c["player_id"]],
                    "room_event_count": len(events),
                    "buy": buy,
                    "use": used,
                    "session_stats": stats,
                },
                indent=2,
            )
        )
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
