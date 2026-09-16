#!/usr/bin/env python3
"""Zero-runtime-deps gate (PLAN D5 / R5 enforcement): stdlib-only import scan.

Walks every ``*.py`` under the given root (default ``src/`` of the current
directory) with ``ast``, collects the ROOT module of every import statement,
and asserts each is a stdlib module (``sys.stdlib_module_names``, py3.10+) or
belongs to this package (``nager_date_mcp``). Any third-party root (httpx,
fastmcp, pydantic, ...) is an offender: prints them and exits non-zero.

Covers, explicitly:
- ``import a.b.c`` / ``import a.b as x``  -> root ``a``
- ``from a.b import c`` and relative imports (``from . import x`` /
  ``from ..y import z`` never leave the package, so their root is the package
  itself, never a third-party dep)
- ``__init__.py`` and every nested package file (rglob ``*.py``)
- imports inside functions/try-blocks (ast walks them all)

Companion gate (the other half of the zero-deps proof, per R5 owner ruling --
no memory measurement anywhere): ``pip list`` inside the rebuilt v2 runtime
venv must show ONLY pip, setuptools, nager-date-mcp.

Usage: python3 tools/zero_deps_check.py [SRC_DIR]
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE = "nager_date_mcp"  # this project's own import root


def import_roots(source: str) -> list[str]:
    """Root module names of every import in *source*.

    Relative imports (``from . import x``, ``from ..client import X``) are
    counted as PACKAGE: by construction they resolve inside the scanned tree,
    so they can never introduce a third-party dependency.
    """
    tree = ast.parse(source)
    roots: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.append(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # Relative import: by construction it stays inside this
                # package tree (a level beyond the package root is a Syntax-
                # level bug, not a dependency). Its root is PACKAGE.
                roots.append(PACKAGE)
            elif node.module:
                roots.append(node.module.split(".", 1)[0])
    return roots


def main(argv: list[str]) -> int:
    src = Path(argv[1]) if len(argv) > 1 else Path("src")
    if not src.is_dir():
        print(f"zero_deps_check: no such source dir: {src}", file=sys.stderr)
        return 2

    stdlib = sys.stdlib_module_names
    offenders: list[str] = []
    files = sorted(src.rglob("*.py"))
    for path in files:
        rel = path.relative_to(src)
        try:
            tree_roots = import_roots(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            print(f"zero_deps_check: cannot parse {path}: {e}", file=sys.stderr)
            return 2
        for root in tree_roots:
            if root in stdlib or root == PACKAGE:
                continue
            offenders.append(f"{rel}: {root}")

    if offenders:
        print("zero_deps_check: NON-STDLIB IMPORTS FOUND:", file=sys.stderr)
        for o in sorted(set(offenders)):
            print(f"  {o}", file=sys.stderr)
        return 1

    print(f"stdlib-only OK ({len(files)} files scanned under {src})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
