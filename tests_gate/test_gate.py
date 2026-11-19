"""Unit tests for the autonomy gate. All data is synthetic. No network.

The fake client answers by SHA: trees and compares are keyed by commit SHA,
and successive reads of the pull request can return different heads. These
tests prove the gate's logic against the fake only. They do not prove how
GitHub itself answers; see docs/NEGATIVE-TESTS.md for that.
"""

from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any

import pytest

import gate

REPO = "example-owner/example-repo"
PR = 7
MAIN = "d" * 40
HEAD = "a" * 40
OTHER = "b" * 40
BLOB = "c" * 40
GATE_BOT = "example-gate-app[bot]"
NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)
MANDATE = json.loads(
    (pathlib.Path(__file__).resolve().parent.parent / ".github" / "autonomy" / "mandate.json").read_text()
)


def entry(path: str, size: int = 500, mode: str = "100644", type_: str = "blob", salt: str = "") -> dict[str, Any]:
    sha = hashlib.sha1((path + salt).encode()).hexdigest()
    return {"path": path, "mode": mode, "type": type_, "size": size, "sha": sha}


BASE_TREE = [entry(p) for p in ("shapes/units.py", "tests/test_geometry.py",
                                ".github/autonomy/mandate.json", "pyproject.toml")]


def make_pr(number: int = PR, ref: str = "autonomy/tests-units", labels: tuple[str, ...] = ("autonomy",),
            merged_at: str | None = None) -> dict[str, Any]:
    return {
        "number": number, "state": "open", "draft": False, "merged_at": merged_at,
        "head": {"sha": HEAD, "ref": ref, "repo": {"full_name": REPO}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
        "labels": [{"name": n} for n in labels],
    }


def default_run() -> dict[str, Any]:
    return {"id": 900, "run_number": 3, "head_sha": HEAD, "event": "pull_request",
            "path": ".github/workflows/ci.yml", "head_repository": {"full_name": REPO},
            "pull_requests": [{"number": PR, "base": {"ref": "main"}}],
            "status": "completed", "conclusion": "success"}


@dataclass
class World:
    pr: dict[str, Any] = field(default_factory=make_pr)
    pr_heads: list[str] = field(default_factory=lambda: [HEAD])
    main_heads: list[str] = field(default_factory=lambda: [MAIN])
    trees: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    truncated: dict[str, bool] = field(default_factory=dict)
    compares: dict[str, dict[str, Any]] = field(default_factory=dict)
    open_prs: list[dict[str, Any]] = field(default_factory=lambda: [make_pr()])
    closed_prs: list[dict[str, Any]] = field(default_factory=list)
    runs: list[dict[str, Any]] = field(default_factory=lambda: [default_run()])
    jobs: Any = field(default_factory=lambda: [{"name": "tests", "conclusion": "success"},
                                               {"name": "gate-tests", "conclusion": "success"}])
    mandate: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(MANDATE))
    mandate_blobs: list[str] = field(default_factory=lambda: [BLOB])
    mandate_text: str | None = None
    pr_labels: list[list[str]] | None = None  # successive label sets of the pull request
    merged: bool = False  # the server state of the pull request
    merged_on_merge_call: bool = False  # the merge call merges, then raises (client timeout)
    closed_on_merge_call: bool = False  # the agent closes the pull request just before the merge call
    raise_on: str = ""  # a write path suffix that raises TimeoutError
    comments: list[dict[str, Any]] = field(default_factory=list)
    check_statuses: list[int] = field(default_factory=list)  # successive check-run statuses; then 201
    merge_status: int = 200
    sha_pulls: list[dict[str, Any]] = field(default_factory=lambda: [make_pr()])
    list_pages: int = 1

    def __post_init__(self) -> None:
        self.trees.setdefault(MAIN, copy.deepcopy(BASE_TREE))
        self.trees.setdefault(HEAD, copy.deepcopy(BASE_TREE))
        self.compares.setdefault(HEAD, {"merge_base_commit": {"sha": MAIN}, "files": []})
        if not self.compares[HEAD]["files"]:
            add_file(self, "tests/test_units.py")


def add_file(w: World, path: str, sha: str = HEAD, additions: int = 40, deletions: int = 0,
             size: int = 900, mode: str = "100644", type_: str = "blob", patch: bool = True,
             status: str = "added") -> None:
    """Add a changed path to the head tree and the compare of `sha`."""
    w.trees.setdefault(sha, copy.deepcopy(BASE_TREE))
    w.trees[sha] = [e for e in w.trees[sha] if e["path"] != path] + [entry(path, size, mode, type_, salt=sha)]
    w.compares.setdefault(sha, {"merge_base_commit": {"sha": MAIN}, "files": []})
    item: dict[str, Any] = {"filename": path, "status": status, "additions": additions, "deletions": deletions}
    if patch:
        item["patch"] = "@@ -0,0 +1 @@\n+x"
    w.compares[sha]["files"].append(item)


