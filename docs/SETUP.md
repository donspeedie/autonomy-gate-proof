# Repository setup

Apply these settings in the order given. Treat every control as UNPROVEN until
a test in `NEGATIVE-TESTS.md` shows that it holds.

The commands use PowerShell syntax. Set the repository name first:

```powershell
$R = "donspeedie/autonomy-gate-proof"
```

## Read this first: why the gate uses its own app

The coding agent's credential is the Claude GitHub App. Its public manifest
(`gh api apps/claude --jq .permissions`) grants `workflows: write`,
`actions: write`, `contents: write`, `pull_requests: write`, `checks: write`
and `administration: read`. With `workflows: write` the agent can push a
workflow file on its own branch. That workflow runs as GitHub Actions (app
15368) with whatever `GITHUB_TOKEN` permissions it declares. In the earlier
design it could create a check named `autonomy-gate` as app 15368 and approve,
which defeated the gate.

The current design takes that power away:

1. A dedicated **gate app**, owned by the repository owner, installed on this
   repository only. Permissions: Checks write, Contents write, Pull requests
   write, Metadata read. No Workflows, Administration, Actions, Secrets,
   Variables or Environments.
2. The gate app's client ID and private key are secrets of the environment
   **`autonomy-gate`**. Its deployment-branch rule allows the branch `main` only.
3. Branch protection requires `autonomy-gate` **from the gate app's id**. A
   check with that name from any other app does not count.
4. **No required review.** The gate submits no review. The check run is the approval.
5. "Allow GitHub Actions to create and approve pull requests" stays **OFF
   permanently**.

`GATE-APP-SETUP.md` has the click-by-click steps for the owner.

## Why the required review is dropped

- A personal repository cannot require a review from one specific reviewer.
  Any approving review from a user or app with write access satisfies the rule.
- The owner cannot approve the owner's own pull request, so a review rule
  blocks every human change unless protection is lifted.
- A review from `github-actions[bot]` would be forgeable: any workflow the
  agent pushes can submit one if the Actions approval setting is ON. That is
  why the setting stays OFF.
- A review from the gate app adds nothing the check does not already give, and
  adds a second write that can fail and a dismissal path that can fail.

So the gate does not review, even as information. The success check's summary
names the head SHA and the mandate blob SHA instead.

## What binds the validation to the head commit

With no review, three facts carry the binding:

1. **The check run is per commit.** The gate creates `autonomy-gate` on the
   exact head SHA X. A new push makes a new head SHA. That SHA has no gate
   check until the gate runs again, so protection blocks the merge. Nothing has
   to be dismissed. (Branch protection reads required checks on the head SHA
   only. UNVERIFIED by test until 5.15 passes.)
2. **`strict: true`.** The head must contain the current `main`.
3. **The merge call's `sha` = X.** If the head moved, GitHub answers 409.

The gate posts a failing check first ("merge in progress"), then a success
check, then merges. Protection must use the NEWEST gate-app check with that
name (UNVERIFIED: record it in P1). If the merge is not confirmed, the gate and
its cleanup step post a failing check on X, so the newest gate check is a failure.

## Step order

| Step | What | Section |
|------|------|---------|
| 1 | Default branch, merge settings | 1, 2 |
| 2 | Actions token read-only, Actions approval **OFF** (permanent) | 3 |
| 3 | Allowed actions, variable `false`, label, no repository secrets | 4 to 7 |
| 4 | Owner: create the environment `autonomy-gate` with the `main`-only rule. Do this FIRST. | GATE-APP-SETUP.md Part 1 |
| 5 | Owner: create the gate app, store its two environment secrets, install it on this repository only. Send the App ID to the operator. | GATE-APP-SETUP.md Parts 2 to 4 |
| 6 | Operator: merge the gate-app change to `main` and apply the new protection | 8a |
| 7 | Run negative-test group 0 (agent-pushed workflows), then groups 1, 3 and 4. The variable stays `false`. | NEGATIVE-TESTS.md |
| 8 | Stop here if any of those tests fails | |
| 9 | Set the variable to `true`. Run group 5. | 9, NEGATIVE-TESTS.md |
| 10 | Run group 6, the positive case P1, then 0.4 | NEGATIVE-TESTS.md |

Why the variable stays `false` in step 7: the attacks of group 0 do not go
through the gate at all, so the variable cannot hide a failure there. With the
variable `false`, the gate refuses every pull request (`stop-switch`), so no
legitimate merge can happen while the attacks run.

