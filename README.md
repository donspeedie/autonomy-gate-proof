# autonomy-gate-proof

This repository exists to prove a merge gate by execution. Everything in it is
synthetic: the code, the tests, the names and the data. It holds no business,
financial or personal data and no secrets.

A scheduled cloud coding agent will open pull requests that add test files for
the untested modules in `shapes/`. GitHub alone decides whether each pull
request merges. The agent must not be able to defeat the rules for its own work.

## Layout

| Path | Purpose |
|------|---------|
| `shapes/` | Synthetic pure-function package. Six modules. |
| `tests/` | Package tests. `geometry` and `intervals` have tests. `units`, `slug`, `roman` and `lru` have none (the agent's work pool). |
| `.github/autonomy/mandate.json` | The mandate. The gate reads it from `main` only. |
| `.github/autonomy/gate.py` | The gate rules. Pure functions and a thin `main()`. |
| `.github/workflows/ci.yml` | Untrusted runner. Read-only token. Runs pytest. |
| `.github/workflows/autonomy-gate.yml` | Trusted gate. Runs from `main`. Writes only with the gate app token from a `main`-only environment. Posts the `autonomy-gate` check and merges. |
| `tests_gate/` | Unit tests for the gate with a fake API client. |
| `docs/SETUP.md` | Repository settings, step order and kill procedure. |
| `docs/GATE-APP-SETUP.md` | Owner steps for the dedicated gate GitHub App and its environment. |
| `docs/NEGATIVE-TESTS.md` | Hand-made pull requests that must be refused. |

## How the gate works (ten steps)

1. `ci.yml` runs the tests of the pull request with a read-only token and no secrets.
2. When `ci.yml` completes, `autonomy-gate.yml` runs from the `main` copy of the workflow.
3. It checks out `main` only, never the pull request, and runs `gate.py`.
4. It reads the head SHA (X) once and refuses a fork, a base other than `main`, or `AUTONOMY_ENABLED` not `true`.
5. It reads the mandate at the pinned `main` SHA and refuses if `enabled` is false or the date is past `enabled_until_utc`.
6. From the compare and git trees pinned to X: only new regular files matching `tests/test_*.py`, within file, line and byte limits.
7. It counts open and merged autonomy pull requests from GitHub data, not from the agent.
8. It requires a successful `ci.yml` run for X, linked to this pull request with base `main`, with both jobs `tests` and `gate-tests` (separate runners) green. X must contain the current `main`.
9. It re-reads head, label, branch, `main` and the mandate, sets a successful `autonomy-gate` check on X with the gate app token and merges with `sha` = X. It submits no review: the gate app's check is the approval.
10. On any refusal, or if the merge is not confirmed, it sets a failing gate-app check. A merged or closed pull request gets no writes.

A green CI run proves nothing about agent-written test code: a test can exit 0
early, read the read-only job token and reach the network. The gate trusts
only the run's conclusion for the pinned SHA. The agent's credential CAN push
workflow files, so the gate's write power lives in a dedicated GitHub App whose
key only a `main` run can read. The design still depends on the agent having no
administration write; see `docs/SETUP.md`.

## Run the tests locally

```
python -m pytest -q             # package tests (tests/)
python -m pytest -q tests_gate  # gate tests
```
