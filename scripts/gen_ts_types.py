"""Generate frontend TS types from axor-kernel Pydantic models.

Pipeline: Pydantic -> JSON Schema -> ts (arch open q resolved by whichever
generator is picked; this script is the single entry point either way).
Run from repo root: uv run scripts/gen_ts_types.py
"""
from __future__ import annotations

import json
from pathlib import Path

from axor_kernel.events import Event, Fact

OUT = Path(__file__).parent.parent / "frontend" / "src" / "generated"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for model in (Event, Fact):
        schema = model.model_json_schema()
        (OUT / f"{model.__name__}.schema.json").write_text(json.dumps(schema, indent=2))
    # ts emission: json-schema-to-typescript invoked from pnpm side


if __name__ == "__main__":
    main()
