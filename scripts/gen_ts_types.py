"""Generate frontend TS types from the kernel event schema.

Pipeline: axor_core.kernel dataclasses -> pydantic TypeAdapter -> JSON Schema
-> ts (json-schema-to-typescript, invoked from pnpm). The kernel stays
stdlib-only; validation and schema generation live here on the platform side.
Run from repo root: uv run scripts/gen_ts_types.py
"""
from __future__ import annotations

import json
from pathlib import Path

from axor_core.kernel.events import Event, Fact
from axor_core.kernel.state import DesiredState
from pydantic import TypeAdapter

OUT = Path(__file__).parent.parent / "frontend" / "src" / "generated"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for model in (Event, Fact, DesiredState):
        schema = TypeAdapter(model).json_schema()
        out = OUT / f"{model.__name__}.schema.json"
        out.write_text(json.dumps(schema, indent=2) + "\n")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
