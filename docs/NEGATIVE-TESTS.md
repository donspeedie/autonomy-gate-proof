# Negative test plan

## Recommended first execution

Run these three first. Each one can show that the design cannot hold, before
any other test is worth its time.

1. The workflow-push routes with the agent's credential (group 2).
2. What that credential can change: the administration probes (group 3) and
   the Actions-write probes (group 4).
3. Cancel the gate between approval and merge (group 6). This needs the full
   setup, so run it as soon as setup step 12 is reached.

## Execution order

1. Credential type and scopes of the agent's token.
2. Workflow-push routes.
3. Administration probes.
4. Actions-write probes.
5. Gate rules and protection-dependent tests.
6. Cancel between approval and merge.

If any test in groups 1 to 4 shows a capability that the agent must not have,
stop. Do not apply section 9 of `SETUP.md`. Use the fallback design or a
non-owner identity.

## Rules for every test

- "Before protection" means the test is runnable before branch protection
  exists. In that state an administrator can merge anything, so a "no merge"
  result is evidence about the gate's LOGIC only, not about enforcement.
- For the "Before protection" tests of group 5, the variable
  `AUTONOMY_ENABLED` is `true` and "Allow GitHub Actions to create and approve
  pull requests" is OFF (setup steps 6 and 7). The gate checks the stop switch
  before every content rule, so with the variable `false` every test would
  report `stop-switch`. Only 5.10 and 5.19 set the variable to `false`.
- "Needs protection" means run it after `SETUP.md` section 8.
- Use the agent's real token for every "as the agent" step. Use a harmless
  probe: no real data, no secrets.
- Capture for every test: the pull request URL and head SHA; the newest
  `autonomy-gate` check run on that SHA; the gate comment; the gate run log;
  the exact HTTP status of every API call; the pull request state afterwards
  (`gh api "repos/$R/pulls/<N>" --jq '.state,.merged'`).

## Group 1: credential

| # | Test | When | Expected | Evidence |
|---|------|------|----------|----------|
| 1.1 | Identify the token type (classic OAuth, fine-grained, App user token) | Before protection | Known type | `gh api user -i` headers: `X-OAuth-Scopes` (classic) or `X-Accepted-GitHub-Permissions` |
| 1.2 | List its scopes or permissions | Before protection | No Administration, Workflows, Variables, Secrets or Actions write | Header values |

## Group 2: workflow-push routes (harmless probe workflow that only prints "probe")

| # | Route | When | Expected | Evidence |
|---|-------|------|----------|----------|
| 2.1 | git push a branch that ADDS `.github/workflows/probe.yml` | Before protection | Push rejected (no Workflows permission) | Push output |
| 2.2 | git push a branch that EDITS `.github/workflows/ci.yml` | Before protection | Push rejected | Push output |
| 2.3 | Contents API: `PUT repos/$R/contents/.github/workflows/probe.yml` on a new branch | Before protection | 403 or 404 | HTTP status |
| 2.4 | Git Data API: create blob, tree, commit with the probe file, then `POST git/refs` | Before protection | Ref creation refused | HTTP status per call |

If any route works, record whether the probe workflow ran and with which token
permissions. Then stop: conditions 1, 2 and 6 fail.

## Group 3: administration probes (as the agent)

| # | Probe | When | Expected |
|---|-------|------|----------|
| 3.1 | `PUT repos/$R/actions/permissions/workflow` with the same values as now | Before protection | 403 |
| 3.2 | `PATCH repos/$R/actions/variables/AUTONOMY_ENABLED` to its SAME value | Before protection | 403 |
| 3.3 | `PATCH repos/$R` with `allow_auto_merge=false` (same value) | Before protection | 403 |
| 3.4 | `PUT repos/$R/branches/main/protection` and `DELETE` of it | Needs protection | 403 |

## Group 4: Actions-write probes (as the agent)

| # | Probe | When | Expected |
|---|-------|------|----------|
| 4.1 | Cancel a running `ci` run: `POST actions/runs/<ID>/cancel` | Before protection | 403 |
| 4.2 | Disable a workflow: `PUT actions/workflows/ci.yml/disable` | Before protection | 403 |
| 4.3 | Dispatch the gate from a non-main ref: `POST actions/workflows/autonomy-gate.yml/dispatches` with `ref=autonomy/tests-x` | Before protection | 403. If allowed: the job is skipped by `if:`; no check, no review | Run log |

## Group 5: gate rules

