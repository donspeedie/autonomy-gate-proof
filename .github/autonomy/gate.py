"""Autonomy merge gate.

This script runs ONLY in the trusted workflow autonomy-gate.yml. That workflow
checks out this file from the base branch `main`. The script never reads,
imports or runs code from the pull request.

The script evaluates ONE pull request against the mandate on `main`. It reads
the head SHA once (X) and the `main` SHA once (M). It takes every later fact
from data pinned to X and M: the mandate at M, the compare of M...X and the git
trees of X and of the merge base. The merge base must be M. If every rule
passes, it approves X and merges with `sha` = X. If any rule fails, it records
the refusal and never approves.

Rules are pure functions. Each rule raises Refusal or Pending. `main()` builds
a real REST client. Tests inject a fake client.

Write order on a refusal: failing check first, then every dismissal, then the
comment. Errors are collected and raised at the end. A merged or closed pull
request gets no writes at all.

Modes:
  gate.py            evaluate one pull request and act
  gate.py --cleanup  if an approval was given but the merge is not confirmed,
                     post a failing check and dismiss the gate's approvals

Exit code: 0 for a clean decision, 1 for an internal error or a failed write.
"""

from __future__ import annotations

import base64
import datetime as dt
import fnmatch
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol

CHECK_NAME = "autonomy-gate"
BOT_LOGIN = "github-actions[bot]"
COMMENT_MARKER = "<!-- autonomy-gate -->"
BASE_BRANCH = "main"
MAIN_REF = "refs/heads/main"
MANDATE_PATH = ".github/autonomy/mandate.json"
MANDATE_SCHEMA = 1
PAGE_SIZE = 100
MAX_PAGES = 30
COMPARE_FILE_CAP = 300  # the compare API lists at most 300 files
REGULAR_FILE_MODE = "100644"
SAFE_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
PR_NUMBER = re.compile(r"[0-9]{1,9}")
SHA = re.compile(r"[0-9a-f]{40}")
# Defense in depth. The allowlist already excludes these paths.
FORBIDDEN_PREFIXES = (".github/", "shapes/", "tests_gate/", "docs/")
FORBIDDEN_NAMES = ("conftest.py", "pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", "tox.ini")
STATE_FILE = "autonomy-gate-state.json"
# The gate requires these CI jobs even if the mandate names fewer.
GATE_REQUIRED_JOBS = ("tests", "gate-tests")
MAX_MANDATE_BYTES = 16384
WORKFLOW_FILE = re.compile(r"[A-Za-z0-9_-]{1,64}\.ya?ml")
JOB_NAME = re.compile(r"[A-Za-z0-9_. -]{1,64}")
# Workflow commands start with `::`. Control characters can start a new log line.
LOG_UNSAFE = re.compile(r"[\x00-\x1f\x7f]|::")


class Refusal(Exception):
    """A rule refused the pull request."""

    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(f"{rule}: {detail}")
        self.rule = rule
        self.detail = detail


class Pending(Refusal):
    """Evidence is not complete yet. The gate refuses for now."""


@dataclass(frozen=True)
class Response:
    status: int
    body: Any


class Client(Protocol):
    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Response: ...


def utc_now() -> dt.datetime:
    """Return the runner clock in UTC."""
    return dt.datetime.now(dt.timezone.utc)


def no_journal(_state: dict[str, Any]) -> None:
    """Default journal: keep nothing."""


@dataclass(frozen=True)
class Context:
    repo: str
    event_name: str
    event: dict[str, Any]
    stop_value: str
    now: dt.datetime
    ref: str = MAIN_REF
    clock: Callable[[], dt.datetime] = utc_now
    journal: Callable[[dict[str, Any]], None] = no_journal


@dataclass
class Decision:
    approved: bool
    rule: str
    detail: str
    pr_number: int | None = None
    head_sha: str | None = None
    mandate_blob_sha: str | None = None
    main_sha: str | None = None


# ---------------------------------------------------------------- API helpers


def api_get(client: Client, path: str) -> Any:
    """GET a path. Any status other than 200 is a refusal."""
    resp = client.request("GET", path)
    if resp.status != 200:
        raise Refusal("api-error", f"GET {path} returned HTTP {resp.status}")
    return resp.body


def paginate(client: Client, path: str) -> list[Any]:
    """GET all pages of a list endpoint, up to MAX_PAGES. More pages is a refusal."""
    sep = "&" if "?" in path else "?"
    items: list[Any] = []
    for page in range(1, MAX_PAGES + 1):
        body = api_get(client, f"{path}{sep}per_page={PAGE_SIZE}&page={page}")
        if isinstance(body, dict):
            body = body.get("workflow_runs", body.get("jobs"))
        if not isinstance(body, list):
            raise Refusal("api-error", f"unexpected list shape from {path}")
        items.extend(body)
        if len(body) < PAGE_SIZE:
            return items
    raise Refusal("pagination-bound", f"{path} has more than {MAX_PAGES} pages")


# ---------------------------------------------------------------- event and shape