class FakeClient:
    """Answers the REST paths the gate uses. Records every call."""

    def __init__(self, world: World) -> None:
        self.w = world
        self.calls: list[tuple[str, str, Any]] = []
        self.reads: dict[str, int] = {}

    def _next(self, key: str, seq: list[str]) -> str:
        i = self.reads.get(key, 0)
        self.reads[key] = i + 1
        return seq[min(i, len(seq) - 1)]

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> gate.Response:
        self.calls.append((method, path, body))
        base = path.split("?", 1)[0]
        if method == "GET":
            return self._get(base, path)
        if base.endswith("/merge") and self.w.closed_on_merge_call:
            self.w.pr["state"] = "closed"
            return gate.Response(405, {"message": "Pull Request is not mergeable"})
        if base.endswith("/merge") and self.w.merged_on_merge_call:
            self.w.merged = True
            raise TimeoutError("The read operation timed out")
        if self.w.raise_on and method != "GET" and base.endswith(self.w.raise_on):
            raise TimeoutError("The read operation timed out")
        if base.endswith("/merge"):
            return gate.Response(self.w.merge_status, {"merged": self.w.merge_status == 200})
        if base.endswith("/check-runs") and self.w.check_statuses:
            return gate.Response(self.w.check_statuses.pop(0), {})
        return gate.Response({"POST": 201, "PATCH": 200, "PUT": 200}[method], {})

    def _get(self, base: str, path: str) -> gate.Response:
        w, r = self.w, f"/repos/{REPO}"
        page = int(path.split("page=")[-1]) if "&page=" in path else 1
        if base == f"{r}/pulls/{PR}":
            pr = copy.deepcopy(w.pr)
            pr["head"]["sha"] = self._next("pr", w.pr_heads)
            if w.pr_labels is not None:
                i = min(self.reads["pr"] - 1, len(w.pr_labels) - 1)
                pr["labels"] = [{"name": n} for n in w.pr_labels[i]]
            if w.merged:
                pr.update(state="closed", merged=True)
            return gate.Response(200, pr)
        if base == f"{r}/git/ref/heads/main":
            return gate.Response(200, {"object": {"sha": self._next("main", w.main_heads)}})
        if base == f"{r}/contents/.github/autonomy/mandate.json":
            ref = path.split("?ref=", 1)[1]
            assert ref in w.main_heads, "mandate must come from a pinned main SHA"
            text = w.mandate_text if w.mandate_text is not None else json.dumps(w.mandate)
            content = base64.b64encode(text.encode()).decode()
            return gate.Response(200, {"encoding": "base64", "content": content,
                                       "sha": self._next("mandate", w.mandate_blobs)})
        if base.startswith(f"{r}/compare/"):
            _, head = base.removeprefix(f"{r}/compare/").split("...")
            return gate.Response(200, copy.deepcopy(w.compares[head])) if head in w.compares else gate.Response(404, {})
        if base.startswith(f"{r}/git/trees/"):
            sha = base.removeprefix(f"{r}/git/trees/")
            assert path.endswith("?recursive=1")
            if sha not in w.trees:
                return gate.Response(404, {})
            return gate.Response(200, {"sha": sha, "tree": copy.deepcopy(w.trees[sha]),
                                       "truncated": w.truncated.get(sha, False)})
        if base == f"{r}/pulls":
            if page < w.list_pages:
                return gate.Response(200, [make_pr(number=1000 + i, ref="x", labels=()) for i in range(100)])
            return gate.Response(200, w.open_prs if "state=open" in path else w.closed_prs)
        table: dict[str, Any] = {
            f"{r}/actions/workflows/ci.yml/runs": {"workflow_runs": w.runs},
            f"{r}/actions/runs/900/jobs": {"jobs": w.jobs},
            f"{r}/issues/{PR}/comments": w.comments,
            f"{r}/commits/{HEAD}/pulls": w.sha_pulls,
            # Present only to prove the gate never uses the unpinned file list.
            f"{r}/pulls/{PR}/files": [{"filename": "tests/test_units.py", "status": "added", "additions": 1}],
        }
        if base in table:
            return gate.Response(200, table[base])
        return gate.Response(404, {"message": "not found"})

    def wrote(self, method: str, suffix: str) -> list[Any]:
        return [b for m, p, b in self.calls if m == method and p.split("?")[0].endswith(suffix)]


def ctx(stop: str = "true", event_name: str = "workflow_dispatch", event: dict[str, Any] | None = None,
        ref: str = "refs/heads/main", clock: Any = None, journal: Any = None,
        dry_run: bool = False) -> gate.Context:
    return gate.Context(repo=REPO, event_name=event_name,
                        event=event if event is not None else {"inputs": {"pr_number": str(PR)}},
                        stop_value=stop, now=NOW, ref=ref, clock=clock or (lambda: NOW),
                        journal=journal or gate.no_journal, bot_login=GATE_BOT, dry_run=dry_run)


def run(world: World, **kwargs: Any) -> tuple[gate.Decision | None, FakeClient]:
    client = FakeClient(world)
    return gate.run(client, ctx(**kwargs)), client


def assert_refused(world: World, rule: str, **kwargs: Any) -> FakeClient:
    decision, client = run(world, **kwargs)
    assert decision is not None and not decision.approved
    assert decision.rule == rule, decision.detail
    assert not client.wrote("POST", "/reviews"), "must never review"
    assert not client.wrote("PUT", "/merge"), "must never merge"
    checks = client.wrote("POST", "/check-runs")
    assert checks and checks[-1]["conclusion"] == "failure"
    assert not any(c["conclusion"] == "success" for c in checks)
    return client


# ------------------------------------------------------------- happy path


def test_happy_path_order_and_exact_sha() -> None:
    journal: list[dict[str, Any]] = []
    decision, client = run(World(), journal=journal.append)
    assert decision is not None and decision.approved
    assert not client.wrote("POST", "/reviews"), "the gate submits no review"
    success = client.wrote("POST", "/check-runs")[-1]
    assert success["conclusion"] == "success" and success["head_sha"] == HEAD
    assert HEAD in success["output"]["summary"] and BLOB in success["output"]["summary"]
    merge = client.wrote("PUT", "/merge")[0]
    assert merge["sha"] == HEAD and merge["merge_method"] == "squash"
    writes = [(m, p.split("?")[0].rsplit("/", 1)[-1], (b or {}).get("conclusion"))
              for m, p, b in client.calls if m != "GET"]
    assert writes == [("POST", "check-runs", "failure"), ("POST", "check-runs", "success"),
                      ("PUT", "merge", None)]
    assert journal == [{"pr": PR, "head": HEAD, "merged": False}, {"pr": PR, "head": HEAD, "merged": True}]
    assert not [p for _, p, _ in client.calls if p.split("?")[0].endswith("/files")], "unpinned list used"


