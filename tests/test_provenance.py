"""Release-provenance checks for the local Nager.Date v0.3.0 RC.

These tests intentionally stay hermetic: they inspect tracked release metadata,
runtime package metadata, the frozen golden surface, and git history/tags.
The process-level JSON-RPC regressions live in ``test_stateless_era.py``.
"""

import ast
import hashlib
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
# The v0.3.0 release commit, pinned by SHA. Local release tags are deliberately not
# kept: a tag that must name HEAD is a release freeze, not a provenance record, and it
# fails on the first ordinary commit after a release. The release is therefore pinned
# here, and proven by ancestry (see test_release_and_rollback_history_are_reachable).
RELEASE_COMMIT = "b537b3243766d88c8227908c3af3762c735496fc"
RELEASE_SUBJECT = "nager-date-mcp: require release tag at verified HEAD"
ROLLBACK_COMMIT = "477a444f6a6f8fd738091da59f9bf202e467a2bd"


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


# P6 criterion 3: the worldwide description changes deliberately and NOTHING
# else in tools/list moves. The hashes below are canonical-JSON SHA-256 values
# of each tool in the frozen P6 baseline capture
# (results/p6-baseline/golden/nager-date.tools.json in the pilot workspace).
P6_BASELINE_TOOL_SHA256 = {
    "get_holidays": "8fc09e45b089deed4040278256564be25328f8ca8b697d6b73fccc5b9a6a2986",
    "get_next_holidays": "d6de333f1c907ab22cbe6c50f8755b6d1ed9696e9c5c75740e0280f1af8ab88d",
    "get_long_weekends": "499a28ee0c3e996917d81775c9cf67ccfd1f071cc09fd0c2c22605870ae9993f",
    "is_today_holiday": "a2435b844a73416a14b6679e0dad6e2dae35f7eca1ce866ab440e403ef9bb0d5",
    "list_countries": "2804f2e005ef47dfbab31a6167c318c0e5ba62341e74c621038e5e91ea9b17a0",
    "get_country_info": "84091a4fd25623bdb362bfc9e498161a66e110aeefb3ce0d659790d265f1094c",
    "get_worldwide_holidays": "85bf00907a442bf454724cd1ec2d5fe43f4537f1eee50e559a5d4d7a3c638481",
}

OLD_WORLDWIDE_DESCRIPTION = (
    "Get all public holidays happening today worldwide.\n\n"
    "Returns a list of holiday objects from all countries that have\n"
    "a public holiday today, each enriched with day_of_week."
)
NEW_WORLDWIDE_DESCRIPTION = (
    "Get upcoming public holidays worldwide.\n\n"
    "Returns a list of holiday objects from all countries with upcoming\n"
    "public holidays, each enriched with day_of_week."
)


def _canonical_tool_sha256(tool: dict) -> str:
    canonical = json.dumps(
        tool, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_worldwide_description_is_the_only_tools_list_delta_from_p6_baseline():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    by_name = {tool["name"]: tool for tool in golden}
    assert list(by_name) == list(P6_BASELINE_TOOL_SHA256)

    for name, baseline_hash in P6_BASELINE_TOOL_SHA256.items():
        if name == "get_worldwide_holidays":
            continue
        assert _canonical_tool_sha256(by_name[name]) == baseline_hash, name

    worldwide = by_name["get_worldwide_holidays"]
    assert worldwide["description"] == NEW_WORLDWIDE_DESCRIPTION
    assert "today" not in worldwide["description"].lower()
    reverted = dict(worldwide, description=OLD_WORLDWIDE_DESCRIPTION)
    assert (
        _canonical_tool_sha256(reverted)
        == P6_BASELINE_TOOL_SHA256["get_worldwide_holidays"]
    )
    assert (
        _canonical_tool_sha256(worldwide)
        != P6_BASELINE_TOOL_SHA256["get_worldwide_holidays"]
    )


def test_worldwide_runtime_description_and_handler_docstring_are_upcoming():
    by_name = {tool["name"]: tool for tool in server.TOOLS}
    assert by_name["get_worldwide_holidays"]["description"] == (
        NEW_WORLDWIDE_DESCRIPTION
    )
    docstring = server.get_worldwide_holidays.__doc__ or ""
    assert "upcoming" in docstring.lower()
    assert "happening today worldwide" not in docstring.lower()


def test_release_and_rollback_history_are_reachable():
    head = git("rev-parse", "HEAD")
    assert git("merge-base", "--is-ancestor", ROLLBACK_TAG, head) == ""
    # The recorded release must still exist, still be reachable from the tip, and must
    # descend from the rollback anchor. This proves the release/rollback relationship
    # without freezing the tip at the release, so later commits cannot break it.
    assert git("cat-file", "-t", RELEASE_COMMIT) == "commit"
    assert git("merge-base", "--is-ancestor", RELEASE_COMMIT, head) == ""
    assert git("merge-base", "--is-ancestor", ROLLBACK_TAG, RELEASE_COMMIT) == ""
    assert git("show", "-s", "--format=%s", RELEASE_COMMIT) == RELEASE_SUBJECT
    assert int(git("rev-list", "--count", f"{ROLLBACK_TAG}..HEAD")) > 0
    assert git("show", "-s", "--format=%s", ROLLBACK_TAG) == (
        "fix: migrate to standalone fastmcp (mcp SDK 2.x dropped mcp.server.fastmcp)"
    )


def test_local_release_tags_are_lightweight_and_ancestry_ordered():
    tags = git("for-each-ref", "--format=%(refname:short) %(objecttype)", "refs/tags")
    rows = dict(line.split() for line in tags.splitlines())
    assert rows[ROLLBACK_TAG] == "commit"
    # No v0.3.0 release tag is kept: a release tag that must name HEAD fails on the
    # first commit after release. The release is pinned by SHA above instead. Asserting
    # the tag is absent keeps the decision enforced, not merely documented.
    assert "v0.3.0" not in rows
    assert git("rev-parse", ROLLBACK_TAG) == ROLLBACK_COMMIT