def resolve_pr_number(ctx: Context, client: Client) -> int | None:
    """Return the pull request number for this event, or None if no work."""
    if ctx.event_name == "workflow_dispatch":
        if ctx.ref != MAIN_REF:
            raise Refusal("dispatch-ref", "workflow_dispatch is allowed only from refs/heads/main")
        raw = str(ctx.event.get("inputs", {}).get("pr_number", ""))
        if not PR_NUMBER.fullmatch(raw):
            raise Refusal("bad-input", "pr_number must be digits")
        return int(raw)
    if ctx.event_name == "pull_request_target":
        return int(ctx.event["pull_request"]["number"])
    if ctx.event_name == "workflow_run":
        run = ctx.event["workflow_run"]
        if run.get("event") != "pull_request":
            return None
        linked = [p["number"] for p in run.get("pull_requests") or []]
        if len(linked) == 1:
            return int(linked[0])
        head_sha = str(run.get("head_sha", ""))
        if linked or not SHA.fullmatch(head_sha):
            return None
        pulls = api_get(client, f"/repos/{ctx.repo}/commits/{head_sha}/pulls")
        open_hits = [p["number"] for p in pulls if p.get("state") == "open"]
        return int(open_hits[0]) if len(open_hits) == 1 else None
    raise Refusal("bad-event", f"unsupported event {ctx.event_name}")


def check_pr_shape(pr: dict[str, Any], repo: str) -> str:
    """Refuse forks, other bases, drafts and closed pull requests. Return head SHA."""
    if pr.get("state") != "open":
        raise Refusal("not-open", "pull request is not open")
    if pr.get("draft"):
        raise Refusal("draft", "pull request is a draft")
    head_repo = (pr.get("head") or {}).get("repo") or {}
    if head_repo.get("full_name") != repo:
        raise Refusal("fork", "head repository is not this repository")
    base = pr.get("base") or {}
    if base.get("ref") != BASE_BRANCH or (base.get("repo") or {}).get("full_name") != repo:
        raise Refusal("wrong-base", f"base must be {BASE_BRANCH} of {repo}")
    head_sha = str((pr.get("head") or {}).get("sha", ""))
    if not SHA.fullmatch(head_sha):
        raise Refusal("bad-head", "head SHA is not a full SHA")
    return head_sha


def check_stop_switch(value: str) -> None:
    """Only the exact string `true` lets the gate continue."""
    if value != "true":
        raise Refusal("stop-switch", "repository variable AUTONOMY_ENABLED is not exactly 'true'")


# ---------------------------------------------------------------- mandate

