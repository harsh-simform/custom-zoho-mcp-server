"""Architecture rule (T05): the renderer is the only code that knows TOON. Command modules and
front-ends hand a Result to render.py and never import `zoho_projects_axi.core.toon` themselves.

The rule is checked statically over every module in the package. `imports_toon` is exercised against
synthetic sources first so the check cannot pass vacuously.
"""
import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "zoho_projects_axi"
ALLOWED = {"zoho_projects_axi.core.render"}
TOON = "zoho_projects_axi.core.toon"


def imports_toon(source: str, module: str, is_package: bool = False) -> bool:
    """True if `source` (the module named `module`) imports zoho_projects_axi.core.toon in any spelling."""
    parts = module.split(".")
    base = parts if is_package else parts[:-1]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(a.name == TOON or a.name.startswith(TOON + ".") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            anchor = base[: len(base) - (node.level - 1)] if node.level else []
            target = ".".join(anchor + ([node.module] if node.module else []))
            if target == TOON or target.startswith(TOON + "."):
                return True
            if target == "zoho_projects_axi.core" and any(a.name == "toon" for a in node.names):
                return True
    return False


@pytest.mark.parametrize("source, module", [
    ("import zoho_projects_axi.core.toon", "zoho_projects_axi.cli.main"),
    ("import zoho_projects_axi.core.toon as t", "zoho_projects_axi.cli.main"),
    ("from zoho_projects_axi.core import toon", "zoho_projects_axi.commands.task"),
    ("from zoho_projects_axi.core.toon import encode", "zoho_projects_axi.commands.task"),
    ("from ..core import toon", "zoho_projects_axi.commands.task"),
    ("from ..core.toon import encode", "zoho_projects_axi.commands.task"),
    ("from .toon import encode", "zoho_projects_axi.core.commands"),
    ("from . import toon", "zoho_projects_axi.core.commands"),
    ("from .core import toon", "zoho_projects_axi.mcp_server"),
    ("def f():\n    from zoho_projects_axi.core import toon\n", "zoho_projects_axi.cli.main"),
])
def test_checker_flags_every_way_of_importing_toon(source, module):
    assert imports_toon(source, module)


@pytest.mark.parametrize("source, module", [
    ("from zoho_projects_axi.core import render", "zoho_projects_axi.commands.task"),
    ("from ..core.render import render", "zoho_projects_axi.commands.task"),
    ("from . import render", "zoho_projects_axi.core.commands"),
    ("import json", "zoho_projects_axi.cli.main"),
    ("from .toonish import x", "zoho_projects_axi.core.commands"),
])
def test_checker_leaves_unrelated_imports_alone(source, module):
    assert not imports_toon(source, module)


def test_only_the_render_module_imports_toon():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE.parent).with_suffix("")
        is_package = rel.name == "__init__"
        module = ".".join(rel.parts[:-1] if is_package else rel.parts)
        if module in ALLOWED:
            continue
        if imports_toon(path.read_text(encoding="utf-8"), module, is_package):
            offenders.append(module)
    assert offenders == [], f"import zoho_projects_axi.core.render instead of toon in: {offenders}"


def test_render_module_does_import_toon_so_the_allowlist_is_real():
    path = PACKAGE / "core" / "render.py"
    assert imports_toon(path.read_text(encoding="utf-8"), "zoho_projects_axi.core.render")
