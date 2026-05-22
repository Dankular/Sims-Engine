from __future__ import annotations

import json
from pathlib import Path


CHECKS = {
    "context_sensors": Path("world/context_sensors.py"),
    "action_intelligence": Path("core/action_intelligence.py"),
    "action_prereqs": Path("core/action_prereqs.py"),
    "consequence_writer": Path("core/consequences.py"),
    "scheduler_wiring": Path("engine/scheduler.py"),
    "sim_desire_loops": Path("core/sim.py"),
    "config_flags": Path("config.py"),
    "tests_added": Path("tests/test_action_intelligence.py"),
}


def main() -> None:
    report = {
        "implemented": {},
        "missing": [],
    }
    for key, path in CHECKS.items():
        ok = path.exists()
        report["implemented"][key] = {
            "present": ok,
            "path": str(path),
        }
        if not ok:
            report["missing"].append(key)

    report["coverage"] = {
        "total": len(CHECKS),
        "present": sum(1 for v in report["implemented"].values() if v["present"]),
    }
    report["coverage"]["percent"] = round(
        100.0 * report["coverage"]["present"] / max(1, report["coverage"]["total"]),
        1,
    )

    out = Path("datasets/.sim_cache/gap_check_actions.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