Dry run: if the environment secrets do not exist, the token step is skipped.
`gate.py` then evaluates, prints `DRY RUN ... rule=...` and writes nothing.
The step exits 1, so the run is red. No gate check means no merge. This is
the fail-closed state, not a mode you need for the tests.

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

## 3. Actions: read-only default token, Actions approval OFF for good

```powershell
gh api -X PUT "repos/$R/actions/permissions/workflow" -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false
```
Why OFF, permanently: when this setting is ON, ANY workflow can approve or
create a pull request with `GITHUB_TOKEN`, including a workflow that the agent
pushes. The gate no longer needs it. Never turn it on.

## 4. Actions: allow only GitHub-owned actions

```powershell
gh api -X PUT "repos/$R/actions/permissions" -F enabled=true -f allowed_actions=selected
gh api -X PUT "repos/$R/actions/permissions/selected-actions" -F github_owned_allowed=true -F verified_allowed=false
```
Why: the workflows use only `actions/checkout`, `actions/setup-python` and
`actions/create-github-app-token`, all GitHub-owned and pinned by full SHA.

## 5. Stop switch variable (start OFF)

```powershell
gh api -X POST "repos/$R/actions/variables" -f name=AUTONOMY_ENABLED -f value=false
```
Why: the gate reads `vars.AUTONOMY_ENABLED` once, at job start. Only the exact
string `true` lets it continue. The mandate field `enabled` is the second
switch: the gate reads it again, with its blob SHA, immediately before the
success check. The gate reads the mandate at the pinned `main` SHA.

## 6. Label

```powershell
gh api -X POST "repos/$R/labels" -f name=autonomy -f color=ededed -f description="Opened by the autonomy agent"
```

## 7. No repository secrets

```powershell
gh api "repos/$R/actions/secrets" --jq .total_count
```
The result must be 0. A `pull_request` workflow from a branch in this
repository can read REPOSITORY secrets. The gate app's secrets are ENVIRONMENT
secrets only.

## 8. Branch protection on `main`

The final file is in `GATE-APP-SETUP.md`, "New branch protection". It requires
`tests` and `gate-tests` from app 15368, and `autonomy-gate` from the gate app's
id. It has no required review.

Why, per field:
- `autonomy-gate` with the gate app's `app_id`: only a token of the gate app can
  create this check. A workflow the agent pushes runs as app 15368, and the
  agent's own credential is the Claude app. Neither can produce it.
- `tests` and `gate-tests` from app 15368: LIMIT: a workflow the agent pushes
  can create check runs with these names as app 15368 on any SHA. So these two
  requirements are a convenience, not a control. The binding CI evidence is the
  gate's own read of the `ci.yml` run: exact head SHA, event `pull_request`,
  path `.github/workflows/ci.yml`, linked to this pull request, both jobs green.
  At an accepted head, `ci.yml` is the `main` copy, because the gate refuses
  any change outside `tests/test_*.py`.
- `strict: true`: the head must contain the current `main`. The gate enforces
  the same rule itself (`not-up-to-date`).
- `required_pull_request_reviews: null`: see "Why the required review is dropped".
- `enforce_admins: true`: the owner gets no bypass.
- No force pushes, no deletions, linear history.

## 8a. Transition from the review-based protection

The protection now on `main` requires a review and `autonomy-gate` from app
15368. This change touches `.github/`, so the gate refuses it on the path rule
(expected), and nobody can give the review. The operator merges it by hand,
after steps 4 and 5:

```powershell
# 1. Check the environment rule and secrets. Expect "main branch", then the two secret names.
gh api "repos/$R/environments/autonomy-gate/deployment-branch-policies" --jq '.branch_policies[] | [.name, .type] | @tsv'
gh api "repos/$R/environments/autonomy-gate/secrets" --jq '.secrets[].name'
# 2. Check that the variable is false.
gh api "repos/$R/actions/variables/AUTONOMY_ENABLED" --jq .value
# 3. Remove protection, squash-merge, and apply the NEW protection at once.
gh api -X DELETE "repos/$R/branches/main/protection"
gh pr merge <N> -R $R --squash --match-head-commit <HEAD_SHA>
gh api -X PUT "repos/$R/branches/main/protection" --input protection.json
```

`protection.json` is the file from `GATE-APP-SETUP.md`, "New branch protection",
with the App ID filled in. The window without protection lasts only between
the DELETE and the PUT. Before the DELETE, check that no agent run is active.

## 9. Enable

Do this at step 9, only after steps 1 to 8 pass:

```powershell
gh api "repos/$R/actions/permissions/workflow" --jq .can_approve_pull_request_reviews
gh api -X PATCH "repos/$R/actions/variables/AUTONOMY_ENABLED" -f name=AUTONOMY_ENABLED -f value=true
```
The first command must print `false`.

## Kill procedure

Do these in order. A per-pull-request stop that needs no administrator rights:
remove the `autonomy` label. The gate re-checks the label immediately before
the success check.

```powershell
# 1. Stop switch off (the gate reads it at job start)
gh api -X PATCH "repos/$R/actions/variables/AUTONOMY_ENABLED" -f name=AUTONOMY_ENABLED -f value=false
# 2. Cancel EVERY gate run whose status is not completed
gh run list -R $R --workflow autonomy-gate.yml --limit 200 --json databaseId,status | ConvertFrom-Json | Where-Object { $_.status -ne 'completed' } | ForEach-Object { gh api -X POST "repos/$R/actions/runs/$($_.databaseId)/cancel" }
# 3. List again. This must print nothing. If a run stays, repeat step 2; if it is stuck in cancelling, use force-cancel.
gh run list -R $R --workflow autonomy-gate.yml --limit 200 --json databaseId,status | ConvertFrom-Json | Where-Object { $_.status -ne 'completed' } | ForEach-Object { "$($_.databaseId) $($_.status)" }
gh api -X POST "repos/$R/actions/runs/<ID>/force-cancel"
```

Then step 4 for every open pull request. Replace `<GATE_APP_ID>` with the number:

```powershell
# 4. Find the newest gate-app check on each open pull request head
gh pr list -R $R --state open --json number,headRefOid | ConvertFrom-Json | ForEach-Object { "PR $($_.number) head $($_.headRefOid)" }
gh api "repos/$R/commits/<HEAD_SHA>/check-runs?check_name=autonomy-gate&filter=latest" --jq '.check_runs[] | select(.app.id == <GATE_APP_ID>) | [.id, .conclusion, .completed_at] | @tsv'
```

**Step 5 is MANDATORY** if the newest gate-app `autonomy-gate` check on any
open head is `success`. In that state the agent (contents write) could call the
merge endpoint itself. Lock `main` first, then make the newest check a failure:

```powershell
# 5. Lock main: set "lock_branch": true in protection.json, then apply it
gh api -X PUT "repos/$R/branches/main/protection" --input protection.json
# 6. Dispatch the gate from main for that pull request. The stop switch is off,
#    so the gate refuses with stop-switch and posts a failing gate-app check.
gh workflow run autonomy-gate.yml -R $R --ref main -f pr_number=<N>
# 7. Repeat step 4. The newest gate-app check must now be failure. Then unlock main.
```

Step 6 needs the gate workflow enabled and the app installed. Do the slow,
strong stops only after step 7:

```powershell
# 8. Disable the gate workflow (no new runs start)
gh api -X PUT "repos/$R/actions/workflows/autonomy-gate.yml/disable"
# 9. Strongest: the owner suspends the gate app installation (GATE-APP-SETUP.md, "Kill switch").
#    No gate run can then mint a token. A success check that already exists stays: keep main locked.
# 10. Optional, slow: set "enabled": false in the mandate. This needs protection lifted.
```

The cleanup step of the gate posts the failing check on cancel. It is best
effort: if the runner dies, the cleanup step does not run. Steps 4 to 7 are the
manual replacement.

## What the agent can still do (must be TESTED, not assumed)

The agent's credential (Claude app): `actions: write`, `workflows: write`,
`contents: write`, `pull_requests: write`, `checks: write`, `issues: write`,
`repository_hooks: write`, `administration: read`. No Secrets, Variables or
Environments permission, and no Administration write.

| Action | Through | Result |
|--------|---------|--------|
| Disable, cancel or re-run gate runs | Actions API (write) | Denial of service only. No gate run means no gate check, so no merge. A re-run of a `main` gate run re-evaluates with the current `gate.py`. |
| Dispatch the gate from `main` | Actions API | A re-evaluation. Every rule applies. |
| Dispatch an EDITED gate copy from its own branch | Actions API | That run has `GITHUB_REF` = the branch. The environment refuses it the secrets. |
| Push a workflow on its own branch | git push (workflows write) | It runs as app 15368 on that branch's ref with any `GITHUB_TOKEN` permission it declares. See the next table. |
| Create check runs (its own app) | Checks API | As the Claude app id. Protection ignores them. |
| Add a repository webhook | Hooks API (write) | Event payloads go out. No secret values. Watch for it. |
| Merge directly | Pulls API | Refused: the gate-app check is missing on the head (unless a success check exists; see kill step 5). |

