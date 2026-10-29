#!/usr/bin/env python3
"""Export the canonical config JSON Schema (aliases/camelCase) for editors and CI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from mini_agent.config import AppConfig  # noqa: E402


def main() -> None:
    schema = AppConfig.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://mini-agent.local/config.schema.json"
    schema["title"] = "Mini Agent configuration"
    target = ROOT / "config" / "config.schema.json"
    target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
