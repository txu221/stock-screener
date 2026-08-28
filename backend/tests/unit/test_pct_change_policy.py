from __future__ import annotations

import ast
from pathlib import Path


LEGACY_BREADTH_ADAPTERS = (
    "services/breadth_calculator_service.py",
    "services/breadth_attribution_service.py",
    "services/static_breadth_eligibility.py",
    "services/static_breadth_section_builder.py",
)

LEGACY_BREADTH_TOKENS = (
    "pct_change(periods=21",
    "pct_change(periods=63",
    "MOVER_THRESHOLD_PCT",
    "MINIMUM_BREADTH_OBSERVATIONS",
    "exact-ohlc-70-v1",
)


def test_backend_app_pct_change_calls_specify_fill_method():
    backend_app = Path(__file__).resolve().parents[2] / "app"
    offenders: list[str] = []

    for path in sorted(backend_app.rglob("*.py")):
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "pct_change":
                continue
            if any(keyword.arg == "fill_method" for keyword in node.keywords):
                continue
            line = lines[node.lineno - 1].strip()
            offenders.append(
                f"{path.relative_to(backend_app.parent)}:{node.lineno}:{line}"
            )

    assert offenders == []


def test_breadth_adapters_do_not_reimplement_legacy_formulas():
    backend_app = Path(__file__).resolve().parents[2] / "app"
    offenders: list[str] = []

    for relative_path in LEGACY_BREADTH_ADAPTERS:
        source = (backend_app / relative_path).read_text()
        for token in LEGACY_BREADTH_TOKENS:
            if token in source:
                offenders.append(f"{relative_path}:{token}")

    assert offenders == []