# ------------------------------------------------------------- pinned change set


def test_aba_race_head_x_has_evil_file() -> None:
    """Head X adds shapes/evil.py. The unpinned /files list (fake) shows only a test.

    The gate reads files only from compare and trees pinned to X, so it refuses.
    """
    w = World()
    add_file(w, "shapes/evil.py")
    w.pr_heads = [HEAD, HEAD]
    client = assert_refused(w, "path")
    assert all(f"{HEAD}" in p for _, p, _ in client.calls if "/compare/" in p)


def test_aba_head_changes_before_approval() -> None:
    w = World()
    w.pr_heads = [HEAD, OTHER]
    decision, client = run(w)
    assert decision is not None and decision.rule == "head-moved"
    assert not client.wrote("POST", "/reviews") and not client.wrote("PUT", "/merge")


def test_tree_diff_must_match_compare() -> None:
    w = World()
    w.trees[HEAD].append(entry("tests/test_hidden.py"))
    assert_refused(w, "files")


def test_truncated_tree() -> None:
    w = World()
    w.truncated[HEAD] = True
    assert_refused(w, "tree")


def test_compare_at_file_cap() -> None:
    w = World()
    w.compares[HEAD]["files"] = [{"filename": f"tests/test_{i}.py", "status": "added"} for i in range(300)]
    assert_refused(w, "files")


@pytest.mark.parametrize("filename", [
    "shapes/units2.py", "README.md", "tests/helpers.py", "tests/test_x/conftest.py",
    "tests/sub/test_x.py", "tests/conftest.py", "tests_gate/test_new.py", "setup.py",
    ".github/workflows/x.yml", ".github/autonomy/new.json",
])
def test_path_outside_allowlist(filename: str) -> None:
    w = World()
    add_file(w, filename)
    assert_refused(w, "path")


def test_dotdot_segment() -> None:
    w = World()
    add_file(w, "tests/../test_x.py")
    assert_refused(w, "path")


def test_unsafe_file_name() -> None:
    w = World()
    add_file(w, "tests/test_$(id).py")
    assert_refused(w, "path")


def test_modified_existing_file() -> None:
    w = World()
    add_file(w, "tests/test_geometry.py", status="modified")
    assert_refused(w, "not-added")


def test_change_to_mandate() -> None:
    w = World()
    add_file(w, ".github/autonomy/mandate.json", status="modified")
    assert_refused(w, "path")


def test_removed_file() -> None:
    w = World()
    w.trees[HEAD] = [e for e in w.trees[HEAD] if e["path"] != "tests/test_geometry.py"]
    w.compares[HEAD]["files"].append({"filename": "tests/test_geometry.py", "status": "removed"})
    assert_refused(w, "not-added")


def test_rename_in_compare() -> None:
    w = World()
    w.compares[HEAD]["files"][0]["previous_filename"] = "tests/test_geometry.py"
    assert_refused(w, "not-added")


def test_added_file_reporting_deletions() -> None:
    w = World()
    w.compares[HEAD]["files"][0]["deletions"] = 1
    assert_refused(w, "too-many-lines")


@pytest.mark.parametrize(("mode", "type_"), [("120000", "blob"), ("160000", "commit"), ("100755", "blob")])
def test_symlink_submodule_executable_refused(mode: str, type_: str) -> None:
    w = World()
    w.trees[HEAD] = [e for e in w.trees[HEAD] if e["path"] != "tests/test_units.py"]
    w.trees[HEAD].append(entry("tests/test_units.py", mode=mode, type_=type_))
    assert_refused(w, "file-type")


def test_too_many_files() -> None:
    w = World()
    add_file(w, "tests/test_slug.py")
    add_file(w, "tests/test_roman.py")
    assert_refused(w, "too-many-files")


def test_too_many_lines() -> None:
    w = World()
    w.compares[HEAD]["files"][0]["additions"] = 201
    assert_refused(w, "too-many-lines")


def test_file_too_many_bytes() -> None:
    w = World()
    add_file(w, "tests/test_slug.py", size=MANDATE["max_bytes_per_file"] + 1, additions=1)
    assert_refused(w, "too-many-bytes")


def test_total_too_many_bytes() -> None:
    w = World()
    w.compares[HEAD]["files"] = []
    w.trees[HEAD] = copy.deepcopy(BASE_TREE)
    add_file(w, "tests/test_slug.py", size=15000)
    add_file(w, "tests/test_roman.py", size=15000)
    assert_refused(w, "too-many-bytes")


def test_empty_file_has_its_own_rule() -> None:
    w = World()
    w.trees[HEAD] = [e for e in w.trees[HEAD] if e["path"] != "tests/test_units.py"]
    w.trees[HEAD].append(entry("tests/test_units.py", size=0))
    w.compares[HEAD]["files"][0].pop("patch")
    w.compares[HEAD]["files"][0]["additions"] = 0
    assert_refused(w, "empty-file")


OLD_MAIN = "9" * 40


def test_head_behind_main_where_main_added_a_file() -> None:
    """Main moved on (added shapes/new.py) after the branch was cut. The merge base is old main."""
    w = World()
    w.trees[OLD_MAIN] = copy.deepcopy(BASE_TREE)
    w.trees[MAIN] = copy.deepcopy(BASE_TREE) + [entry("shapes/new.py")]
    w.compares[HEAD]["merge_base_commit"] = {"sha": OLD_MAIN}
    client = assert_refused(w, "not-up-to-date")
    assert not [p for _, p, _ in client.calls if "/git/trees/" in p], "refused before reading trees"


def test_criss_cross_merge_base_is_not_main() -> None:
    """A criss-cross history has a merge base other than main: the same rule applies."""
    w = World()
    w.compares[HEAD]["merge_base_commit"] = {"sha": OTHER}
    decision, _ = run(w)
    assert decision is not None and decision.rule == "not-up-to-date"
    assert isinstance(gate.Pending("not-up-to-date", ""), gate.Refusal)


