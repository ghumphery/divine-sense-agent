"""Tests for v0.8.7+ ``cli.py schedules add --max-fires / --expires-at``
flags and the new STATUS column in ``schedules list``."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = str(Path(__file__).resolve().parents[1])


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("QDRANT_RAG_HOME", str(tmp_path))
    yield tmp_path


def _run_cli(*args, env_root):
    return subprocess.run(
        [sys.executable, "cli.py", *args],
        env={**os.environ, "QDRANT_RAG_HOME": str(env_root)},
        capture_output=True, text=True, cwd=REPO_ROOT,
    )


def test_cli_add_with_max_fires(cli_env):
    r = _run_cli(
        "schedules", "add",
        "--name", "lifecycle",
        "--prompt", "p",
        "--type", "interval_seconds",
        "--interval", "60",
        "--max-fires", "5",
        "--no-tools",
        env_root=cli_env,
    )
    assert r.returncode == 0, r.stderr
    json_files = list(Path(cli_env, "schedules").glob("*.json"))
    assert len(json_files) == 1
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert data["max_fires"] == 5


def test_cli_add_with_expires_at(cli_env):
    r = _run_cli(
        "schedules", "add",
        "--name", "expiring",
        "--prompt", "p",
        "--type", "interval_seconds",
        "--interval", "60",
        "--expires-at", "2027-06-01T00:00:00",
        "--no-tools",
        env_root=cli_env,
    )
    assert r.returncode == 0, r.stderr
    json_files = list(Path(cli_env, "schedules").glob("*.json"))
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert data["expires_at"] == "2027-06-01T00:00:00"


def test_cli_add_without_lifecycle_flags_still_works(cli_env):
    """Backward compat: pre-v0.8.7 invocations don't pass the new
    flags and must still create a schedule with ``max_fires=None``
    and ``expires_at=None``."""
    r = _run_cli(
        "schedules", "add",
        "--name", "legacy",
        "--prompt", "p",
        "--type", "interval_seconds",
        "--interval", "60",
        "--no-tools",
        env_root=cli_env,
    )
    assert r.returncode == 0, r.stderr
    json_files = list(Path(cli_env, "schedules").glob("*.json"))
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert data["max_fires"] is None
    assert data["expires_at"] is None


def test_cli_list_shows_status_column(cli_env):
    _run_cli(
        "schedules", "add",
        "--name", "active-row",
        "--prompt", "p",
        "--type", "interval_seconds",
        "--interval", "60",
        "--no-tools",
        env_root=cli_env,
    )
    r = _run_cli("schedules", "list", env_root=cli_env)
    assert r.returncode == 0
    assert "STATUS" in r.stdout
    assert "active" in r.stdout


def test_cli_add_rejects_bad_max_fires(cli_env):
    r = _run_cli(
        "schedules", "add",
        "--name", "bad",
        "--prompt", "p",
        "--type", "interval_seconds",
        "--interval", "60",
        "--max-fires", "0",
        "--no-tools",
        env_root=cli_env,
    )
    # argparse ``type=int`` accepts 0; validation happens up-front
    # in ``_handle_schedules`` so the user sees a clean error
    # rather than a no-op schedule.
    assert r.returncode != 0
    assert "--max-fires" in r.stderr or "max_fires" in r.stderr


def test_cli_show_includes_lifecycle_fields(cli_env):
    _run_cli(
        "schedules", "add",
        "--name", "show-me",
        "--prompt", "p",
        "--type", "interval_seconds",
        "--interval", "60",
        "--max-fires", "2",
        "--expires-at", "2027-01-01T00:00:00",
        "--no-tools",
        env_root=cli_env,
    )
    r = _run_cli("schedules", "list", env_root=cli_env)
    assert r.returncode == 0
    # Extract the id from list output
    sid = next(
        line.split()[0] for line in r.stdout.splitlines()
        if line.startswith(('a', 'b', 'c', 'd', 'e', 'f', '0', '1',
                            '2', '3', '4', '5', '6', '7', '8', '9'))
    )
    r = _run_cli("schedules", "show", sid, env_root=cli_env)
    assert "status" in r.stdout
    assert "max_fires" in r.stdout
    assert "expires_at" in r.stdout