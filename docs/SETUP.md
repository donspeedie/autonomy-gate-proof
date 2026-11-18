# Repository setup

Apply these settings AFTER the first push to `main`, in the order given.
Nothing in this file has been applied. Treat every control as UNPROVEN until a
test in `NEGATIVE-TESTS.md` shows that it holds.

The commands use PowerShell syntax. Set the repository name first:

```powershell
$R = "donspeedie/autonomy-gate-proof"
```

## Read this first: the owner is the agent

The agent acts with the repository owner's identity, an administrator. Two
facts follow:

1. Repository settings do not bind an administrator whose token has the
   matching permission. Settings help only if the agent's token cannot change them.
2. With `enforce_admins` on and one required review, no human can merge a
   change to `main` (for example a mandate change) without lifting protection.
   The owner cannot approve the owner's own pull request. Lifting protection is
   the same administrative action that the agent could take. So a mandate change
   is slow by design. The fast stop is the variable plus disabling the workflow
   (see "Kill procedure").

## Step order

| Step | What | Section |
|------|------|---------|
| 1 | Default branch, merge settings | 1, 2 |
| 2 | Actions token read-only, Actions approval **OFF** | 3 |
| 3 | Allowed actions, variable `false`, label, no secrets | 4 to 7 |
| 4 | Run negative-test groups 1 to 4 | NEGATIVE-TESTS.md |
| 5 | Stop here if any of those tests fails. Use "Fallback design" or a non-owner identity. | |
| 6 | Set the variable to `true`. Actions approval stays **OFF**. | 5a |
| 7 | Run the "Before protection" tests of group 5 (including 5.11 and 5.12, which commit to `main` directly, 5.10 for the stop switch and 5.21 for `approve-failed`) | NEGATIVE-TESTS.md |
| 8 | Set the variable back to `false` | 5 |
| 9 | Branch protection | 8 |
| 10 | Run the "Needs protection" tests of groups 3 and 5 | NEGATIVE-TESTS.md |
| 11 | **LAST:** turn Actions approval ON, then set the variable to `true` | 9 |
| 12 | Run negative-test group 6 (cancel between approval and merge) and the positive case | NEGATIVE-TESTS.md |

Why the variable is `true` in step 6: the gate checks the stop switch before
every content rule. With the variable `false`, every logic test reports
`stop-switch` and proves nothing about its own rule. In steps 6 and 7 the
safety is the Actions approval setting: it is OFF, so the gate cannot approve.
A fully valid pull request then ends at `approve-failed` with no merge (test 5.21).

## 1. Default branch is `main`

```powershell
gh api -X PATCH "repos/$R" -f default_branch=main
```
Why: `workflow_run` and `pull_request_target` use the workflow file on the
default branch. The gate reads the mandate from `main`.

## 2. Merge settings: squash only, no auto-merge

```powershell
gh api -X PATCH "repos/$R" -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false -F allow_auto_merge=false -F delete_branch_on_merge=true
```
Why: the gate merges with `squash`. A queued auto-merge could complete after
the stop switch is turned off.

## 3. Actions: read-only default token, Actions approval OFF

```powershell
gh api -X PUT "repos/$R/actions/permissions/workflow" -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false
```
Why OFF for now: when this setting is ON, ANY workflow can approve with
`GITHUB_TOKEN`, including a workflow that a pull request adds. If the agent can
push workflow files, that is a self-approval path. Keep it OFF until negative
test groups 1 to 4 show that the agent cannot push workflow files, cannot change
settings and cannot write to Actions. Section 9 turns it on as the last step.

## 4. Actions: allow only GitHub-owned actions

```powershell
gh api -X PUT "repos/$R/actions/permissions" -F enabled=true -f allowed_actions=selected
gh api -X PUT "repos/$R/actions/permissions/selected-actions" -F github_owned_allowed=true -F verified_allowed=false
```
Why: both workflows use only `actions/checkout` and `actions/setup-python`, pinned by SHA.

## 5. Stop switch variable (start OFF)

```powershell
gh api -X POST "repos/$R/actions/variables" -f name=AUTONOMY_ENABLED -f value=false
```
Why: the gate reads `vars.AUTONOMY_ENABLED` once, at job start. Only the exact
string `true` lets it continue. The job token cannot read variables through the
API, so the gate cannot read it again before the merge. The mandate field
`enabled` is the second switch: the gate reads it again, with its blob SHA,
immediately before approval. The gate reads the mandate at the pinned `main`
SHA, not at the moving name `main`.

## 5a. Variable `true` for the logic tests (Actions approval still OFF)

Do this only at step 6 of the step order, and only if section 3 shows
`can_approve_pull_request_reviews` = `false`:

```powershell
gh api "repos/$R/actions/permissions/workflow" --jq .can_approve_pull_request_reviews
gh api -X PATCH "repos/$R/actions/variables/AUTONOMY_ENABLED" -f name=AUTONOMY_ENABLED -f value=true
```
The first command must print `false`. Set the variable back to `false` at step 8.