MANDATE_FIELDS: dict[str, type] = {
    "schema": int, "enabled": bool, "enabled_until_utc": str, "allowed_path_globs": list,
    "max_files": int, "max_added_lines": int, "max_deleted_lines": int,
    "max_bytes_per_file": int, "max_total_bytes": int,
    "branch_prefix": str, "label": str, "max_open_prs": int,
    "max_merges_per_utc_day": int, "max_total_merges": int,
    "required_ci_workflow": str, "required_ci_jobs": list,
}
# Inclusive (low, high) bound for every integer field.
MANDATE_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "schema": (MANDATE_SCHEMA, MANDATE_SCHEMA), "max_files": (1, 20),
    "max_added_lines": (0, 5000), "max_deleted_lines": (0, 5000),
    "max_bytes_per_file": (0, 200_000), "max_total_bytes": (0, 1_000_000),
    "max_open_prs": (1, 10), "max_merges_per_utc_day": (0, 20), "max_total_merges": (0, 500),
}
MANDATE_STR_MAX = {"enabled_until_utc": 32, "branch_prefix": 64, "label": 50, "required_ci_workflow": 70}
MANDATE_LIST_MAX = 20


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """object_pairs_hook for json.loads: a repeated key is an error."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def reject_constant(name: str) -> Any:
    """parse_constant for json.loads: NaN and Infinity are errors."""
    raise ValueError(f"constant {name} is not allowed")


def check_mandate_field(key: str, value: Any) -> None:
    """Type, bound and element checks for one known field."""
    kind = MANDATE_FIELDS[key]
    if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
        raise Refusal("mandate-invalid", f"field {key} is missing or has the wrong type")
    if key in MANDATE_INT_BOUNDS:
        low, high = MANDATE_INT_BOUNDS[key]
        if not low <= value <= high:
            raise Refusal("mandate-invalid", f"field {key} must be between {low} and {high}")
    if kind is str and not 0 < len(value) <= MANDATE_STR_MAX[key]:
        raise Refusal("mandate-invalid", f"field {key} must be 1 to {MANDATE_STR_MAX[key]} characters")
    if kind is list:
        if not 0 < len(value) <= MANDATE_LIST_MAX:
            raise Refusal("mandate-invalid", f"field {key} must have 1 to {MANDATE_LIST_MAX} items")
        if not all(isinstance(item, str) and 0 < len(item) <= 100 for item in value):
            raise Refusal("mandate-invalid", f"every item of {key} must be a string of 1 to 100 characters")


def parse_mandate(text: str) -> dict[str, Any]:
    """Parse and validate the mandate. Any shape error is the refusal mandate-invalid."""
    try:
        mandate = json.loads(text, object_pairs_hook=reject_duplicate_keys, parse_constant=reject_constant)
    except (ValueError, RecursionError) as exc:
        raise Refusal("mandate-invalid", f"not valid JSON: {exc}") from exc
    if not isinstance(mandate, dict):
        raise Refusal("mandate-invalid", "mandate is not an object")
    unknown = sorted(set(mandate) - set(MANDATE_FIELDS))
    if unknown:
        raise Refusal("mandate-invalid", f"unknown field(s) {unknown}")
    for key in MANDATE_FIELDS:
        check_mandate_field(key, mandate.get(key))
    if not WORKFLOW_FILE.fullmatch(mandate["required_ci_workflow"]):
        raise Refusal("mandate-invalid", "required_ci_workflow must be a plain .yml file name")
    if not all(JOB_NAME.fullmatch(j) for j in mandate["required_ci_jobs"]):
        raise Refusal("mandate-invalid", "a required_ci_jobs name has unsafe characters")
    parse_utc(mandate["enabled_until_utc"])
    return mandate


def parse_utc(text: str) -> dt.datetime:
    """Parse an ISO date-time that ends in Z and has no other offset."""
    if not text.endswith("Z"):
        raise Refusal("mandate-invalid", "enabled_until_utc must end in Z")
    try:
        parsed = dt.datetime.fromisoformat(text[:-1])
    except ValueError as exc:
        raise Refusal("mandate-invalid", f"bad enabled_until_utc: {exc}") from exc
    if parsed.tzinfo is not None:
        raise Refusal("mandate-invalid", "enabled_until_utc has an offset and a Z")
    return parsed.replace(tzinfo=dt.timezone.utc)


def check_mandate_active(mandate: dict[str, Any], now: dt.datetime) -> None:
    """Refuse when the mandate is disabled, or the UTC clock is at or after the expiry."""
    assert now.tzinfo is not None, "now must be timezone-aware"
    if mandate["enabled"] is not True:
        raise Refusal("mandate-disabled", "mandate field enabled is false")
    if now >= parse_utc(mandate["enabled_until_utc"]):
        raise Refusal("mandate-expired", f"mandate expired at {mandate['enabled_until_utc']}")


def load_mandate(client: Client, repo: str, at_sha: str) -> tuple[dict[str, Any], str]:
    """Read the mandate at the pinned `main` SHA. Return the mandate and its blob SHA."""
    assert SHA.fullmatch(at_sha), "the mandate must be read at a full main SHA"
    body = api_get(client, f"/repos/{repo}/contents/{MANDATE_PATH}?ref={at_sha}")
    if not isinstance(body, dict) or body.get("encoding") != "base64" \
            or not SHA.fullmatch(str(body.get("sha", ""))):
        raise Refusal("mandate-invalid", "unexpected contents response")
    try:
        raw = base64.b64decode(str(body.get("content", "")))
        if len(raw) > MAX_MANDATE_BYTES:
            raise ValueError(f"mandate is larger than {MAX_MANDATE_BYTES} bytes")
        text = raw.decode("utf-8")
    except ValueError as exc:  # binascii.Error and UnicodeDecodeError are ValueErrors
        raise Refusal("mandate-invalid", f"cannot decode the mandate: {exc}") from exc
    return parse_mandate(text), str(body["sha"])


# ---------------------------------------------------------------- pinned change set


def path_matches(path: str, pattern: str) -> bool:
    """Match one path segment per pattern segment. `*` never crosses `/`."""
    path_parts = path.split("/")
    pattern_parts = pattern.split("/")
    if len(path_parts) != len(pattern_parts):
        return False
    return all(fnmatch.fnmatchcase(p, q) for p, q in zip(path_parts, pattern_parts))


def check_branch_and_label(pr: dict[str, Any], mandate: dict[str, Any]) -> None:
    """Head branch must have the prefix. The label must be present."""
    head_ref = str((pr.get("head") or {}).get("ref", ""))
    if not head_ref.startswith(mandate["branch_prefix"]):
        raise Refusal("branch-prefix", f"head branch must start with {mandate['branch_prefix']}")
    labels = {str(label.get("name")) for label in pr.get("labels") or []}
    if mandate["label"] not in labels:
        raise Refusal("label", f"label {mandate['label']} is missing")


def tree_entries(tree: Any) -> dict[str, dict[str, Any]]:
    """Return non-directory entries of a recursive tree by path. Truncation is a refusal."""
    if not isinstance(tree, dict) or tree.get("truncated") is not False or not isinstance(tree.get("tree"), list):
        raise Refusal("tree", "tree is truncated or malformed")
    return {str(e.get("path")): e for e in tree["tree"] if e.get("type") != "tree"}


def diff_trees(base: dict[str, dict[str, Any]], head: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every path that differs between two trees, with a status."""
    changes = []
    for path in sorted(set(base) | set(head)):
        old, new = base.get(path), head.get(path)
        if old is None:
            changes.append({"path": path, "status": "added", **(new or {})})
        elif new is None:
            changes.append({"path": path, "status": "removed"})
        elif (old.get("sha"), old.get("mode")) != (new.get("sha"), new.get("mode")):
            changes.append({"path": path, "status": "modified", **new})
    return changes


