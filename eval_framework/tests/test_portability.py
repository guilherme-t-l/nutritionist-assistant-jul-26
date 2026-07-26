"""Enforce the portability constraint: no imports from agent/ or src/.

The framework must stay copy-pasteable into another project. The only
allowed seam to the host app is a Target Adapter (HTTP in this repo).
This test walks every .py file under eval_framework/ and fails if any
module imports `agent` or `src` (AST-based — comments/strings don't count).
"""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_TOP_LEVEL = frozenset({"agent", "src"})

# eval_framework/ — parent of tests/
FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]


def _top_level(module_name: str) -> str:
    return module_name.split(".", 1)[0]


def _scan_file(path: Path) -> list[str]:
    """Return human-readable violation strings for one file."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error while scanning ({exc})"]

    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _top_level(alias.name) in FORBIDDEN_TOP_LEVEL:
                    hits.append(
                        f"{path.relative_to(FRAMEWORK_ROOT)}:{node.lineno} "
                        f"import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            # Relative imports (from .x) have module=None or start with '.'
            # — those stay inside the package and are fine.
            if node.level and not node.module:
                continue
            if node.module and _top_level(node.module) in FORBIDDEN_TOP_LEVEL:
                hits.append(
                    f"{path.relative_to(FRAMEWORK_ROOT)}:{node.lineno} "
                    f"from {node.module} import ..."
                )
    return hits


def test_eval_framework_never_imports_host_app() -> None:
    violations: list[str] = []
    for path in sorted(FRAMEWORK_ROOT.rglob("*.py")):
        violations.extend(_scan_file(path))

    assert not violations, (
        "eval_framework/ must not import agent/ or src/. "
        "Talk to the host only through a Target Adapter.\n"
        + "\n".join(violations)
    )


def test_nutri_adapter_is_http_only() -> None:
    """Sanity: the project adapter module mentions HTTP endpoints, not agent imports."""
    adapter = (FRAMEWORK_ROOT / "adapters" / "nutri_http.py").read_text(encoding="utf-8")
    tree = ast.parse(adapter)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            # Re-check via the shared scanner for this one file.
            pass
    hits = _scan_file(FRAMEWORK_ROOT / "adapters" / "nutri_http.py")
    assert hits == []
    assert "/plan" in adapter and "/chat" in adapter
