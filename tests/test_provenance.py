"""Release-provenance checks for the local Nager.Date v0.3.0 RC.

These tests intentionally stay hermetic: they inspect tracked release metadata,
runtime package metadata, the frozen golden surface, and git history/tags.
The process-level JSON-RPC regressions live in ``test_stateless_era.py``.
"""

import ast
import importlib.util
import json
import pathlib
import subprocess
import sys
import tomllib

import nager_date_mcp
from conftest import GOLDEN
from nager_date_mcp import client, server, transport


ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION = "0.3.0"
ROLLBACK_TAG = "pre-migration/20260914"
RELEASE_TAG = "v0.3.0"


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True, stderr=subprocess.PIPE
    ).strip()


def test_version_metadata_is_coherent():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == VERSION
    assert nager_date_mcp.__version__ == VERSION
    assert server.SERVER_INFO["version"] == VERSION
    assert transport.USER_AGENT == f"nager-date-mcp/{VERSION}"


def test_runtime_has_no_declared_dependencies():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []
    assert pyproject["build-system"]["requires"] == ["setuptools>=68"]


def test_runtime_package_is_installed_from_this_checkout():
    spec = importlib.util.find_spec("nager_date_mcp")
    assert spec is not None and spec.origin is not None
    assert pathlib.Path(spec.origin).resolve().is_relative_to(ROOT)
    assert client.__file__ is not None
    assert pathlib.Path(client.__file__).resolve().is_relative_to(ROOT)


def test_runtime_source_imports_only_stdlib_and_package():
    stdlib = sys.stdlib_module_names
    offenders: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".", 1)[0]]
            else:
                continue
            offenders.extend(
                f"{path.relative_to(ROOT)}: {root}"
                for root in roots
                if root not in stdlib and root != "nager_date_mcp"
            )
    assert offenders == []


def test_single_transport_seam_and_five_second_budget():
    network_calls = [
        path
        for path in (ROOT / "src").rglob("*.py")
        if any(
            token in path.read_text(encoding="utf-8")
            for token in ("urlopen(", "HTTPSConnection(", "HTTPConnection(")
        )
    ]
    assert network_calls == [ROOT / "src/nager_date_mcp/transport.py"]
    assert transport.DEFAULT_TIMEOUT == 5.0
    assert transport.TRANSPORT_ERRORS == (
        TimeoutError,
        transport.socket.timeout,
        transport.http.client.HTTPException,
        ConnectionResetError,
        OSError,
    )


def test_frozen_golden_has_seven_tools_without_runtime_output_schemas():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(golden) == 7
    assert {tool["name"] for tool in golden} == {
        tool["name"] for tool in server.TOOLS
    }
    assert len(server.TOOLS) == 7
    assert all("outputSchema" not in tool for tool in server.TOOLS)


def test_release_and_rollback_history_are_reachable():
    head = git("rev-parse", "HEAD")
    assert git("merge-base", "--is-ancestor", ROLLBACK_TAG, head) == ""
    assert git("merge-base", "--is-ancestor", RELEASE_TAG, head) == ""
    assert int(git("rev-list", "--count", f"{ROLLBACK_TAG}..HEAD")) > 0
    assert int(git("rev-list", "--count", f"{RELEASE_TAG}..HEAD")) > 0
    assert git("show", "-s", "--format=%s", ROLLBACK_TAG) == (
        "fix: migrate to standalone fastmcp (mcp SDK 2.x dropped mcp.server.fastmcp)"
    )


def test_local_release_tags_are_lightweight_and_ancestry_ordered():
    tags = git("for-each-ref", "--format=%(refname:short) %(objecttype)", "refs/tags")
    rows = dict(line.split() for line in tags.splitlines())
    assert rows[ROLLBACK_TAG] == "commit"
    assert rows[RELEASE_TAG] == "commit"
    assert git("rev-parse", ROLLBACK_TAG) == (
        "477a444f6a6f8fd738091da59f9bf202e467a2bd"
    )