def test_binary_or_huge_file_has_no_patch() -> None:
    w = World()
    w.compares[HEAD]["files"][0].pop("patch")
    w.compares[HEAD]["files"][0]["additions"] = 0
    assert_refused(w, "binary-or-large")


# ------------------------------------------------------------- branch, label, shape


def test_wrong_branch_prefix() -> None:
    w = World()
    w.pr["head"]["ref"] = "feature/tests-units"
    assert_refused(w, "branch-prefix")


def test_missing_label() -> None:
    w = World()
    w.pr["labels"] = []
    assert_refused(w, "label")


def test_fork_refused() -> None:
    w = World()
    w.pr["head"]["repo"] = {"full_name": "someone-else/example-repo"}
    assert_refused(w, "fork")


def test_wrong_base_refused() -> None:
    w = World()
    w.pr["base"]["ref"] = "develop"
    assert_refused(w, "wrong-base")


def writes(client: FakeClient) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _ in client.calls if m != "GET"]


def test_closed_unmerged_pr_gets_failing_check_and_no_comment() -> None:
    """A closed pull request can be reopened. It is not finished: post a failing check."""
    w = World()
    w.pr["state"] = "closed"
    decision, client = run(w)
    assert decision is not None and decision.rule == "not-open"
    assert [p.rsplit("/", 1)[-1] for _, p in writes(client)] == ["check-runs"]
    assert client.wrote("POST", "/check-runs")[-1]["conclusion"] == "failure"


def test_close_before_merge_leaves_failing_check() -> None:
    """Attack: the agent closes the PR after the success check and before the merge call."""
    w = World(closed_on_merge_call=True)
    decision, client = run(w)
    assert decision is not None and not decision.approved and decision.rule == "merge-refused"
    checks = client.wrote("POST", "/check-runs")
    assert [c["conclusion"] for c in checks] == ["failure", "success", "failure"]
    assert checks[-1]["head_sha"] == HEAD


def test_reopen_after_close_before_merge_has_no_success_check() -> None:
    """After the attack, the agent reopens. The newest gate check on X stays a failure."""
    w = World(closed_on_merge_call=True)
    _, first = run(w)
    w.pr["state"] = "open"
    w.closed_on_merge_call = False
    _, second = run(w, stop="false")  # the stop switch is off after the incident
    checks = first.wrote("POST", "/check-runs") + second.wrote("POST", "/check-runs")
    assert checks[-1]["conclusion"] == "failure" and checks[-1]["head_sha"] == HEAD
    assert not second.wrote("PUT", "/merge")


def test_cleanup_posts_failing_check_on_closed_unmerged_pr() -> None:
    w = World()
    w.pr["state"] = "closed"
    client = FakeClient(w)
    assert gate.cleanup(client, REPO, {"pr": PR, "head": HEAD, "merged": False}) is True
    assert client.wrote("POST", "/check-runs")[-1]["conclusion"] == "failure"


def test_merged_pr_makes_no_writes() -> None:
    """Re-running the gate after a merge: no comment, no check."""
    w = World(merged=True)
    decision, client = run(w)
    assert decision is not None and decision.rule == "not-open"
    assert writes(client) == []


def test_draft() -> None:
    w = World()
    w.pr["draft"] = True
    assert_refused(w, "draft")


def test_bad_head_sha() -> None:
    w = World()
    w.pr_heads = ["abc"]
    decision, client = run(w)
    assert decision is not None and decision.rule == "bad-head"
    assert not client.wrote("POST", "/reviews") and not client.wrote("POST", "/check-runs")


# ------------------------------------------------------------- stop switch and mandate


@pytest.mark.parametrize("value", ["", "false", "True", "true ", "1", "yes"])
def test_stop_variable_off(value: str) -> None:
    assert_refused(World(), "stop-switch", stop=value)


def test_mandate_disabled() -> None:
    w = World()
    w.mandate["enabled"] = False
    assert_refused(w, "mandate-disabled")


def test_mandate_enabled_must_be_bool() -> None:
    w = World()
    w.mandate["enabled"] = "true"
    assert_refused(w, "mandate-invalid")


def test_mandate_expired() -> None:
    w = World()
    w.mandate["enabled_until_utc"] = "2026-11-20T11:59:59Z"
    assert_refused(w, "mandate-expired")


def test_mandate_date_without_z() -> None:
    w = World()
    w.mandate["enabled_until_utc"] = "2026-12-05T00:00:00"
    assert_refused(w, "mandate-invalid")


def test_mandate_wrong_schema() -> None:
    w = World()
    w.mandate["schema"] = 2
    assert_refused(w, "mandate-invalid")


def test_mandate_malformed() -> None:
    w = World()
    del w.mandate["max_total_bytes"]
    assert_refused(w, "mandate-invalid")


def test_mandate_expires_between_evaluation_and_merge() -> None:
    late = dt.datetime(2026, 12, 5, 0, 0, tzinfo=dt.timezone.utc)
    decision, client = run(World(), clock=lambda: late)
    assert decision is not None and decision.rule == "mandate-expired"
    assert not client.wrote("POST", "/reviews") and not client.wrote("PUT", "/merge")


def test_mandate_changed_before_approval() -> None:
    w = World()
    w.mandate_blobs = [BLOB, "e" * 40]
    decision, client = run(w)
    assert decision is not None and decision.rule == "mandate-changed"
    assert not client.wrote("POST", "/reviews")


def test_label_removed_before_success_stops_this_pr() -> None:
    """NOTE 7: removing the label between evaluation and the success check is a per-PR stop."""
    w = World(pr_labels=[["autonomy"], []])
    decision, client = run(w)
    assert decision is not None and decision.rule == "label"
    assert not client.wrote("POST", "/reviews") and not client.wrote("PUT", "/merge")
    assert client.wrote("POST", "/check-runs")[-1]["conclusion"] == "failure"