What an agent-pushed workflow on its own branch can do with `GITHUB_TOKEN`:

| It CAN | It CANNOT |
|--------|-----------|
| Push to branches that are not protected; delete them | Push to `main` (protection, `enforce_admins`) |
| Create tags and releases | Read the environment secrets (its ref is not `main`) |
| Comment; add or remove labels (the gate requires the label anyway) | Produce a check run from the gate app |
| Create commit statuses and check runs as app 15368, including names `tests`, `gate-tests`, `autonomy-gate` | Approve or open pull requests (Actions approval is OFF) |
| Create deployments (no secrets come with them) | Merge: the required gate-app check is absent |
| Dispatch, cancel or re-run workflows (`actions: write`) | Change settings, variables, protection or environments (no such `GITHUB_TOKEN` scope) |

Tags matter in two places. A tag named `main` could shadow the branch name, so
the gate checks out `refs/heads/main`, not `main`, and reads `main` through
`git/ref/heads/main`. A tag push runs workflows with `GITHUB_REF` =
`refs/tags/...`, which the branch-type rule `main` does not match. No workflow
on `main` listens to `release` or tag events.

### Triggers that run with `GITHUB_REF` = `refs/heads/main`

The environment lets a job in only when its ref is `refs/heads/main`. The
question is whether the agent controls the workflow FILE in any such run:

- `push` to `main`: the file is `main`'s. The agent cannot push to `main`.
- `workflow_run`, `pull_request_target`, `schedule`, `issues`,
  `issue_comment`, `release`, `repository_dispatch` and other non-pull-request
  events: GitHub runs the file on the DEFAULT branch. The agent cannot change
  it without a merged pull request. On `main` only `autonomy-gate.yml` (no
  writes except through `gate.py` from `main`) and `ci.yml` (`push` to `main`,
  no environment) exist.
- `workflow_dispatch` with `ref=main` and re-runs of `main` runs: the file is
  `main`'s.
- `workflow_dispatch` with any other ref, `push` to any other branch,
  `pull_request` (ref `refs/pull/N/merge`) and tag pushes: the agent controls
  the file, but the ref is not `main`, so the environment refuses the job.

No trigger runs a workflow file that the agent controls with `GITHUB_REF` =
`refs/heads/main`, as long as `main` holds only these two workflows. A new
workflow on `main` that listens to an event and runs code from a pull request
would break this. Review every workflow change to `main` with this in mind.

## Other facts to know

- A green CI run proves nothing about agent-written test code. A test can exit
  0 early, read the read-only job token and reach the network. The gate trusts
  only the run's conclusion for the pinned head SHA, its workflow file path and
  its link to this pull request with base `main`.
- A merge made with the gate app's token DOES start workflows (unlike
  `GITHUB_TOKEN`), so `ci.yml` runs on `main` after a merge. The gate's
  `workflow_run` for that `push` run does nothing. (UNVERIFIED: record it in P1.)
- The workflow `GITHUB_TOKEN` of the gate job is read-only. `gate.py` sends
  every GET with it and every write with the gate app token. A gate run with
  no gate app token is a dry run.
- The token action revokes the gate app token in its post step, after the
  cleanup step. The token is scoped to this repository and to checks, contents
  and pull requests write.
- Re-running an old gate run uses the workflow file of that old run, but the
  checkout is still `refs/heads/main`, so the current `gate.py` runs. A re-run
  of a run from before this change uses the old workflow: it writes with
  `GITHUB_TOKEN` as app 15368, which protection ignores.
- A re-run of the gate after a merge makes no writes: the gate reads the pull
  request first and does nothing if it is merged or closed.
- Gate runs share one concurrency lane. GitHub keeps at most ONE pending run
  per group: a newer run replaces the pending one, even with
  `cancel-in-progress: false`. A later event triggers the lost evaluation again.
- `ci.yml` has two jobs on separate runners. `gate-tests` runs only
  `tests_gate/` and installs only pinned pytest and PyYAML.
- If `gate.py` hits an unexpected error, the step exits 1 and, when the head
  SHA is known, posts a failing `autonomy-gate` check.
- Log output from the gate has `::` and control characters removed.

## What this design does NOT fix

An agent credential with Administration write defeats all of this: it can
change protection, environments and secrets. The Claude app has
`administration: read` today. If that changes, stop and use a non-owner
identity for the agent.