def check_one_change(change: dict[str, Any], mandate: dict[str, Any]) -> int:
    """Check one changed path from the pinned trees. Return its size in bytes."""
    path = str(change.get("path", ""))
    if not SAFE_PATH.fullmatch(path) or ".." in path.split("/"):
        raise Refusal("path", "a file name has characters outside [A-Za-z0-9_./-] or a '..' segment")
    if path.startswith(FORBIDDEN_PREFIXES) or path.rsplit("/", 1)[-1] in FORBIDDEN_NAMES:
        raise Refusal("path", f"{path} is a protected path")
    if not any(path_matches(path, g) for g in mandate["allowed_path_globs"]):
        raise Refusal("path", f"{path} is outside the allowlist")
    if change.get("status") != "added":
        raise Refusal("not-added", f"{path} is {change.get('status')}; only new files are allowed")
    if change.get("type") != "blob" or change.get("mode") != REGULAR_FILE_MODE:
        raise Refusal("file-type", f"{path} has mode {change.get('mode')}; only regular files (100644)")
    size = int(change.get("size", -1))
    if size == 0:
        # An empty file has no patch in the compare. Refuse it by name.
        raise Refusal("empty-file", f"{path} is empty")
    if not 0 < size <= mandate["max_bytes_per_file"]:
        raise Refusal("too-many-bytes", f"{path} is {size} bytes > {mandate['max_bytes_per_file']}")
    return size


def check_compare_entry(entry: dict[str, Any]) -> tuple[int, int]:
    """Check one compare file entry. Return (additions, deletions)."""
    path = str(entry.get("filename", ""))
    shown = path if SAFE_PATH.fullmatch(path) else "<unsafe name>"
    if entry.get("status") != "added" or entry.get("previous_filename"):
        raise Refusal("not-added", f"{shown} has compare status {entry.get('status')}")
    if "patch" not in entry:
        raise Refusal("binary-or-large", f"{shown} has no text patch (binary or too large)")
    return int(entry.get("additions", 0)), int(entry.get("deletions", 0))


def check_change_set(changes: list[dict[str, Any]], compare_files: list[dict[str, Any]],
                     mandate: dict[str, Any]) -> None:
    """The pinned tree diff and the pinned compare must agree and obey the mandate."""
    if not changes:
        raise Refusal("files", "pull request changes no files")
    if len(compare_files) >= COMPARE_FILE_CAP:
        raise Refusal("files", "compare lists the maximum number of files; it may be truncated")
    if {c["path"] for c in changes} != {str(f.get("filename")) for f in compare_files}:
        raise Refusal("files", "tree diff and compare disagree on the changed files")
    if len(changes) > mandate["max_files"]:
        raise Refusal("too-many-files", f"{len(changes)} files > {mandate['max_files']}")
    total_bytes = sum(check_one_change(c, mandate) for c in changes)
    if total_bytes > mandate["max_total_bytes"]:
        raise Refusal("too-many-bytes", f"{total_bytes} bytes in total > {mandate['max_total_bytes']}")
    counts = [check_compare_entry(f) for f in compare_files]
    added, deleted = sum(a for a, _ in counts), sum(d for _, d in counts)
    if deleted > mandate["max_deleted_lines"]:
        raise Refusal("too-many-lines", f"{deleted} deleted lines > {mandate['max_deleted_lines']}")
    if added > mandate["max_added_lines"]:
        raise Refusal("too-many-lines", f"{added} added lines > {mandate['max_added_lines']}")


