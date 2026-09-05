#!/usr/bin/env python3
"""Remove post-Terraform Observability / Evaluations / Dashboard resources.

Uses runtime_agent/langgraph uninstaller helpers. Run before `terraform destroy`.
Ignores missing resources.
"""

from __future__ import annotations

import json
import os
import sys

_TERRAFORM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.abspath(os.path.join(_TERRAFORM_DIR, ".."))
_LANGGRAPH_DIR = os.path.join(_REPO_ROOT, "runtime_agent", "langgraph")
_APP_CONFIG = os.path.join(_REPO_ROOT, "application", "config.json")
_RUNTIME_CONFIG = os.path.join(_LANGGRAPH_DIR, "config.json")


def _load_config() -> dict:
    for path in (_RUNTIME_CONFIG, _APP_CONFIG):
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return {}


def main() -> int:
    config = _load_config()
    if not config:
        print("No config.json found; nothing to clean up.")
        return 0

    print(f"Project: {config.get('projectName')}")
    print(f"Region:  {config.get('region')}")

    if _LANGGRAPH_DIR not in sys.path:
        sys.path.insert(0, _LANGGRAPH_DIR)

    from uninstaller import (  # noqa: WPS433
        delete_cloudwatch_dashboards,
        delete_online_evaluation,
    )

    steps = [
        ("Online evaluation", delete_online_evaluation),
        ("CloudWatch dashboards", delete_cloudwatch_dashboards),
    ]
    for name, fn in steps:
        print(f"\n>>> {name}")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            print(f"  warning: {name} cleanup failed: {exc}")

    print("\nObservability cleanup finished (missing resources ignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