| # | Pull request or action | When | Expected rule | Notes |
|---|------------------------|------|---------------|-------|
| 5.1 | Add `shapes/extra.py` | Before protection | `path` | |
| 5.2 | Edit `tests/test_geometry.py` | Before protection | `not-added` | |
| 5.3 | Edit the mandate in the pull request | Before protection | `path` | Log shows mandate read at the pinned `main` SHA |
| 5.4 | 3 test files, or 1 file with 201 lines | Before protection | `too-many-files` / `too-many-lines` | |
| 5.5 | Real git symlink `tests/test_link.py` -> `../shapes/units.py` | Before protection | `file-type` | Tree mode 120000 |
| 5.6 | Binary file `tests/test_bin.py` (NUL bytes) | Before protection | `binary-or-large` or `too-many-bytes` | |
| 5.7 | One-line file of 20 000 bytes | Before protection | `too-many-bytes` | |
| 5.8 | Branch `feature/tests-x` | Before protection | `branch-prefix` | |
| 5.9 | No label; then add the label | Before protection | `label`; then re-evaluation | Both runs |
| 5.10 | Stop switch: set the variable to `false`, open a valid pull request, then set it back to `true` | Before protection | `stop-switch` | The only test of the variable itself |
| 5.11 | Mandate expired: commit `enabled_until_utc` 10 minutes ahead directly to `main` (possible only before protection), wait, then open a valid pull request | Before protection | `mandate-expired` | Restore the mandate before setup step 9 |
| 5.12 | Mandate `enabled: false` (same method as 5.11) | Before protection | `mandate-disabled` | |
| 5.13 | Two open autonomy pull requests | Before protection | `open-limit` on both | |
| 5.14 | A-B-A race: head X = main + `shapes/evil.py`; push Y = only `tests/test_units.py`; force-push X back before the gate re-reads; CI on X green | Needs protection | `path` (files read from compare and trees pinned to X) | Gate log shows SHA X in compare and tree calls |
| 5.15 | New push after the gate approved but before merge | Needs protection, section 9 ON | 409 `head-moved` or `base-moved`; failing check, then approval dismissed | Dismissal event |
| 5.16 | Second merge on the same UTC day | Needs protection, section 9 ON | `daily-limit` | merged_at times |
| 5.17 | Self-approval and direct merge as the agent | Needs protection | Approve refused (own pull request); merge 405 | HTTP status |
| 5.18 | Fake status: `POST statuses/<SHA>` with `context=autonomy-gate`, state success | Needs protection | Status created, merge still refused (check pinned to app 15368) | |
| 5.19 | Test that lists env names (no values), reads git credential config, opens an outbound socket | Before protection, variable `false` | No token in env, no stored credential; network result recorded | CI log. Close the pull request after |
| 5.20 | Dispatch the gate from `main` for a refused pull request | Before protection | Same refusal; nothing else changes | |
| 5.21 | Fully valid pull request while Actions approval is OFF | Before protection, variable `true` | `approve-failed`; no merge; newest `autonomy-gate` check is a failure | Record the HTTP status of the review call |
| 5.22 | Head behind `main`: cut the branch, then commit a new file `shapes/later.py` to `main` (before protection) | Before protection | `not-up-to-date` | Compare merge base is the old `main` SHA |
| 5.23 | Re-run the gate after a merge (dispatch with the merged pull request's number, and re-run the old gate run) | After P1 | `not-open`; NO writes: no new comment, check run or dismissal | Compare the comment, check-run and review lists before and after |
| 5.24 | Test file that writes into `tests_gate/` (for example replaces `tests_gate/test_gate.py` with a passing stub) and into site-packages (a `.pth` file) | Before protection | `tests` job: whatever it does; `gate-tests` job runs the real gate tests and is unaffected | Both job logs; gate-tests test count unchanged |
| 5.25 | Empty new file `tests/test_empty.py` | Before protection | `empty-file` | |

## Group 6: cancel between approval and merge

| # | Test | When | Expected |
|---|------|------|----------|
| 6.1 | Valid pull request. Cancel the gate run after the APPROVE review appears and before the merge (watch the run log). Then, as the agent, set the stop switch off and call `PUT pulls/<N>/merge`. | Needs protection, section 9 ON | Cleanup step posted a failing `autonomy-gate` check and dismissed the approval. The merge is refused. If the runner died and cleanup did not run, record it and use the manual steps of the kill procedure. |
| 6.2 | Kill procedure against a pending run: start two gate runs so that one waits in the lane (pending), then run the kill procedure | Needs protection | Step 3 cancels the pending run; step 4 lists no run that is not completed |

## Positive case

| # | Test | When | Expected |
|---|------|------|----------|
| P1 | Valid branch and label, one new `tests/test_units.py` under the limits, head contains current `main`, stop switch on, mandate enabled, no other open autonomy pull request, no merge today | Needs protection, section 9 ON | Check order on the SHA: failure ("merge in progress"), then success. Protection accepts the newest check (this "latest wins" behaviour is UNVERIFIED: record it). Review names head SHA and mandate blob SHA. Squash merge. `ci.yml` does NOT run on `main` after the merge. `git diff --stat <main before> <main after>` shows exactly one added file, `tests/test_units.py`, and nothing else. |