def load_change_set(client: Client, repo: str, main_sha: str, head_sha: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the compare and both trees, all pinned to SHAs.

    The head must contain the current main: the merge base must BE main. Then
    the squash result has the head's tree, and CI tested that tree. A head
    behind main, or a criss-cross history, waits for the agent to update it.
    """
    cmp = api_get(client, f"/repos/{repo}/compare/{main_sha}...{head_sha}")
    merge_base = str((cmp.get("merge_base_commit") or {}).get("sha", ""))
    if not SHA.fullmatch(merge_base) or not isinstance(cmp.get("files"), list):
        raise Refusal("compare", "compare response has no merge base or no file list")
    if merge_base != main_sha:
        raise Pending("not-up-to-date", f"head does not contain main {main_sha}; merge base is {merge_base}")
    base_tree = tree_entries(api_get(client, f"/repos/{repo}/git/trees/{merge_base}?recursive=1"))
    head_tree = tree_entries(api_get(client, f"/repos/{repo}/git/trees/{head_sha}?recursive=1"))
    return diff_trees(base_tree, head_tree), cmp["files"]


# ---------------------------------------------------------------- counters and CI


def is_autonomy_pr(pr: dict[str, Any], mandate: dict[str, Any]) -> bool:
    """A pull request counts if it has the label OR the branch prefix.

    The branch of a merged pull request cannot change, so removing the label
    after a merge does not reset the counters.
    """
    labels = {str(label.get("name")) for label in pr.get("labels") or []}
    head_ref = str((pr.get("head") or {}).get("ref", ""))
    return mandate["label"] in labels or head_ref.startswith(mandate["branch_prefix"])


def check_counters(open_prs: list[dict[str, Any]], closed_prs: list[dict[str, Any]],
                   pr_number: int, mandate: dict[str, Any], now: dt.datetime) -> None:
    """Enforce open, per-day and total limits from GitHub data."""
    open_auto = sorted(p["number"] for p in open_prs if is_autonomy_pr(p, mandate))
    if pr_number not in open_auto or len(open_auto) > mandate["max_open_prs"]:
        raise Refusal("open-limit", f"open autonomy pull requests are {open_auto}; limit {mandate['max_open_prs']}")
    merged = [p for p in closed_prs if p.get("merged_at") and is_autonomy_pr(p, mandate)]
    today = now.astimezone(dt.timezone.utc).date().isoformat()
    merged_today = [p for p in merged if str(p["merged_at"]).startswith(today)]
    if len(merged_today) >= mandate["max_merges_per_utc_day"]:
        raise Refusal("daily-limit", f"{len(merged_today)} autonomy merge(s) already on {today} UTC")
    if len(merged) >= mandate["max_total_merges"]:
        raise Refusal("total-limit", f"{len(merged)} autonomy merges reached the ceiling {mandate['max_total_merges']}")


def run_links_pr(run: dict[str, Any], pr_number: int) -> bool:
    """The run must name this pull request with base main. An empty list fails closed."""
    return any(p.get("number") == pr_number and (p.get("base") or {}).get("ref") == BASE_BRANCH
               for p in run.get("pull_requests") or [])


def select_ci_run(runs: list[dict[str, Any]], head_sha: str, repo: str, workflow_file: str,
                  pr_number: int) -> dict[str, Any]:
    """Return the newest CI run for the exact head SHA, this pull request and base main."""
    wanted_path = f".github/workflows/{workflow_file}"
    matching = [
        r for r in runs
        if r.get("head_sha") == head_sha
        and r.get("event") == "pull_request"
        and str(r.get("path", "")).split("@", 1)[0] == wanted_path
        and (r.get("head_repository") or {}).get("full_name") == repo
    ]
    if not matching:
        raise Pending("ci-missing", f"no {workflow_file} pull_request run for {head_sha}")
    newest = max(matching, key=lambda r: (int(r.get("run_number", 0)), int(r.get("id", 0))))
    if not run_links_pr(newest, pr_number):
        raise Refusal("ci-unlinked", f"run {newest.get('id')} is not linked to #{pr_number} with base {BASE_BRANCH}")
    if newest.get("status") != "completed":
        raise Pending("ci-pending", f"{workflow_file} run {newest.get('id')} is {newest.get('status')}")
    if newest.get("conclusion") != "success":
        raise Refusal("ci-failed", f"{workflow_file} run {newest.get('id')} concluded {newest.get('conclusion')}")
    return newest


def check_ci_jobs(jobs: list[dict[str, Any]], required: list[str]) -> None:
    """Every required job name must exist and every job with that name must succeed.

    The gate adds GATE_REQUIRED_JOBS to the mandate's list: the isolated
    `gate-tests` job is always required, so a mandate edit cannot drop it.
    """
    for name in sorted(set(required) | set(GATE_REQUIRED_JOBS)):
        hits = [j for j in jobs if j.get("name") == name]
        if not hits or any(j.get("conclusion") != "success" for j in hits):
            raise Refusal("ci-job", f"required job {name} did not succeed")


# ---------------------------------------------------------------- evaluation


def main_sha(client: Client, repo: str) -> str:
    """Return the current SHA of main."""
    sha = str((api_get(client, f"/repos/{repo}/git/ref/heads/{BASE_BRANCH}").get("object") or {}).get("sha", ""))
    if not SHA.fullmatch(sha):
        raise Refusal("api-error", "main ref has no SHA")
    return sha


def evaluate(client: Client, ctx: Context, pr_number: int) -> Decision:
    """Run every rule in order. Read-only. Every fact after the first read is pinned to X."""
    d = Decision(False, "internal", "not evaluated", pr_number=pr_number)
    try:
        pr = api_get(client, f"/repos/{ctx.repo}/pulls/{pr_number}")
        raw_head = str((pr.get("head") or {}).get("sha", ""))
        d.head_sha = raw_head if SHA.fullmatch(raw_head) else None  # for the refusal record
        d.head_sha = check_pr_shape(pr, ctx.repo)
        check_stop_switch(ctx.stop_value)
        d.main_sha = main_sha(client, ctx.repo)
        mandate, d.mandate_blob_sha = load_mandate(client, ctx.repo, d.main_sha)
        check_mandate_active(mandate, ctx.now)
        check_branch_and_label(pr, mandate)
        changes, compare_files = load_change_set(client, ctx.repo, d.main_sha, d.head_sha)
        check_change_set(changes, compare_files, mandate)
        open_prs = paginate(client, f"/repos/{ctx.repo}/pulls?state=open")
        closed_prs = paginate(client, f"/repos/{ctx.repo}/pulls?state=closed")
        check_counters(open_prs, closed_prs, pr_number, mandate, ctx.now)
        wf = mandate["required_ci_workflow"]
        runs = paginate(client, f"/repos/{ctx.repo}/actions/workflows/{wf}/runs"
                                f"?head_sha={d.head_sha}&event=pull_request")
        run = select_ci_run(runs, d.head_sha, ctx.repo, wf, pr_number)
        jobs = paginate(client, f"/repos/{ctx.repo}/actions/runs/{run['id']}/jobs?filter=latest")
        check_ci_jobs(jobs, mandate["required_ci_jobs"])
    except Refusal as exc:
        d.rule, d.detail = exc.rule, exc.detail
        return d
    except Exception as exc:  # noqa: BLE001 - keep the head SHA so the failing check is posted
        d.rule, d.detail = "internal-error", f"{type(exc).__name__}: {exc}"
        return d
    d.approved, d.rule, d.detail = True, "all-rules-passed", "all rules passed"
    return d


# ---------------------------------------------------------------- writes


def set_check(client: Client, repo: str, sha: str, success: bool, summary: str) -> None:
    """Create a completed check run named autonomy-gate on the head SHA."""
    body = {
        "name": CHECK_NAME, "head_sha": sha, "status": "completed",
        "conclusion": "success" if success else "failure",
        "output": {"title": "approved" if success else "not approved", "summary": summary[:60000]},
    }
    resp = client.request("POST", f"/repos/{repo}/check-runs", body)
    if resp.status != 201:
        raise RuntimeError(f"create check run returned HTTP {resp.status}")


def upsert_comment(client: Client, repo: str, pr_number: int, text: str) -> None:
    """Edit the gate's own earlier comment, or add one if none exists."""
    body = {"body": f"{COMMENT_MARKER}\n{text}"}
    comments = paginate(client, f"/repos/{repo}/issues/{pr_number}/comments")
    own = [c for c in comments
           if (c.get("user") or {}).get("login") == BOT_LOGIN and COMMENT_MARKER in str(c.get("body", ""))]
    if own:
        client.request("PATCH", f"/repos/{repo}/issues/comments/{own[0]['id']}", body)
    else:
        client.request("POST", f"/repos/{repo}/issues/{pr_number}/comments", body)


def dismiss_gate_approvals(client: Client, repo: str, pr_number: int, reason: str) -> list[str]:
    """Try to dismiss EVERY approval the gate gave. Return the errors; never stop early."""
    try:
        reviews = paginate(client, f"/repos/{repo}/pulls/{pr_number}/reviews")
    except Exception as exc:  # noqa: BLE001 - collected and raised by the caller
        return [f"dismissal: cannot list reviews: {type(exc).__name__}: {exc}"]
    errors: list[str] = []
    for review in reviews:
        if (review.get("user") or {}).get("login") != BOT_LOGIN or review.get("state") != "APPROVED":
            continue
        try:
            resp = client.request("PUT", f"/repos/{repo}/pulls/{pr_number}/reviews/{review['id']}/dismissals",
                                  {"message": f"autonomy-gate: {reason}", "event": "DISMISS"})
            if resp.status != 200:
                errors.append(f"dismissal of review {review['id']} returned HTTP {resp.status}")
        except Exception as exc:  # noqa: BLE001 - try the next review
            errors.append(f"dismissal of review {review.get('id')} raised {type(exc).__name__}: {exc}")
    return errors


def withdraw(client: Client, repo: str, pr_number: int | None, head_sha: str | None,
             summary: str, reason: str) -> list[str]:
    """Failing check FIRST, then every dismissal. Return the collected errors."""
    errors: list[str] = []
    if head_sha:
        try:
            set_check(client, repo, head_sha, False, summary)
        except Exception as exc:  # noqa: BLE001 - still dismiss
            errors.append(f"failing check: {type(exc).__name__}: {exc}")
    if pr_number is not None:
        errors.extend(dismiss_gate_approvals(client, repo, pr_number, reason))
    return errors


def pr_is_finished(client: Client, repo: str, pr_number: int) -> bool:
    """True if the pull request is merged or closed. If the read fails, return False (act)."""
    try:
        pr = api_get(client, f"/repos/{repo}/pulls/{pr_number}")
    except Exception:  # noqa: BLE001 - unknown state: the safe side is to write
        return False
    return isinstance(pr, dict) and (pr.get("merged") is True or pr.get("state") != "open")


def refuse(client: Client, ctx: Context, d: Decision) -> None:
    """Record a refusal: failing check, every dismissal, one updated comment. Raise on errors.

    A merged or closed pull request gets no writes at all.
    """
    assert not d.approved, "refuse() needs a refusal"
    if d.rule == "not-open":
        return
    if d.pr_number is not None and pr_is_finished(client, ctx.repo, d.pr_number):
        return
    text = (f"**autonomy-gate refused** this pull request.\n\n"
            f"- rule: `{d.rule}`\n- detail: {d.detail}\n- head SHA: `{d.head_sha}`\n"
            f"- mandate blob SHA: `{d.mandate_blob_sha}`\n- evaluated at: {ctx.now.isoformat()}")
    # Order matters: the failing check first, so a failed dismissal cannot leave
    # an approval next to a newest check of success.
    errors = withdraw(client, ctx.repo, d.pr_number, d.head_sha, text, f"refused by rule {d.rule}")
    if d.pr_number is not None:
        try:
            upsert_comment(client, ctx.repo, d.pr_number, text)
        except Exception as exc:  # noqa: BLE001 - collected
            errors.append(f"comment: {type(exc).__name__}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))


def recheck_before_approval(client: Client, ctx: Context, d: Decision) -> None:
    """Re-read head, label, branch, main and the mandate. Any change is a refusal.

    Removing the label is a per-pull-request stop that needs no admin rights.
    """
    pr = api_get(client, f"/repos/{ctx.repo}/pulls/{d.pr_number}")
    if (pr.get("head") or {}).get("sha") != d.head_sha:
        raise Refusal("head-moved", "head changed during evaluation")
    check_pr_shape(pr, ctx.repo)
    if main_sha(client, ctx.repo) != d.main_sha:
        raise Refusal("base-moved", "main changed during evaluation")
    assert d.main_sha is not None, "an approved decision has a main SHA"
    mandate, blob_sha = load_mandate(client, ctx.repo, d.main_sha)
    if blob_sha != d.mandate_blob_sha:
        raise Refusal("mandate-changed", "mandate changed during evaluation")
    check_mandate_active(mandate, ctx.clock())
    check_branch_and_label(pr, mandate)


def approve_and_merge(client: Client, ctx: Context, d: Decision) -> Decision:
    """Pending check, re-check, approve, success check, merge with sha = X. No retry."""
    assert d.approved and d.pr_number is not None and d.head_sha, "need an approved decision"
    fail = lambda rule, detail: Decision(False, rule, detail, d.pr_number, d.head_sha, d.mandate_blob_sha, d.main_sha)  # noqa: E731
    set_check(client, ctx.repo, d.head_sha, False, "merge in progress; not approved yet")
    try:
        recheck_before_approval(client, ctx, d)
    except Refusal as exc:
        return fail(exc.rule, exc.detail)
    ctx.journal({"pr": d.pr_number, "head": d.head_sha, "merged": False})
    review = client.request("POST", f"/repos/{ctx.repo}/pulls/{d.pr_number}/reviews", {
        "commit_id": d.head_sha, "event": "APPROVE",
        "body": f"{COMMENT_MARKER}\nautonomy-gate approves head `{d.head_sha}` "
                f"under mandate blob `{d.mandate_blob_sha}`.",
    })
    if review.status != 200:
        return fail("approve-failed", f"review returned HTTP {review.status}")
    set_check(client, ctx.repo, d.head_sha, True,
              f"All rules passed for head {d.head_sha} under mandate blob {d.mandate_blob_sha}.")
    merge = client.request("PUT", f"/repos/{ctx.repo}/pulls/{d.pr_number}/merge", {
        "sha": d.head_sha, "merge_method": "squash",
        "commit_title": f"autonomy: merge #{d.pr_number} at {d.head_sha[:12]}",
    })
    if merge.status == 200 and (merge.body or {}).get("merged") is True:
        ctx.journal({"pr": d.pr_number, "head": d.head_sha, "merged": True})
        return d
    rule = "head-moved" if merge.status == 409 else "merge-refused"
    return fail(rule, f"merge endpoint returned HTTP {merge.status}; the gate does not retry")


def run(client: Client, ctx: Context) -> Decision | None:
    """Evaluate one pull request and act on the result. Return the final Decision."""
    try:
        pr_number = resolve_pr_number(ctx, client)
    except Refusal as exc:
        return Decision(False, exc.rule, exc.detail)
    if pr_number is None:
        return None
    decision = evaluate(client, ctx, pr_number)  # never raises: errors become internal-error
    if decision.approved:
        try:
            decision = approve_and_merge(client, ctx, decision)
        except Exception as exc:  # noqa: BLE001 - fail closed; undo any approval below
            decision.approved = False
            decision.rule, decision.detail = "internal-error", f"{type(exc).__name__}: {exc}"
    if not decision.approved:
        refuse(client, ctx, decision)
    return decision


CLEANUP_SUMMARY = "merge not confirmed; approval withdrawn by cleanup"


def cleanup(client: Client, repo: str, state: dict[str, Any] | None) -> bool:
    """If an approval may exist and the merge is not confirmed, undo it. Return True if undone.

    `state` is {"pr": int, "head": SHA or None, "merged": bool}. The pull request
    is read first: merged or closed means no writes (this covers a merge that the
    server completed after the client timed out). Errors are raised at the end.
    """
    if not state or state.get("merged") is True:
        return False
    pr_number = int(state["pr"])
    head = state.get("head")
    try:
        pr = api_get(client, f"/repos/{repo}/pulls/{pr_number}")
    except Exception:  # noqa: BLE001 - unknown state: do the safe actions
        pr = None
    if isinstance(pr, dict):
        if pr.get("merged") is True or pr.get("state") != "open":
            return False
        if head is None:
            head = str((pr.get("head") or {}).get("sha", ""))
    head = head if isinstance(head, str) and SHA.fullmatch(head) else None
    errors = withdraw(client, repo, pr_number, head, CLEANUP_SUMMARY, "merge not confirmed; approval withdrawn")
    if head is None:
        errors.append("no head SHA known; no failing check posted")
    if errors:
        raise RuntimeError("; ".join(errors))
    return True


# ---------------------------------------------------------------- real client


class RestClient:
    """Minimal GitHub REST client on the standard library.

    The gate needs exact HTTP status codes (409 head moved, 405 not mergeable).
    `gh api` reports every HTTP error as exit code 1, so this client uses urllib.
    """

    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        assert token, "token must not be empty"
        self._token = token
        self._base = base_url.rstrip("/")

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Response:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self._base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "autonomy-gate",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return Response(resp.status, json.loads(raw) if raw else None)
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                parsed = json.loads(raw) if raw else None
            except ValueError:
                parsed = None
            return Response(err.code, parsed)


def state_path() -> str:
    return os.path.join(os.environ["RUNNER_TEMP"], STATE_FILE)


def write_state(state: dict[str, Any]) -> None:
    """Write the journal atomically: a temporary file, then a rename."""
    tmp = state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(tmp, state_path())


def safe_log(text: str) -> str:
    """Remove workflow-command prefixes and control characters from API-derived text."""
    return LOG_UNSAFE.sub(" ", text)[:2000]


def parse_state(text: str) -> dict[str, Any]:
    """Validate the journal. Any defect raises ValueError."""
    state = json.loads(text)
    if not isinstance(state, dict) or not isinstance(state.get("merged"), bool):
        raise ValueError("state is not an object with a boolean 'merged'")
    pr, head = state.get("pr"), state.get("head")
    if not isinstance(pr, int) or isinstance(pr, bool) or not isinstance(head, str) or not SHA.fullmatch(head):
        raise ValueError("state has no valid pr number or head SHA")
    return {"pr": pr, "head": head, "merged": state["merged"]}


def run_cleanup(client: Client, repo: str, text: str | None,
                fallback_pr: Callable[[], int | None]) -> int:
    """Cleanup step logic. Return the exit code. A defect or a failed undo exits 1.

    A corrupt state file means an approval MAY exist. The pull request number
    then comes from the event, the head SHA from the pull request, and the safe
    actions still run. The step still fails, so the defect is visible.
    """
    if text is None:
        print("autonomy-gate cleanup: no approval was given")
        return 0
    corrupt = False
    try:
        state: dict[str, Any] | None = parse_state(text)
    except (ValueError, RecursionError) as exc:
        corrupt = True
        print(safe_log(f"autonomy-gate cleanup: state file is corrupt: {exc}"), file=sys.stderr)
        number = fallback_pr()
        state = {"pr": number, "head": None, "merged": False} if number is not None else None
    if state is None:
        print("autonomy-gate cleanup FAILED: corrupt state and no pull request in the event", file=sys.stderr)
        return 1
    try:
        undone = cleanup(client, repo, state)
    except Exception as exc:  # noqa: BLE001 - report loudly
        print(safe_log(f"autonomy-gate cleanup FAILED: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return 1
    print(f"autonomy-gate cleanup: approval withdrawn={undone}")
    return 1 if corrupt else 0


def read_text(path: str) -> str | None:
    """Return the file text, or None if the file does not exist."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(65536)
    except FileNotFoundError:
        return None


def event_context(repo: str, journal: Callable[[dict[str, Any]], None]) -> Context:
    """Build the Context from the runner environment."""
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        event = json.load(handle)
    return Context(repo=repo, event_name=os.environ["GITHUB_EVENT_NAME"], event=event,
                   stop_value=os.environ.get("AUTONOMY_ENABLED", ""), now=utc_now(),
                   ref=os.environ.get("GITHUB_REF", ""), journal=journal)


def exit_code(decision: Decision | None) -> int:
    """0 for a clean decision or no work. 1 for an internal error or a bad dispatch ref."""
    if decision is None:
        return 0
    return 1 if decision.rule in ("internal-error", "dispatch-ref") else 0


def main(argv: list[str]) -> int:
    """Entry point for the workflow. Any unexpected exception exits 1."""
    try:
        repo = os.environ["GITHUB_REPOSITORY"]
        client = RestClient(os.environ["GITHUB_TOKEN"])
        if argv[1:] == ["--cleanup"]:
            def fallback() -> int | None:
                try:
                    return resolve_pr_number(event_context(repo, no_journal), client)
                except Exception:  # noqa: BLE001 - no fallback number
                    return None
            return run_cleanup(client, repo, read_text(state_path()), fallback)
        decision = run(client, event_context(repo, write_state))
    except Exception as exc:  # noqa: BLE001 - fail closed on any error
        print(safe_log(f"autonomy-gate internal error: {type(exc).__name__}: {exc}"), file=sys.stderr)
        return 1
    if decision is None:
        print("autonomy-gate: no pull request to evaluate for this event")
        return 0
    print(safe_log(f"autonomy-gate: pr={decision.pr_number} head={decision.head_sha} "
                   f"approved_and_merged={decision.approved} rule={decision.rule} detail={decision.detail}"))
    return exit_code(decision)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
