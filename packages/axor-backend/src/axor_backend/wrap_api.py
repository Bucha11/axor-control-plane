"""Wrap engine API (/v1/wrap) — real code scan for the Config Builder.

POST /v1/wrap/scan       {files: [{path, content}]} → detected tools + effect
                         guesses. Files are written to a throwaway temp dir and
                         statically scanned by axor-wrap (stdlib ``ast``, the
                         uploaded code is never imported or executed).
POST /v1/wrap/manifests  classified tools → tool-manifest/v1 files, the
                         compiled governance YAML, and a wrap.json-style
                         sidecar (same artifacts as ``axor-wrap manifest``).

The engine is the optional ``axor-backend[wrap]`` extra (the ``axor-wrap``
package): imports are lazy and both routes answer 501 honestly when it is
missing. Scope: ``ingest`` is enough — this is analysis of operator-supplied
code, not an operational plane command (see auth._WRITE_POLICY).
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/v1/wrap")

# Upload ceiling for one scan request (sum of file contents, UTF-8 bytes).
MAX_SCAN_BYTES = 2 * 1024 * 1024
EFFECT_CLASSES = ("READ", "WRITE", "EXPORT", "EXEC")

_NOT_INSTALLED = (
    "wrap engine not installed on the backend — the /v1/wrap routes need the "
    "axor-wrap package (the axor-backend[wrap] extra). Install it and restart."
)


def _engine() -> ModuleType:
    """Lazy import: axor-wrap is an optional extra; absent → honest 501."""
    try:
        import axor_wrap
    except ImportError as exc:
        raise HTTPException(501, _NOT_INSTALLED) from exc
    return axor_wrap


def _safe_relpath(raw: object) -> PurePosixPath:
    """Normalize an uploaded path; absolute paths and ``..`` escapes are 400."""
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(400, "each file needs a non-empty string `path`")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or raw.startswith(("/", "\\", "~"))
        or ":" in raw  # windows drive (C:\...) — no legitimate use in a relpath
        or any(part in ("..", "") for part in path.parts)
    ):
        raise HTTPException(400, f"path must be relative and stay inside the upload root: {raw!r}")
    if path.suffix != ".py":
        raise HTTPException(400, f"only .py files are scanned: {raw!r}")
    return path


def _validated_files(body: dict) -> list[tuple[PurePosixPath, str]]:
    files = body.get("files")
    if not isinstance(files, list) or not files:
        raise HTTPException(400, "body must be {files: [{path, content}, ...]} (non-empty)")
    total = 0
    entries: list[tuple[PurePosixPath, str]] = []
    for item in files:
        if not isinstance(item, dict):
            raise HTTPException(400, "each file must be an object {path, content}")
        rel = _safe_relpath(item.get("path"))
        content = item.get("content")
        if not isinstance(content, str):
            raise HTTPException(400, f"file {str(rel)!r} needs a string `content`")
        total += len(content.encode("utf-8"))
        if total > MAX_SCAN_BYTES:
            raise HTTPException(
                413, f"upload exceeds the scan limit of {MAX_SCAN_BYTES} bytes total"
            )
        entries.append((rel, content))
    return entries


def _scan_tree(engine: ModuleType, entries: list[tuple[PurePosixPath, str]]) -> list[dict]:
    """Write the upload into a temp dir, scan it, and shape the response rows.

    Runs in a worker thread (sync file I/O + CPU-bound ast parsing).
    """
    tools: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="axor-wrap-scan-") as tmp:
        root = Path(tmp).resolve()
        for rel, content in entries:
            dest = (root / rel).resolve()
            if not dest.is_relative_to(root):  # defense in depth after _safe_relpath
                raise HTTPException(400, f"path escapes the upload root: {str(rel)!r}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
        for tool in engine.scan_project(root):
            guess = engine.infer_effect(tool)
            tools.append({
                "id": tool.id,
                "source": tool.source,
                "description": tool.description,
                "args_schema": tool.args_schema,
                "framework": tool.framework,
                "schema_confidence": tool.schema_confidence,
                "guess": {
                    "default_class": guess.default_class,  # UNKNOWN survives — honest
                    "confidence": guess.confidence,
                    "reason": guess.reason,
                    "driving_args": list(guess.driving_args),
                    "untrusted_fields": list(guess.untrusted_fields),
                },
            })
    return tools


@router.post("/scan")
async def wrap_scan(body: dict) -> dict:
    """Scan uploaded .py sources for tools; classification stays a human step."""
    engine = _engine()
    entries = _validated_files(body)
    tools = await asyncio.to_thread(_scan_tree, engine, entries)
    return {"tools": tools}


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value]


@router.post("/manifests")
async def wrap_manifests(body: dict) -> dict:
    """Build tool-manifest/v1 files + governance YAML from classified tools."""
    engine = _engine()
    raw_tools = body.get("tools")
    if not isinstance(raw_tools, list) or not raw_tools:
        raise HTTPException(400, "body must be {tools: [...]} (non-empty)")

    manifests: list[dict[str, object]] = []
    sidecar: list[dict[str, object]] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            raise HTTPException(400, "each tool must be an object")
        tool_id = str(item.get("id") or "")
        if not tool_id:
            raise HTTPException(400, "each tool needs a non-empty `id`")
        effect = item.get("effect")
        if not isinstance(effect, dict):
            raise HTTPException(400, f"tool {tool_id!r} needs an `effect` object")
        default_class = str(effect.get("default_class") or "")
        if default_class not in EFFECT_CLASSES:
            raise HTTPException(
                400,
                f"tool {tool_id!r}: effect.default_class must be one of "
                f"{'|'.join(EFFECT_CLASSES)} — UNKNOWN/unclassified tools must be "
                "classified in the config builder first",
            )
        args_schema = item.get("args_schema")
        detected = engine.DetectedTool(
            id=tool_id,
            source=str(item.get("source") or ""),
            description=str(item.get("description") or ""),
            args_schema=dict(args_schema) if isinstance(args_schema, dict) else {"type": "object"},
            framework=str(item.get("framework") or "langchain"),
            schema_confidence=str(item.get("schema_confidence") or "low"),
        )
        guess = engine.EffectGuess(
            default_class=default_class,
            confidence="high",
            reason="operator-classified in the config builder",
            driving_args=tuple(_str_list(effect.get("driving_args"))),
            untrusted_fields=tuple(_str_list(effect.get("untrusted_fields"))),
        )
        manifest = engine.build_manifest(detected, guess)
        sensitive = _str_list(effect.get("sensitive_fields"))
        if sensitive:
            manifest["sensitive_fields"] = sensitive
        errors = engine.validate_manifest(manifest)
        if errors:
            raise HTTPException(
                400, f"tool {tool_id!r} does not compile to a valid manifest: {'; '.join(errors)}"
            )
        manifests.append(manifest)
        sidecar.append({
            "id": tool_id,
            "framework": detected.framework,
            "source": detected.source,
            "schema_confidence": detected.schema_confidence,
            "effect_guess": {
                "class": default_class,
                "confidence": guess.confidence,
                "reason": guess.reason,
            },
        })

    return {
        "manifests": manifests,
        "governance_yaml": engine.governance_yaml(manifests),
        # Same shape as the wrap.json sidecar `axor-wrap manifest` writes.
        "wrap": {
            "generated_by": f"axor-wrap {engine.__version__}",
            "manifest_schema": "tool-manifest/v1",
            "tools": sidecar,
        },
    }
