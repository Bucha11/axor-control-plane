"""Contract: kernel modules stay pure. This test travels with the code into
axor-core (as a guard on axor_core.kernel) — the boundary is a test, not a package."""
import ast
import pathlib

FORBIDDEN = {"httpx", "fastapi", "starlette", "kuzu", "asyncpg", "sqlalchemy",
             "requests", "aiohttp", "socket", "anyio", "uvicorn"}
SRC = pathlib.Path(__file__).parent.parent / "src" / "axor_kernel"


def test_kernel_imports_no_io() -> None:
    for f in SRC.glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            bad = FORBIDDEN.intersection(names)
            assert not bad, f"{f.name}: forbidden import {bad}"
