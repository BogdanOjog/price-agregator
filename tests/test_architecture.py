"""Enforces the layer rules: domain <- config <- ingest/storage <- services <- cli/ui; imports only flow downwards."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "pisaprices"

# layer -> internal layers it may import (besides itself)
ALLOWED = {
    "domain": set(),
    "config": {"domain"},
    "ingest": {"domain", "config"},
    "storage": {"domain", "config"},
    "services": {"domain", "config", "ingest", "storage"},
    "cli": {"services"},
    "ui": {"services"},
}


def _layer_of(path: Path) -> str:
    if path.is_relative_to(ROOT / "ui"):
        return "ui"
    rel = path.relative_to(PACKAGE)
    return rel.parts[0].removesuffix(".py")


def _internal_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    layers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # relative imports stay inside the current package
                continue
            names = [node.module or ""]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] == "pisaprices" and len(parts) > 1:
                layers.add(parts[1])
    return layers


def _modules() -> list[Path]:
    files = [p for p in PACKAGE.rglob("*.py") if p.name != "__init__.py" or p.parent != PACKAGE]
    files += list((ROOT / "ui").rglob("*.py"))
    return files


@pytest.mark.parametrize("path", _modules(), ids=lambda p: str(p.relative_to(ROOT)))
def test_imports_respect_layers(path: Path) -> None:
    layer = _layer_of(path)
    assert layer in ALLOWED, f"{path} is outside any known layer"
    forbidden = _internal_imports(path) - ALLOWED[layer] - {layer}
    assert not forbidden, f"{layer} must not import {sorted(forbidden)}"


def test_layer_check_detects_violation(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("from pisaprices.storage import orm\nimport pisaprices.services.plan\n")
    assert _internal_imports(bad) == {"storage", "services"}
