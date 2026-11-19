# Gate app setup

This page tells the repository OWNER how to make the dedicated gate app. It
also gives the commands that the OPERATOR runs after that. Do the parts in order.

The commands use PowerShell. Run them one line at a time.

## What the gate app is, in four sentences

The gate app is a small GitHub App that only you own. It is the only identity
that can post the `autonomy-gate` check that branch protection requires.
The coding agent cannot use it, because its key is kept in an environment that
only the `main` branch can open. The gate app cannot change settings, workflows
or secrets.

## Part 1: create the environment first (owner or operator)

Do this BEFORE the gate change merges. If the gate workflow runs first, GitHub
makes the environment for you with NO branch rule.

You can do it on the web, or give it to the operator (see "Operator commands").

1. Open `https://github.com/donspeedie/autonomy-gate-proof/settings/environments`.
2. Click **New environment**.
3. Type the name `autonomy-gate`. Click **Configure environment**.
4. Leave **Required reviewers** off. Leave **Wait timer** off.
5. Clear the box **Allow administrators to bypass configured protection rules**.
6. Under **Deployment branches and tags**, choose **Selected branches and tags**.
7. Click **Add deployment branch or tag rule**.
8. Set **Ref type** to **Branch**. Type the name pattern `main`. Click **Add rule**.
9. Make sure that the list shows exactly one rule: `main`, type branch.
10. Click **Save protection rules**.

## Part 2: create the gate app (owner)

App names are global on GitHub. Pick a unique name now, for example
`autonomy-gate-k7q2` (four random letters or digits at the end). Write it down.

### 2a. Save the manifest page

Copy the text below into Notepad. Change `autonomy-gate-CHANGE-ME` to your
unique name. Save the file on your desktop as `gate-app.html`
(set **Save as type** to **All files**).

```html
<!doctype html>
<html><body>
<form action="https://github.com/settings/apps/new" method="post">
<p>Check the name, then click the button.</p>
<textarea name="manifest" rows="22" cols="90">
{
  "name": "autonomy-gate-CHANGE-ME",
  "url": "https://github.com/donspeedie/autonomy-gate-proof",
  "description": "Merge gate for autonomy-gate-proof. Posts the autonomy-gate check run and merges. Test repository.",
  "public": false,
  "hook_attributes": {"url": "https://github.com/donspeedie/autonomy-gate-proof", "active": false},
  "redirect_url": "https://github.com/donspeedie/autonomy-gate-proof",
  "default_permissions": {
    "checks": "write",
    "contents": "write",
    "pull_requests": "write",
    "metadata": "read"
  },
  "default_events": []
}
</textarea>
<p><button type="submit">Create the gate app on GitHub</button></p>
</form>
</body></html>
```

What the manifest asks for:

| Field | Value | Why |
|-------|-------|-----|
| `public` | `false` | Only your account can install it. |
| `hook_attributes.active` | `false` | No webhook. The app receives nothing. |
| `checks` | write | Post the `autonomy-gate` check run. |
| `contents` | write | Merge the pull request. |
| `pull_requests` | write | Write the refusal comment on a pull request. |
| `metadata` | read | Required by GitHub for every app. |
| Not asked | Workflows, Administration, Actions, Secrets, Environments, Variables | The app cannot change the rules that hold it. |

### 2b. Create the app

1. Sign in to GitHub in your browser as `donspeedie`.
2. Double-click `gate-app.html` on your desktop. It opens in the browser.
3. Check the name in the box. Click **Create the gate app on GitHub**.
4. GitHub shows a page "Create GitHub App". Check the name. Click **Create GitHub App**.
5. GitHub sends you to the repository page. The address bar now ends with
   `?code=` and a long code. Copy only the code (the part after `code=`).
6. Do Part 2c within one hour. The code works one time only.
7. Delete `gate-app.html`. It holds no secret, but you do not need it again.

### 2c. Store the app's credentials (owner)

This step turns the code into the app's credentials and stores them directly
as environment secrets. The private key never touches your disk or screen.

Paste this one line into PowerShell. Replace `PASTE-CODE-HERE` with the code
first:

```powershell
$code = 'PASTE-CODE-HERE'; $r = gh api -X POST "app-manifests/$code/conversions" | ConvertFrom-Json; $r.pem | gh secret set GATE_APP_PRIVATE_KEY --env autonomy-gate -R donspeedie/autonomy-gate-proof; $r.client_id | gh secret set GATE_APP_CLIENT_ID --env autonomy-gate -R donspeedie/autonomy-gate-proof; "App ID: $($r.id)   App slug: $($r.slug)"; Remove-Variable r, code
```

