"""Static checks on the two workflow files. Needs PyYAML.

These tests read the YAML only. They cannot prove how GitHub runs it.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

WF = pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows"


def load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WF / name).read_text())
    data["on"] = data.pop(True)  # YAML 1.1 reads the key `on` as True
    return data


def test_gate_dispatch_only_from_main() -> None:
    job = load("autonomy-gate.yml")["jobs"]["gate"]
    assert job["if"] == "github.event_name != 'workflow_dispatch' || github.ref == 'refs/heads/main'"


def test_gate_checks_out_main_only_without_credentials() -> None:
    steps = load("autonomy-gate.yml")["jobs"]["gate"]["steps"]
    checkouts = [s for s in steps if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert len(checkouts) == 1
    assert checkouts[0]["with"]["ref"] == "refs/heads/main", "full ref: a tag named main cannot shadow it"
    assert checkouts[0]["with"]["persist-credentials"] is False


def test_gate_run_scripts_have_no_expressions() -> None:
    for step in load("autonomy-gate.yml")["jobs"]["gate"]["steps"]:
        assert "${{" not in str(step.get("run", ""))


def test_gate_permissions_are_minimal() -> None:
    data = load("autonomy-gate.yml")
    assert data["permissions"] == {}
    assert data["jobs"]["gate"]["permissions"] == {
        "contents": "read", "pull-requests": "read", "issues": "read", "actions": "read"}, "workflow token reads only"


def test_gate_has_always_cleanup_step_last() -> None:
    last = load("autonomy-gate.yml")["jobs"]["gate"]["steps"][-1]
    assert last["if"] == "always()" and last["run"].endswith("--cleanup")


def test_gate_has_one_job_level_lane_and_no_workflow_group() -> None:
    data = load("autonomy-gate.yml")
    assert "concurrency" not in data
    assert list(data["jobs"]) == ["gate"]
    assert data["jobs"]["gate"]["concurrency"] == {"group": "autonomy-gate-merge-lane", "cancel-in-progress": False}


def test_ci_is_read_only_and_main_based() -> None:
    data = load("ci.yml")
    assert data["permissions"] == {"contents": "read"}
    assert data["on"]["pull_request"] == {"branches": ["main"]}
    for name in ("tests", "gate-tests"):
        job = data["jobs"][name]
        assert job["name"] == name
        assert job["timeout-minutes"] == 10
        assert job["permissions"] == {"contents": "read"}
        assert job["steps"][0]["with"]["persist-credentials"] is False
    assert "secrets." not in (WF / "ci.yml").read_text()


def test_gate_tests_job_is_isolated() -> None:
    """The gate tests run on their own runner: no `needs`, never tests/, pinned installs only."""
    job = load("ci.yml")["jobs"]["gate-tests"]
    assert "needs" not in job
    runs = [str(s.get("run", "")) for s in job["steps"] if "run" in s]
    assert runs == ['python -m pip install "pytest==8.3.4" "pyyaml==6.0.2"',
                    "python -I -m pytest -q -p no:cacheprovider tests_gate"]
    package_job = load("ci.yml")["jobs"]["tests"]
    assert not any("tests_gate" in str(s.get("run", "")) for s in package_job["steps"])


def test_required_jobs_match_ci_job_names() -> None:
    mandate = json.loads((WF.parent / "autonomy" / "mandate.json").read_text())
    assert set(mandate["required_ci_jobs"]) == {j["name"] for j in load("ci.yml")["jobs"].values()}


def test_every_action_is_pinned_by_full_sha() -> None:
    for name in ("autonomy-gate.yml", "ci.yml"):
        for job in load(name)["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    ref = str(step["uses"]).split("@", 1)[1]
                    assert re.fullmatch(r"[0-9a-f]{40}", ref), step["uses"]


def test_gate_job_uses_main_only_environment_and_gate_app_token() -> None:
    job = load("autonomy-gate.yml")["jobs"]["gate"]
    assert job["environment"] == "autonomy-gate"
    mint = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/create-github-app-token@")]
    assert len(mint) == 1 and mint[0]["id"] == "gate-app"
    assert mint[0]["uses"].endswith("@64ab729a6294000f5aa4a4bc0b73d1f32bc45510")
    assert mint[0]["if"] == "env.GATE_APP_CONFIGURED == 'true'"
    assert mint[0]["with"] == {
        "client-id": "${{ secrets.GATE_APP_CLIENT_ID }}",
        "private-key": "${{ secrets.GATE_APP_PRIVATE_KEY }}",
        "permission-checks": "write", "permission-contents": "write", "permission-pull-requests": "write"}
    for step in job["steps"]:
        if "run" in step:
            env = step["env"]
            assert env["GITHUB_TOKEN"] == "${{ github.token }}"
            assert env["GATE_TOKEN"] == "${{ steps.gate-app.outputs.token }}"
            assert env["GATE_APP_SLUG"] == "${{ steps.gate-app.outputs.app-slug }}"
            assert not any("secrets." in str(v) for v in env.values()), "no secret reaches gate.py"


def test_secrets_appear_only_in_the_gate_job_env_flag_and_mint_step() -> None:
    text = (WF / "autonomy-gate.yml").read_text()
    uses = [line.strip() for line in text.splitlines() if "secrets." in line and not line.strip().startswith("#")]
    assert len(uses) == 3, uses
    assert "secrets." not in (WF / "ci.yml").read_text()


def test_gate_never_reviews() -> None:
    gate_py = (WF.parent / "autonomy" / "gate.py").read_text()
    assert "/reviews" not in gate_py and "/dismissals" not in gate_py