def test_mandate_read_at_pinned_main_sha() -> None:
    _, client = run(World())
    refs = [p.split("?ref=", 1)[1] for _, p, _ in client.calls if "/contents/" in p]
    assert refs == [MAIN, MAIN]


VALID_TEXT = json.dumps(MANDATE)


@pytest.mark.parametrize("text", [
    VALID_TEXT[:-1] + ', "enabled": false}',                   # duplicate key
    VALID_TEXT[:-1] + ', "auto_approve_all": true}',           # unknown key
    VALID_TEXT.replace('"max_files": 2', '"max_files": "2"'),  # string where a number belongs
    VALID_TEXT.replace('"max_files": 2', '"max_files": 2.0'),  # float
    VALID_TEXT.replace('"max_files": 2', '"max_files": -1'),   # negative
    VALID_TEXT.replace('"max_files": 2', '"max_files": null'),  # null
    VALID_TEXT.replace('"max_files": 2', '"max_files": NaN'),  # NaN constant
    VALID_TEXT.replace('["tests/test_*.py"]', '["tests/test_*.py", 5]'),  # non-string glob
    VALID_TEXT.replace('["tests/test_*.py"]', '[]'),           # empty allowlist
    VALID_TEXT.replace('"max_total_merges": 5', '"max_total_merges": 100000000'),  # over-large
    VALID_TEXT.replace('"max_added_lines": 200', '"max_added_lines": ' + "9" * 5000),  # huge int
    VALID_TEXT.replace("2026-12-05T00:00:00Z", "2026-12-05T00:00:00+00:00Z"),  # offset and Z
    VALID_TEXT.replace('"ci.yml"', '"../ci.yml"'),             # workflow name with a path
    "[" * 100000,                                              # deep nesting
], ids=["duplicate", "unknown", "string", "float", "negative", "null", "nan", "non-string-glob",
        "empty-list", "over-large", "huge-int", "offset-and-z", "workflow-path", "deep-nesting"])
def test_mandate_text_invalid(text: str) -> None:
    w = World(mandate_text=text)
    assert_refused(w, "mandate-invalid")


class ContentsClient:
    """Answers only the contents call, with a fixed body."""

    def __init__(self, body: Any) -> None:
        self.body = body

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> gate.Response:
        return gate.Response(200, self.body)


@pytest.mark.parametrize("content", [
    "!!!not base64!!!", base64.b64encode(b"\xff\xfe{}").decode(),
    base64.b64encode(b" " * (gate.MAX_MANDATE_BYTES + 1)).decode(),
], ids=["not-base64", "not-utf8", "too-large"])
def test_mandate_undecodable_is_named_refusal(content: str) -> None:
    client = ContentsClient({"encoding": "base64", "sha": BLOB, "content": content})
    with pytest.raises(gate.Refusal) as info:
        gate.load_mandate(client, REPO, MAIN)
    assert info.value.rule == "mandate-invalid"


def test_mandate_contents_not_an_object() -> None:
    with pytest.raises(gate.Refusal) as info:
        gate.load_mandate(ContentsClient(["x"]), REPO, MAIN)
    assert info.value.rule == "mandate-invalid"


def test_valid_mandate_file_parses() -> None:
    assert gate.parse_mandate(VALID_TEXT)["required_ci_jobs"] == ["tests", "gate-tests"]


def test_gate_tests_job_required_even_if_mandate_omits_it() -> None:
    w = World()
    w.mandate["required_ci_jobs"] = ["tests"]
    w.jobs = [{"name": "tests", "conclusion": "success"}]
    assert_refused(w, "ci-job")


def test_main_moved_before_approval() -> None:
    w = World()
    w.main_heads = [MAIN, "f" * 40]
    decision, client = run(w)
    assert decision is not None and decision.rule == "base-moved"
    assert not client.wrote("POST", "/reviews")


# ------------------------------------------------------------- counters


def test_second_open_autonomy_pr() -> None:
    w = World()
    w.open_prs.append(make_pr(number=8, ref="autonomy/tests-slug", labels=()))
    assert_refused(w, "open-limit")


def test_max_open_prs_from_mandate() -> None:
    w = World()
    w.mandate["max_open_prs"] = 2
    w.open_prs.append(make_pr(number=8, ref="autonomy/tests-slug"))
    decision, _ = run(w)
    assert decision is not None and decision.approved


def test_merge_already_today() -> None:
    w = World()
    w.mandate["max_merges_per_utc_day"] = 1
    w.closed_prs = [make_pr(number=5, merged_at="2026-11-20T01:00:00Z")]
    assert_refused(w, "daily-limit")


def test_daily_cap_two_allows_second_merge() -> None:
    w = World()
    w.mandate["max_merges_per_utc_day"] = 2
    w.closed_prs = [make_pr(number=5, merged_at="2026-11-20T01:00:00Z")]
    decision, _ = run(w)
    assert decision is not None and decision.approved


def test_daily_cap_two_refuses_third_merge() -> None:
    w = World()
    w.mandate["max_merges_per_utc_day"] = 2
    w.closed_prs = [make_pr(number=n, merged_at=f"2026-11-20T0{n}:00:00Z") for n in (4, 5)]
    assert_refused(w, "daily-limit")


def test_merge_today_counted_even_if_label_removed() -> None:
    w = World()
    w.mandate["max_merges_per_utc_day"] = 1
    w.closed_prs = [make_pr(number=5, labels=(), merged_at="2026-11-20T01:00:00Z")]
    assert_refused(w, "daily-limit")


def test_total_ceiling_reached() -> None:
    w = World()
    w.closed_prs = [make_pr(number=n, merged_at=f"2026-11-0{n}T10:00:00Z") for n in range(1, 6)]
    assert_refused(w, "total-limit")


