# Contributing

## Set up a checkout

Use Git and Python 3.11 with `venv` and pip available. The application supports
Python >=3.10; this contributor recipe uses 3.11 because the test suite reads
package metadata with the standard-library `tomllib` module.

Use a full clone, not a shallow clone: the provenance tests check historical
commits as well as current package metadata.

```bash
git clone https://github.com/Dthen/nager-date-mcp.git
cd nager-date-mcp
python3.11 -m venv .venv-tests
.venv-tests/bin/python -m pip install --editable '.[dev]'
python3.11 -m venv .venv-runtime
.venv-runtime/bin/python -m pip install --no-deps --editable .
```

If you already followed the README quickstart, start at `python3.11 -m venv
.venv-tests` from that checkout; you do not need to clone again or replace the
quickstart's `.venv`. Installing the dev
extra makes the test environment editable and adds pytest. The runtime
interpreter is separate so subprocess tests cannot accidentally exercise the
test runner's installation. Neither environment changes your system Python.
Installation can download build/test tools. The application itself has no
third-party runtime dependencies.

The provenance tests also expect a local historical rollback reference. If
it is absent, create it at the recorded commit:

```bash
git show-ref --verify --quiet refs/tags/pre-migration/20260914 || \
  git tag pre-migration/20260914 477a444f6a6f8fd738091da59f9bf202e467a2bd
```

This is a local test-history anchor, not a new release. Do not push this tag.
If the commit is unavailable, check that you have a full clone before running
the suite; for a shallow clone, fetch its history with `git fetch --unshallow
origin`.

## Run the tests

From the repository root:

```bash
TZ=UTC PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PROD_PY="$PWD/.venv-runtime/bin/python" \
  .venv-tests/bin/python -m pytest tests/ -q -p no:cacheprovider
```

`PROD_PY` must be the absolute path to the **other** interpreter, which must
import this checkout. Do not point it at `.venv-tests/bin/python`, use a system
interpreter as a fallback, or install pytest into the runtime environment.
A gitignored `.prod_py` file containing that absolute path can replace the
environment variable; an explicit `PROD_PY` takes precedence.

The tests fake HTTP at the transport boundary; normal test execution does not
need the live holiday API. Actual CLI queries do need network access.

For a focused run, replace `tests/` with a file or node ID, for example
`tests/test_cli.py`. Keep the same environment settings and interpreter roles.
The runtime and test environments, pytest caches and build outputs are ignored
by Git.

## Building a wheel

After setting up the test environment, run from the repository root:

```bash
.venv-tests/bin/python -m pip install build
.venv-tests/bin/python -m build --wheel
```

Build isolation may download the backend declared in `pyproject.toml`.
The current version produces `dist/nager_date_mcp-0.3.0-py3-none-any.whl`.
The README is embedded in wheel metadata, so rebuild after documentation
changes too; do not reuse an older wheel when checking current documentation.

To check the artifact in a third environment without dependency or index
access during installation:

```bash
python3.11 -m venv .venv-wheel
.venv-wheel/bin/python -m pip install --no-deps --no-index dist/nager_date_mcp-0.3.0-py3-none-any.whl
.venv-wheel/bin/nager-date --version
.venv-wheel/bin/nager-date --help
```

This is a regular, non-editable installation: it imports packaged files, not
`src/`. Source-suite provenance tests intentionally assert checkout imports,
so do not run that entire suite unchanged against the wheel environment.
Runtime commands can also be launched as `python -m nager_date_mcp.cli`; the
MCP entry point is `python -m nager_date_mcp.server`.

## Changes and bug reports

Keep fetching and application operations shared by the CLI and MCP adapters;
do not add a second HTTP path inside either interface. Preserve the distinction
between empty/false successes, warnings and failures. Update the relevant
behavior tests and documentation when changing a command or result contract.

For a bug report, include your Python version, installation method, exact
command, exit status, expected result and relevant output. Do not include
credentials or personal data. Use the repository's
[issue tracker](https://github.com/Dthen/nager-date-mcp/issues).