It prints two lines from `gh` ("Set Actions secret ...") and then the App ID
and the app slug. Write down the App ID (a number). The operator needs it for
branch protection. The App ID is not a secret.

If this step fails (for example, the code expired), use Part 2d instead.

### 2d. Only if 2c failed, or to replace the key later: a .pem file

1. Open `https://github.com/settings/apps`. Click your gate app.
2. Note the **App ID** and the **Client ID** at the top of the page.
3. Scroll to **Private keys**. Click **Generate a private key**.
   Your browser downloads a `.pem` file.
4. Paste this one line into PowerShell. Replace the path with the real path of
   the `.pem` file:

```powershell
$pem = 'C:\Users\YOU\Downloads\your-app.private-key.pem'; Get-Content -Raw $pem | gh secret set GATE_APP_PRIVATE_KEY --env autonomy-gate -R donspeedie/autonomy-gate-proof; Remove-Item $pem; Remove-Variable pem
```

5. Paste this line. Replace `PASTE-CLIENT-ID` with the Client ID from step 2:

```powershell
'PASTE-CLIENT-ID' | gh secret set GATE_APP_CLIENT_ID --env autonomy-gate -R donspeedie/autonomy-gate-proof
```

6. Check that the `.pem` file is gone from your Downloads folder. Empty the
   Recycle Bin if the file is there.
7. If you replaced a key, delete the OLD key on the app page under **Private keys**.

## Part 3: install the app on this repository only (owner)

1. Open `https://github.com/settings/apps`. Click your gate app.
2. Click **Install App** on the left. Click **Install** next to `donspeedie`.
3. Choose **Only select repositories**. Select `autonomy-gate-proof`.
4. Click **Install**.
5. Check: the install page lists exactly one repository.

## Part 4: check your work (owner)

Run these two lines:

```powershell
gh api "repos/donspeedie/autonomy-gate-proof/environments/autonomy-gate/deployment-branch-policies" --jq '.branch_policies[] | [.name, .type] | @tsv'
gh api "repos/donspeedie/autonomy-gate-proof/environments/autonomy-gate/secrets" --jq '.secrets[].name'
```

The first line must print exactly one line: `main` and `branch`.
The second line must print `GATE_APP_CLIENT_ID` and `GATE_APP_PRIVATE_KEY`.
Send the App ID to the operator. Do not send the key.

## Operator commands

Set the repository name first:

```powershell
$R = "donspeedie/autonomy-gate-proof"
```

### Create the environment and its branch rule (if the owner asks the operator)

Save as `environment.json`:

```json
{
  "wait_timer": 0,
  "prevent_self_review": false,
  "reviewers": [],
  "can_admins_bypass": false,
  "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}
}
```

```powershell
gh api -X PUT "repos/$R/environments/autonomy-gate" --input environment.json
gh api -X POST "repos/$R/environments/autonomy-gate/deployment-branch-policies" -f name=main -f type=branch
gh api "repos/$R/environments/autonomy-gate" --jq '.deployment_branch_policy, .can_admins_bypass'
gh api "repos/$R/environments/autonomy-gate/deployment-branch-policies" --jq '.branch_policies[] | [.name, .type] | @tsv'
```

The last command must print exactly `main` and `branch`.
Why `custom_branch_policies` and not `protected_branches`: a custom rule names
`main` only. "Protected branches" would admit any branch that later gets protection.

### Check the app's permissions (no key needed)

Replace `<slug>` with the app slug:

```powershell
gh api "apps/<slug>" --jq '.id, .permissions'
```

The permissions must be exactly `checks: write`, `contents: write`,
`metadata: read`, `pull_requests: write`.

### New branch protection on `main`

Save as `protection.json`. Replace `REPLACE_WITH_GATE_APP_ID` with the App ID
number (no quotes). The file is not valid JSON until you do this, so a
forgotten edit fails loudly.

```json
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      {"context": "tests", "app_id": 15368},
      {"context": "gate-tests", "app_id": 15368},
      {"context": "autonomy-gate", "app_id": REPLACE_WITH_GATE_APP_ID}
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
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
gh api "repos/$R/branches/main/protection" --jq '.required_status_checks.checks, .required_pull_request_reviews, .enforce_admins.enabled'
```

The second command must show the three checks with their app ids, `null` for
reviews, and `true`. See `SETUP.md` section 8 for the reason behind each field.

### Kill switch for the app itself

The strongest stop: suspend the gate app's installation. Then no gate run can
mint a token, so no new `autonomy-gate` check and no merge by the gate.
On the web: `https://github.com/settings/installations`, click **Configure**
next to the gate app, then **Suspend**. See `SETUP.md`, "Kill procedure", for
the order of the other steps.