def test_closed_unmerged_prs_do_not_count() -> None:
    w = World()
    w.closed_prs = [make_pr(number=n) for n in range(1, 6)]
    decision, _ = run(w)
    assert decision is not None and decision.approved


def test_pagination_bound() -> None:
    w = World(list_pages=gate.MAX_PAGES + 1)
    assert_refused(w, "pagination-bound")


# ------------------------------------------------------------- CI evidence


def test_ci_run_for_different_sha() -> None:
    w = World()
    w.runs[0]["head_sha"] = OTHER
    assert_refused(w, "ci-missing")


def test_ci_run_from_other_workflow_file() -> None:
    w = World()
    w.runs[0]["path"] = ".github/workflows/fake.yml"
    assert_refused(w, "ci-missing")


def test_ci_run_from_push_event() -> None:
    w = World()
    w.runs[0]["event"] = "push"
    assert_refused(w, "ci-missing")


def test_ci_run_head_repository_mismatch() -> None:
    w = World()
    w.runs[0]["head_repository"] = {"full_name": "someone-else/example-repo"}
    assert_refused(w, "ci-missing")


@pytest.mark.parametrize("linked", [
    [], [{"number": 99, "base": {"ref": "main"}}], [{"number": PR, "base": {"ref": "develop"}}],
])
def test_ci_run_not_linked_to_this_pr_and_main(linked: list[dict[str, Any]]) -> None:
    w = World()
    w.runs[0]["pull_requests"] = linked
    assert_refused(w, "ci-unlinked")


def test_ci_failed() -> None:
    w = World()
    w.runs[0]["conclusion"] = "failure"
    assert_refused(w, "ci-failed")


def test_newest_ci_run_wins() -> None:
    w = World()
    w.runs.append(dict(w.runs[0], id=901, run_number=4, conclusion="failure"))
    assert_refused(w, "ci-failed")


def test_ci_still_running() -> None:
    w = World()
    w.runs[0].update(status="in_progress", conclusion=None)
    assert_refused(w, "ci-pending")


def test_required_job_missing() -> None:
    w = World()
    w.jobs = [{"name": "lint", "conclusion": "success"}]
    assert_refused(w, "ci-job")


def test_failing_duplicate_job() -> None:
    w = World()
    w.jobs = [{"name": "tests", "conclusion": "success"}, {"name": "tests", "conclusion": "failure"}]
    assert_refused(w, "ci-job")


def test_api_error_fails_closed() -> None:
    w = World()
    w.jobs = None
    assert_refused(w, "api-error")


# ------------------------------------------------------------- approval, merge, cleanup


def test_head_changed_at_merge_returns_409() -> None:
    journal: list[dict[str, Any]] = []
    decision, client = run(World(merge_status=409), journal=journal.append)
    assert decision is not None and not decision.approved and decision.rule == "head-moved"
    assert len(client.wrote("PUT", "/merge")) == 1, "must not retry"
    assert client.wrote("POST", "/check-runs")[-1]["conclusion"] == "failure"
    assert journal == [{"pr": PR, "head": HEAD, "merged": False}]


def test_gate_never_reviews_or_dismisses() -> None:
    """With no required review, the gate writes only check runs, comments and the merge."""
    for world in (World(), World(merge_status=405), World(pr_labels=[["autonomy"], []])):
        _, client = run(world)
        assert not [p for m, p, _ in client.calls if m != "GET" and ("/reviews" in p or "/dismissals" in p)]


def test_failed_merge_posts_failing_check_after_success() -> None:
    decision, client = run(World(merge_status=405))
    assert decision is not None and decision.rule == "merge-refused"
    checks = [c["conclusion"] for c in client.wrote("POST", "/check-runs")]
    assert checks == ["failure", "success", "failure"], "the newest gate check is a failure"
    assert client.wrote("POST", "/comments")


def test_failing_check_write_failure_is_loud_and_comment_still_written() -> None:
    client = FakeClient(World(merge_status=405, check_statuses=[201, 201, 500]))
    with pytest.raises(RuntimeError, match="failing check"):
        gate.run(client, ctx())
    assert client.wrote("POST", "/comments"), "the comment is still written"


def test_pending_check_failure_stops_before_success() -> None:
    """If the first (pending) check cannot be written, no success check and no merge follow."""
    client = FakeClient(World(check_statuses=[500]))
    decision = gate.run(client, ctx())
    assert decision is not None and decision.rule == "internal-error"
    assert not any(c["conclusion"] == "success" for c in client.wrote("POST", "/check-runs"))
    assert not client.wrote("PUT", "/merge")


def test_cleanup_posts_failing_check_when_merge_not_confirmed() -> None:
    """Simulates the `if: always()` step after a cancelled run (fake only)."""
    client = FakeClient(World())
    assert gate.cleanup(client, REPO, {"pr": PR, "head": HEAD, "merged": False}) is True
    assert [p.rsplit("/", 1)[-1] for _, p in writes(client)] == ["check-runs"]
    assert client.wrote("POST", "/check-runs")[-1] == {
        "name": "autonomy-gate", "head_sha": HEAD, "status": "completed", "conclusion": "failure",
        "output": {"title": "not approved", "summary": "merge not confirmed; success withdrawn by cleanup"}}


@pytest.mark.parametrize("state", [None, {"pr": PR, "head": HEAD, "merged": True}])
def test_cleanup_does_nothing_when_merged_or_no_success(state: Any) -> None:
    client = FakeClient(World())
    assert gate.cleanup(client, REPO, state) is False
    assert not client.calls


def test_cleanup_check_failure_raises() -> None:
    client = FakeClient(World(check_statuses=[500]))
    with pytest.raises(RuntimeError, match="check run"):
        gate.cleanup(client, REPO, {"pr": PR, "head": HEAD, "merged": False})


