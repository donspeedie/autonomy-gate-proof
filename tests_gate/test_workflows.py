"""Static checks on the two workflow files. Needs PyYAML.

These tests read the YAML only. They cannot prove how GitHub runs it.
"""

from __future__ import annotations

import json
import pathlib
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
    assert checkouts[0]["with"]["ref"] == "main"
    assert checkouts[0]["with"]["persist-credentials"] is False


def test_gate_run_scripts_have_no_expressions() -> None:
    for step in load("autonomy-gate.yml")["jobs"]["gate"]["steps"]:
        assert "${{" not in str(step.get("run", ""))


def test_gate_permissions_are_minimal() -> None:
    data = load("autonomy-gate.yml")
    assert data["permissions"] == {}
    assert data["jobs"]["gate"]["permissions"] == {
        "contents": "write", "pull-requests": "write", "checks": "write", "actions": "read"}


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