## 6. Label

```powershell
gh api -X POST "repos/$R/labels" -f name=autonomy -f color=ededed -f description="Opened by the autonomy agent"
```

## 7. No Actions secrets

```powershell
gh api "repos/$R/actions/secrets" --jq .total_count
```
The result must be 0. A `pull_request` workflow from a branch in this
repository can read repository secrets.

## 8. Branch protection on `main`

Save as `protection.json`:

```json
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      {"context": "tests", "app_id": 15368},
      {"context": "gate-tests", "app_id": 15368},
      {"context": "autonomy-gate", "app_id": 15368}
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1,
    "require_last_push_approval": true
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": false,
  "lock_branch": false
}
```

```powershell
gh api -X PUT "repos/$R/branches/main/protection" --input protection.json
```

Why, per field:
- `checks` with `app_id` 15368 (GitHub Actions): a user token can create a
  commit status with any name, but it cannot create a check run. Pinning the
  app stops a plain status from satisfying `autonomy-gate`. For this reason the
  gate writes a check run and needs `checks: write`. LIMIT: every workflow,
  including one that a pull request adds, writes as app 15368.
- `tests` and `gate-tests`: both jobs of `ci.yml` are required checks. The
  mandate lists both in `required_ci_jobs`, and the gate requires `gate-tests`
  even if a mandate leaves it out. `gate-tests` runs on its own runner (see
  "Other facts to know").
- `strict: true`: the head must contain the current `main`. Then the CI run
  for the head SHA tested the tree that the squash merge produces. The gate
  enforces the same rule itself (rule `not-up-to-date`: the merge base of
  `main` and the head must be the current `main` SHA), so the behaviour before
  protection matches the behaviour after. The gate also pins the `main` SHA
  and refuses if `main` moves during evaluation.
- One approval, `dismiss_stale_reviews`, `require_last_push_approval`: an
  approval is valid only for the head it saw. The gate approves with `commit_id`.
- `enforce_admins: true`: the owner (the agent identity) gets no bypass.
- No force pushes, no deletions, linear history.

## 9. LAST: turn Actions approval ON, then enable

Do this only after steps 1 to 10 of the step order pass. The variable must be
`false` (step 8) until Actions approval is ON.

```powershell
gh api -X PUT "repos/$R/actions/permissions/workflow" -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true
gh api -X PATCH "repos/$R/actions/variables/AUTONOMY_ENABLED" -f name=AUTONOMY_ENABLED -f value=true
```

## Kill procedure

Do these in order. Steps 1 to 4 are the fast path; they need no pull request.
A per-pull-request stop that needs no administrator rights: remove the
`autonomy` label. The gate re-checks the label immediately before approval.

```powershell
# 1. Stop switch off (the gate reads it at job start)
gh api -X PATCH "repos/$R/actions/variables/AUTONOMY_ENABLED" -f name=AUTONOMY_ENABLED -f value=false
# 2. Disable the gate workflow (no new runs start)
gh api -X PUT "repos/$R/actions/workflows/autonomy-gate.yml/disable"
# 3. Cancel EVERY gate run whose status is not completed (queued, in_progress, pending, waiting, requested)
gh run list -R $R --workflow autonomy-gate.yml --limit 200 --json databaseId,status | ConvertFrom-Json | Where-Object { $_.status -ne 'completed' } | ForEach-Object { gh api -X POST "repos/$R/actions/runs/$($_.databaseId)/cancel" }
# 4. List again. This must print nothing. If a run stays, repeat step 3; if it is stuck in cancelling, use force-cancel.
gh run list -R $R --workflow autonomy-gate.yml --limit 200 --json databaseId,status | ConvertFrom-Json | Where-Object { $_.status -ne 'completed' } | ForEach-Object { "$($_.databaseId) $($_.status)" }
gh api -X POST "repos/$R/actions/runs/<ID>/force-cancel"
```

Then do step 5 for every open pull request:

```powershell
# 5. Find gate approvals and gate checks on each open pull request
gh pr list -R $R --state open --json number,headRefOid | ConvertFrom-Json | ForEach-Object { "PR $($_.number) head $($_.headRefOid)" }
gh api "repos/$R/pulls/<N>/reviews" --jq '.[] | [.id, .user.login, .state] | @tsv'
gh api "repos/$R/commits/<HEAD_SHA>/check-runs?check_name=autonomy-gate" --jq '.check_runs[] | [.id, .conclusion, .completed_at] | @tsv'
```

**Step 6 is MANDATORY** if any open pull request has a `github-actions[bot]`
approval that is not dismissed, OR its newest `autonomy-gate` check run has
the conclusion `success`. In that state the owner identity (the agent) could
call the merge endpoint directly. Lock `main` first, then clean up:

```powershell
# 6. Lock main: edit protection.json to "lock_branch": true, then apply it
gh api -X PUT "repos/$R/branches/main/protection" --input protection.json
# 7. Dismiss each gate approval by hand
gh api -X PUT "repos/$R/pulls/<N>/reviews/<ID>/dismissals" -f message="kill procedure" -f event=DISMISS
# 8. Optional, slow: set "enabled": false in the mandate. This needs a pull request to main (see "Read this first").
```

Before branch protection exists (setup steps 1 to 8), step 6 creates the
protection. Use the full `protection.json` with `"lock_branch": true`.

The cleanup step of the gate posts the failing check and dismisses approvals
on cancel. It is best effort: if the runner dies, the cleanup step does not
run. Steps 5 to 7 are the manual replacement. Unlock `main` only after step 5
shows no gate approval and no newest `success` check on any open pull request.

## What the agent identity can still change (must be TESTED, not assumed)

| It can, if its token allows... | Through | Result |
|--------------------------------|---------|--------|
| Remove protection or `enforce_admins` | Branch protection API | The gate becomes advisory |
| Set `AUTONOMY_ENABLED=true` | Variables API | Stop switch defeated (the mandate `enabled` field still holds) |
| Change Actions or repository settings | Actions permissions API, repository API | Wider tokens, auto-merge |
| Cancel runs, disable or dispatch workflows | Actions API (write) | Gate runs stopped; dispatch only re-evaluates |
| Push a branch that adds or edits a workflow | git push, Contents API, Git Data API | The `pull_request` run uses the pull request's workflow file with the permissions that file declares. It can create an `autonomy-gate` check as app 15368, approve (if section 9 is ON) and call merge. This defeats the gate without a settings change. A changed copy of `autonomy-gate.yml` on a branch can also drop the dispatch guard. |

It cannot (verify): approve its own pull request as the owner; create a check
run with a user token; push to `main` while protection holds.

Condition 1 holds only if the agent's token has NO Administration, Workflows,
Variables, Secrets or Actions-write permission. A classic OAuth token with the
`repo` scope, held by an administrator, includes administration of a private
repository and cannot be narrowed. A fine-grained token or a GitHub App token
can: grant Contents and Pull requests write only.

## Other facts to know

- A green CI run proves nothing about agent-written test code. A test can exit
  0 early, read the read-only job token and reach the network. The gate trusts
  only the run's conclusion for the pinned head SHA, its workflow file path and
  its link to this pull request with base `main`.
- A merge made with the job token does not start `ci.yml` on `main`, because
  events made by `GITHUB_TOKEN` do not start workflows. Run CI on `main` by
  hand after a merge if you need that evidence.
- Re-running an old gate run uses the workflow file of that old run, but the
  checkout is still `ref: main`, so the current `gate.py` runs.
- A re-run of the gate after a merge makes no writes: the gate reads the pull
  request first and does nothing if it is merged or closed.
- Gate runs share one concurrency lane. GitHub keeps at most ONE pending run
  per group: a newer run replaces the pending one, even with
  `cancel-in-progress: false`. The replaced evaluation is lost. A later event
  (CI completion, a label change, a push or a manual dispatch) triggers it again.
- `ci.yml` has two jobs on separate runners. `tests` runs the pull request's
  tests. `gate-tests` runs only `tests_gate/` and installs only pinned pytest
  and PyYAML. In ONE job a file under `tests/` could rewrite `tests_gate/` or
  `gate.py` on disk, or add a `.pth` file, before the gate tests ran. A
  separate job removes that path.
- A pull request under the mandate cannot change `tests_gate/` or `gate.py`:
  both are outside the allowlist. A human change to `gate.py` must pass the
  isolated `gate-tests` job before it merges.
- If `gate.py` hits an unexpected error, the step exits 1 and, when the head
  SHA is known, posts a failing `autonomy-gate` check. A failed write (for
  example a dismissal) also exits 1, after the failing check is posted.
- Log output from the gate has `::` and control characters removed, so
  API-derived text cannot issue workflow commands.

## Fallback design (if the agent CAN push workflow files)

Not built. Use it if negative-test group 2 shows that a workflow push works.

- Create a dedicated GitHub App, used only for the gate's check run and merge.
- Store its private key as a secret of an environment named `autonomy-gate`.
  Give the environment a deployment-branch rule: `main` only. A pull-request
  run uses `refs/pull/N/merge`, so it cannot read the key.
- The gate job uses `environment: autonomy-gate` and makes an App token.
- Branch protection pins the required `autonomy-gate` check to the App's `app_id`.
- "Allow GitHub Actions to create and approve pull requests" stays OFF.
- Drop the required review. A personal repository cannot require one specific
  reviewer, so any review requirement is satisfied or blocked by the wrong party.
  The App-pinned check is the approval.

What it does NOT fix: an agent credential with administration rights defeats
all of this, because it can change protection and environments. In that case
the agent must use a non-owner identity (a separate account or App with write,
not admin, access).
