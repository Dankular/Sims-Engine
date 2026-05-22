from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request


BASE = "http://127.0.0.1:8092"


def req(method: str, path: str, payload: dict | None = None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    r = urllib.request.Request(BASE + path, method=method, data=data, headers=headers)
    with urllib.request.urlopen(r, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_ready(timeout: float = 70.0):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req("GET", "/state")
            return
        except Exception:
            time.sleep(1.0)
    raise RuntimeError("server not ready")


def safe_action(token: str, command: str, args: dict):
    try:
        return req(
            "POST",
            "/online/action",
            {"token": token, "command": command, "args": args},
        )
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.read().decode("utf-8")}


async def ws_probe(tokens: list[str], seconds: float = 4.0) -> dict:
    """Optional websocket probe if websockets package exists."""
    try:
        import websockets  # type: ignore
    except Exception:
        return {"supported": False, "reason": "websockets package unavailable"}

    recv_counts = {t: 0 for t in tokens}

    async def _run(token: str):
        uri = f"ws://127.0.0.1:8092/online/ws?token={token}"
        async with websockets.connect(uri, ping_interval=None) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            end = time.time() + seconds
            while time.time() < end:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    _ = json.loads(msg)
                    recv_counts[token] += 1
                except Exception:
                    pass

    await asyncio.gather(*[_run(t) for t in tokens])
    return {"supported": True, "recv_counts": recv_counts}


def main() -> int:
    proc = subprocess.Popen(
        [
            sys.executable,
            "server.py",
            "--host",
            "127.0.0.1",
            "--port",
            "8092",
            "--sims",
            "6",
            "--no-datasets",
        ]
    )
    try:
        wait_ready()
        state = req("GET", "/state")
        sim_ids = [s["id"] for s in state.get("sims", [])[:4]]
        assert len(sim_ids) == 4, "need at least 4 sims"

        # sessions + claims
        users = {}
        for uname, sid in zip(["alice", "bob", "cara", "dave"], sim_ids):
            s = req("POST", "/online/connect", {"username": uname, "sim_id": sid})
            users[uname] = s

        # resume consistency
        r = req("POST", "/online/session/resume", {"username": "alice"})
        assert r["token"] == users["alice"]["token"], "resume token mismatch"

        # room movement + chat fan-in
        for u in users.values():
            req(
                "POST",
                "/online/action",
                {"token": u["token"], "command": "move", "args": {"room_id": "park"}},
            )
        req(
            "POST",
            "/online/action",
            {
                "token": users["alice"]["token"],
                "command": "chat",
                "args": {"text": "alpha"},
            },
        )
        req(
            "POST",
            "/online/action",
            {
                "token": users["bob"]["token"],
                "command": "chat",
                "args": {"text": "beta"},
            },
        )
        park = req("GET", "/online/room/park")
        assert len(park.get("events", [])) >= 2, "park chat events missing"

        # scene restrictions
        req(
            "POST",
            "/online/action",
            {
                "token": users["cara"]["token"],
                "command": "move",
                "args": {"room_id": "home"},
            },
        )
        disallowed = safe_action(
            users["cara"]["token"],
            "buy",
            {"lot_id": "shopping_center", "object_id": 1, "qty": 1},
        )
        assert disallowed.get("ok") is False and disallowed.get("status") == 403, (
            "home room restriction failed"
        )

        # economy interactions in shopping center
        req(
            "POST",
            "/online/action",
            {
                "token": users["alice"]["token"],
                "command": "move",
                "args": {"room_id": "shopping_center"},
            },
        )
        stock = req("GET", "/items/lot/shopping_center").get("items", [])
        profile = req("GET", f"/profile/{sim_ids[0]}")
        funds = float(profile.get("simoleons", 0.0))
        affordable = [
            it for it in stock if float(it.get("current_price", 10**9)) <= funds
        ]
        assert affordable, "no affordable stock"
        item = sorted(affordable, key=lambda x: float(x.get("current_price", 0.0)))[0]
        oid = int(item["id"])
        b = req(
            "POST",
            "/online/action",
            {
                "token": users["alice"]["token"],
                "command": "buy",
                "args": {"lot_id": "shopping_center", "object_id": oid, "qty": 1},
            },
        )
        assert b.get("ok") is True, "buy failed in shopping center"
        u = req(
            "POST",
            "/online/action",
            {
                "token": users["alice"]["token"],
                "command": "use",
                "args": {"object_id": oid},
            },
        )
        assert u.get("ok") is True, "use failed"

        # social interaction queue
        it = req(
            "POST",
            "/online/action",
            {
                "token": users["alice"]["token"],
                "command": "interact",
                "args": {"target_sim_id": sim_ids[1], "action": "chat"},
            },
        )
        assert it.get("ok") is True and it.get("queued") is True, (
            "interact queue failed"
        )

        # gift/trade path (best effort)
        gt = safe_action(
            users["alice"]["token"],
            "gift",
            {"target_sim_id": sim_ids[1]},
        )

        # TTL cleanup
        req("POST", "/online/sessions/ttl", {"ttl_seconds": 1.0})
        req("POST", "/online/disconnect", {"token": users["dave"]["token"]})
        time.sleep(3.0)
        sess_stats = req("GET", "/online/sessions")
        assert sess_stats.get("archived_count", 0) >= 1, "ttl cleanup not applied"

        # optional websocket probe
        ws_res = asyncio.run(
            ws_probe([users["alice"]["token"], users["bob"]["token"]], seconds=3.0)
        )

        print(
            json.dumps(
                {
                    "ok": True,
                    "users": {k: v["player_id"] for k, v in users.items()},
                    "park_events": len(park.get("events", [])),
                    "buy": b,
                    "use": u,
                    "gift_or_trade_result": gt,
                    "session_stats": sess_stats,
                    "ws_probe": ws_res,
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
