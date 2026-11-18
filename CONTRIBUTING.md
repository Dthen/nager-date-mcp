# Contributing

Use Python 3.11+ (`tomllib` is required by the tests), Git and venv/pip.
Use a full clone: provenance tests need historical commits.

## Setup

```sh
git clone https://github.com/Dthen/nager-date-mcp.git
cd nager-date-mcp
python3.11 -m venv .venv-tests
.venv-tests/bin/python -m pip install --editable '.[dev]'
python3.11 -m venv .venv-runtime
.venv-runtime/bin/python -m pip install --no-deps --editable .
git show-ref --verify --quiet refs/tags/pre-migration/20260914 || \
  git tag pre-migration/20260914 477a444f6a6f8fd738091da59f9bf202e467a2bd
```

The tag is a local test anchor; **do not push it**. If its commit is missing,
fetch full history. Existing checkouts can skip cloning.

## Tests

```sh
TZ=UTC PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  PROD_PY="$PWD/.venv-runtime/bin/python" \
  .venv-tests/bin/python -m pytest tests/ -q -p no:cacheprovider
```

Keep the test and runtime interpreters separate. `PROD_PY` must import this
checkout. Tests fake HTTP and run offline; installing dependencies may need
network access. Replace `tests/` with a test file for a focused run.

## Wheel

```sh
.venv-tests/bin/python -m pip install build
.venv-tests/bin/python -m build --wheel
python3.11 -m venv .venv-wheel
.venv-wheel/bin/python -m pip install --no-deps --no-index dist/nager_date_mcp-0.3.0-py3-none-any.whl
.venv-wheel/bin/nager-date --version
.venv-wheel/bin/nager-date --help
```

Rebuild after README changes: it is embedded in package metadata. Building may
download tools; installing the wheel as above is offline. Do not run the
checkout-provenance suite against the wheel environment.

Keep application logic shared between CLI and MCP. Update tests and docs for
behavior changes.