def test_cleanup_does_nothing_when_server_says_merged() -> None:
    """The journal says not merged, but the server merged after a client timeout."""
    w = World(merged=True)
    client = FakeClient(w)
    assert gate.cleanup(client, REPO, {"pr": PR, "head": HEAD, "merged": False}) is False
    assert writes(client) == []


# ------------------------------------------------------------- internal errors (NOTE 9)


def test_timeout_at_merge_call_that_did_not_merge() -> None:
    w = World(raise_on="/merge")
    decision, client = run(w)
    assert decision is not None and decision.rule == "internal-error" and "TimeoutError" in decision.detail
    assert gate.exit_code(decision) == 1
    assert client.wrote("POST", "/check-runs")[-1]["conclusion"] == "failure"


def test_timeout_at_merge_call_that_merged_makes_no_more_writes() -> None:
    w = World(merged_on_merge_call=True)
    decision, client = run(w)
    assert decision is not None and decision.rule == "internal-error" and gate.exit_code(decision) == 1
    last = writes(client)[-1]
    assert last[0] == "PUT" and last[1].endswith("/merge"), "no write after the merge call"


def test_non_refusal_exception_inside_evaluation_posts_failing_check() -> None:
    w = World()
    w.jobs = ["not-a-dict"]  # j.get(...) raises AttributeError
    decision, client = run(w)
    assert decision is not None and decision.rule == "internal-error" and "AttributeError" in decision.detail
    assert decision.head_sha == HEAD
    assert gate.exit_code(decision) == 1
    checks = client.wrote("POST", "/check-runs")
    assert checks and checks[-1]["conclusion"] == "failure" and checks[-1]["head_sha"] == HEAD
    assert not client.wrote("POST", "/reviews")


@pytest.mark.parametrize("text", ['{"pr": 7, "head": "aaa', "[]", '{"pr": "7", "head": null}', ""])
def test_cleanup_with_corrupt_state_still_withdraws_and_fails(text: str) -> None:
    client = FakeClient(World())
    assert gate.run_cleanup(client, REPO, text, lambda: PR) == 1
    check = client.wrote("POST", "/check-runs")[-1]
    assert check["conclusion"] == "failure" and check["head_sha"] == HEAD, "head taken from the pull request"


def test_cleanup_with_corrupt_state_and_no_event_pr_fails() -> None:
    client = FakeClient(World())
    assert gate.run_cleanup(client, REPO, "{broken", lambda: None) == 1
    assert writes(client) == []


def test_cleanup_without_state_file_is_clean() -> None:
    client = FakeClient(World())
    assert gate.run_cleanup(client, REPO, None, lambda: PR) == 0
    assert not client.calls


