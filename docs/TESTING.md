# Testing, and why the full suite does not run on the laptop

The development machine is an 8 GB MacBook. Running the whole suite on it has
repeatedly driven the machine into swap and ended in an OOM kill (`exit 137`) or a
hard freeze, which reads like a flaky test and is not one. This file says what to
run where, so that stops being rediscovered.

## The short version

| What you want | Where | Command |
|---|---|---|
| Fast feedback while editing | laptop | `uv run pytest tests/test_<thing>.py` |
| "Did I break anything obvious" | laptop | `uv run pytest -q -x` |
| The real gate, everything, heavy set included | **CI** | `gh workflow run CI --ref <branch>` |
| Lint and types | laptop | `uv run ruff check && uv run mypy` |

Lint and typecheck are cheap and should stay local. The full pytest run should not.

## What actually exhausts the memory

Measured on this machine, 2026-09-20:

- `tests/test_retrieval_index.py` with the heavy set **deselected**: 74 MB peak RSS
- the same file with `-m ""` (heavy **included**): 213 MB peak RSS

213 MB is not by itself fatal. The problem is the sum, in one process:

- ~800 tests in a single pytest process, with module-scoped fixtures that hold
  their corpora for the life of the run
- the `heavy` scale tests holding a 10,000-object corpus, each object carrying a
  768-dimension embedding plus a detached copy inside the store
- a local Postgres alongside it
- the dev server, the editor, and a browser

At 8 GB with swap already around half consumed at idle, that lands in swap
thrashing rather than a clean failure. `vm_stat` on this machine has shown ~695,000
pageouts.

**It is not Docker.** The Docker daemon is not running, and the local Postgres is a
Homebrew-native cluster on port 5433. Stopping Docker changes nothing here.

## The `heavy` marker

`pyproject.toml` sets `addopts = "-q -m 'not heavy'"`, so the scale tests are
deselected **by default locally**. Leave that alone. The failure mode to avoid is
typing `-m ""` on the laptop, which is what clears the guard.

CI runs `-m ""` and therefore does cover them.

## Running the full suite in CI, on any branch

CI triggers on pull requests and on pushes to `main`. Neither of those fires for a
working branch with no PR open yet, which used to mean the only way to get a full
run was locally — the exact thing that takes the machine down. `workflow_dispatch`
closes that gap:

```sh
gh workflow run CI --ref launch/clerk-accounts-and-ui
gh run watch
```

It includes the heavy set by default. To skip it:

```sh
gh workflow run CI --ref <branch> -f heavy=false
```

Opening a **draft PR** early is the other way: every push then gets a full cloud run
without asking for one.

## If you do need Postgres locally

The Postgres suite **skips silently** when nothing is reachable on 5433, so a green
local run with ~67 skips has tested no SQL at all. Bringing one up is in the project
memory notes; the trap worth repeating here is that `LC_ALL=C` — required or the
Homebrew postmaster will not start — also makes `initdb` create the cluster as
`SQL_ASCII`, which cannot store the en-dashes in coletar's own content. Create the
database as UTF8 explicitly:

```sh
psql -h localhost -p 5433 -U coletar -d postgres \
  -c "CREATE DATABASE coletar ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
```

The failure without this is not at startup. Migrations apply, the server boots, and
the first non-ASCII write dies with `UntranslatableCharacter` from `_insert_event`,
which looks like a JSON bug.

## Paid-provider tests

Gated on `COLETAR_RUN_PAID_EXTRACTION_TESTS`, `OPENAI_API_KEY` and
`COLETAR_TEST_OLLAMA_URL`. CI never sets them, so the gate is the absence of a
secret rather than a flag anyone can flip by accident. They are the 3 skips in an
otherwise-complete run.