def test_main_exits_1_on_internal_error_and_journal_is_atomic(tmp_path: pathlib.Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(raise_on="/merge")
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"pr_number": str(PR)}}))
    for key, value in {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "x", "GITHUB_EVENT_PATH": str(event),
                       "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
                       "AUTONOMY_ENABLED": "true", "RUNNER_TEMP": str(tmp_path),
                       "GATE_TOKEN": "y", "GATE_APP_SLUG": "example-gate-app"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(gate, "RestClient", lambda _token: FakeClient(w))
    monkeypatch.setattr(gate, "utc_now", lambda: NOW)
    assert gate.main(["gate.py"]) == 1
    assert json.loads((tmp_path / gate.STATE_FILE).read_text()) == {"pr": PR, "head": HEAD, "merged": False}
    assert not (tmp_path / (gate.STATE_FILE + ".tmp")).exists()
    (tmp_path / gate.STATE_FILE).write_text('{"pr": 7, "he')  # partially written
    assert gate.main(["gate.py", "--cleanup"]) == 1


def test_main_exits_1_when_head_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    event = tmp_path / "event.json"
    event.write_text("{}")  # pull_request_target event without a pull_request: KeyError
    for key, value in {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "x", "GITHUB_EVENT_PATH": str(event),
                       "GITHUB_EVENT_NAME": "pull_request_target", "RUNNER_TEMP": str(tmp_path)}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(gate, "RestClient", lambda _token: FakeClient(World()))
    assert gate.main(["gate.py"]) == 1


def test_log_text_has_no_workflow_commands() -> None:
    text = gate.safe_log("ok\n::add-mask::x\r::set-output name=a::b\x1b[31m")
    assert "::" not in text and "\n" not in text and "\r" not in text and "\x1b" not in text


# ------------------------------------------------------------- comments and events


def test_existing_gate_comment_is_updated_not_duplicated() -> None:
    w = World()
    w.pr["labels"] = []
    w.comments = [{"id": 11, "user": {"login": "someone"}, "body": gate.COMMENT_MARKER},
                  {"id": 10, "user": {"login": "github-actions[bot]"}, "body": gate.COMMENT_MARKER},
                  {"id": 12, "user": {"login": GATE_BOT}, "body": gate.COMMENT_MARKER + "\nold"}]
    client = assert_refused(w, "label")
    assert [p for m, p, _ in client.calls if m == "PATCH"] == [f"/repos/{REPO}/issues/comments/12"]
    assert not client.wrote("POST", "/comments")


def test_refusal_comment_names_rule_and_sha() -> None:
    w = World()
    w.pr["labels"] = []
    body = assert_refused(w, "label").wrote("POST", "/comments")[0]["body"]
    assert "`label`" in body and HEAD in body


def test_dispatch_from_non_main_ref_refused() -> None:
    """gate.py half of the guard. The workflow `if:` half is in test_workflows.py."""
    decision, client = run(World(), ref="refs/heads/autonomy/tests-units")
    assert decision is not None and decision.rule == "dispatch-ref"
    assert not client.calls


def test_bad_dispatch_input() -> None:
    decision, client = run(World(), event={"inputs": {"pr_number": "7; rm -rf /"}})
    assert decision is not None and decision.rule == "bad-input"
    assert not client.calls


def test_pull_request_target_resolution() -> None:
    decision, _ = run(World(), event_name="pull_request_target", event={"pull_request": {"number": PR}},
                      ref="refs/heads/main")
    assert decision is not None and decision.approved and decision.pr_number == PR


def test_workflow_run_from_push_is_ignored() -> None:
    decision, client = run(World(), event_name="workflow_run", event={"workflow_run": {"event": "push"}})
    assert decision is None and not client.calls


def test_workflow_run_with_zero_linked_prs_uses_commit_lookup() -> None:
    event = {"workflow_run": {"event": "pull_request", "pull_requests": [], "head_sha": HEAD}}
    decision, _ = run(World(), event_name="workflow_run", event=event)
    assert decision is not None and decision.approved


def test_workflow_run_with_zero_linked_and_two_open_prs_does_nothing() -> None:
    w = World(sha_pulls=[make_pr(), make_pr(number=8)])
    event = {"workflow_run": {"event": "pull_request", "pull_requests": [], "head_sha": HEAD}}
    decision, client = run(w, event_name="workflow_run", event=event)
    assert decision is None and not client.wrote("POST", "/check-runs")


def test_workflow_run_with_multiple_linked_prs_does_nothing() -> None:
    event = {"workflow_run": {"event": "pull_request", "head_sha": HEAD,
                              "pull_requests": [{"number": PR}, {"number": 8}]}}
    decision, client = run(World(), event_name="workflow_run", event=event)
    assert decision is None and not client.calls


# ------------------------------------------------------------- pure helpers


@pytest.mark.parametrize(("path", "ok"), [
    ("tests/test_units.py", True), ("tests/test_.py", True), ("tests/test_a/b.py", False),
    ("Tests/test_a.py", False), ("tests/test_a.pyc", False), ("x/tests/test_a.py", False),
])
def test_path_matches_does_not_cross_slash(path: str, ok: bool) -> None:
    assert gate.path_matches(path, "tests/test_*.py") is ok


# ------------------------------------------------------------- gate app token and dry run


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> gate.Response:
        self.calls.append(method)
        return gate.Response(200, {})


def test_split_client_reads_with_workflow_token_and_writes_with_gate_token() -> None:
    reader, writer = Recorder(), Recorder()
    split = gate.SplitClient(reader, writer)
    for method in ("GET", "POST", "PATCH", "PUT", "GET"):
        split.request(method, "/x")
    assert reader.calls == ["GET", "GET"] and writer.calls == ["POST", "PATCH", "PUT"]


def test_no_write_client_refuses_every_write() -> None:
    with pytest.raises(RuntimeError, match="dry run"):
        gate.NoWriteClient().request("POST", "/repos/x/check-runs", {})


@pytest.mark.parametrize("world", [World(), World(pr_labels=[[]]), World(merge_status=405)],
                         ids=["valid", "refused", "merge-would-fail"])
def test_dry_run_evaluates_and_writes_nothing(world: World) -> None:
    client = FakeClient(world)
    decision = gate.run(client, ctx(dry_run=True))
    assert decision is not None
    assert writes(client) == []


def test_dry_run_reports_the_rule() -> None:
    w = World()
    w.pr["labels"] = []
    decision = gate.run(FakeClient(w), ctx(dry_run=True))
    assert decision is not None and decision.rule == "label" and not decision.approved


def set_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, **extra: str) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"pr_number": str(PR)}}))
    env = {"GITHUB_REPOSITORY": REPO, "GITHUB_TOKEN": "x", "GITHUB_EVENT_PATH": str(event),
           "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
           "AUTONOMY_ENABLED": "true", "RUNNER_TEMP": str(tmp_path)}
    env.update(extra)
    for key in ("GATE_TOKEN", "GATE_APP_SLUG"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_main_without_gate_token_is_a_dry_run(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    set_env(monkeypatch, tmp_path)
    clients: list[FakeClient] = []

    def make(_token: str) -> FakeClient:
        clients.append(FakeClient(World()))
        return clients[-1]

    monkeypatch.setattr(gate, "RestClient", make)
    monkeypatch.setattr(gate, "utc_now", lambda: NOW)
    assert gate.main(["gate.py"]) == 1
    assert "DRY RUN" in capsys.readouterr().out
    assert len(clients) == 1, "only the read-only client is built"
    assert writes(clients[0]) == []
    assert not (tmp_path / gate.STATE_FILE).exists()
    assert gate.main(["gate.py", "--cleanup"]) == 0, "no state file: nothing to withdraw"


@pytest.mark.parametrize("slug", ["", "Bad_Slug", "x" * 70, "a[bot]"])
def test_main_with_gate_token_needs_a_valid_slug(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path,
                                                 slug: str) -> None:
    set_env(monkeypatch, tmp_path, GATE_TOKEN="y", GATE_APP_SLUG=slug)
    monkeypatch.setattr(gate, "RestClient", lambda _token: FakeClient(World()))
    assert gate.main(["gate.py"]) == 1


def test_main_with_gate_token_uses_it_for_writes(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    set_env(monkeypatch, tmp_path, GATE_TOKEN="gate-token", GATE_APP_SLUG="example-gate-app")
    w = World()
    by_token: dict[str, FakeClient] = {}

    def make(token: str) -> FakeClient:
        by_token[token] = FakeClient(w)
        return by_token[token]

    monkeypatch.setattr(gate, "RestClient", make)
    monkeypatch.setattr(gate, "utc_now", lambda: NOW)
    assert gate.main(["gate.py"]) == 0
    assert all(m == "GET" for m, _, _ in by_token["x"].calls), "the workflow token never writes"
    kinds = [p.rsplit("/", 1)[-1] for _, p in writes(by_token["gate-token"])]
    assert kinds == ["check-runs", "check-runs", "merge"]


def test_check_run_needs_full_sha() -> None:
    with pytest.raises(AssertionError):
        gate.set_check(FakeClient(World()), REPO, "abc", False, "x")
