"""Tests for raoc.agents.verification.VerificationAgent."""

from pathlib import Path

import pytest

from raoc import config
from raoc.agents.verification import VerificationAgent
from raoc.db.queries import create_job, get_job, update_job_field
from raoc.db.schema import create_tables, get_engine
from raoc.models.job import JobStatus
from raoc.substrate.host_sampler import HostSampler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    engine = get_engine(db_path=tmp_path / "test_verify.db")
    create_tables(engine)
    return engine


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "raoc_workspace"
    bk = ws / ".backups"
    ws.mkdir()
    bk.mkdir()
    monkeypatch.setattr(config, "WORKSPACE", ws)
    monkeypatch.setattr(config, "BACKUPS_DIR", bk)
    return ws


def _agent(db) -> VerificationAgent:
    from unittest.mock import MagicMock
    sampler = MagicMock(spec=HostSampler)
    return VerificationAgent(db=db, sampler=sampler)


def _make_execution_summary(job_id: str, actions: list[dict]) -> dict:
    return {
        "job_id": job_id,
        "steps_completed": len(actions),
        "steps_failed": 0,
        "actions": actions,
    }


def _action(atype: str, output: str | None = None, status: str = "succeeded") -> dict:
    return {"action_id": "a1", "step_index": 0, "action_type": atype,
            "status": status, "output": output}


# ---------------------------------------------------------------------------
# rewrite_file tests
# ---------------------------------------------------------------------------

class TestRewriteSuccess:
    """Successful rewrite: all_passed=True, correct before/after sizes."""

    def test_all_passed_true(self, db, workspace):
        target = workspace / "notes.txt"
        target.write_text("rewritten content")
        backup = config.BACKUPS_DIR / "notes.txt.bak"
        backup.write_text("original content")

        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path=str(target), engine=db)

        summary = _make_execution_summary(job.job_id, [
            _action("file_read", output="size=16"),
            _action("file_backup"),
            _action("file_write"),
        ])

        result = _agent(db).run(job.job_id, summary)

        assert result["all_passed"] is True

    def test_before_size_from_file_read(self, db, workspace):
        target = workspace / "notes.txt"
        target.write_text("new content")
        (config.BACKUPS_DIR / "notes.txt.bak").write_text("original")

        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path=str(target), engine=db)

        summary = _make_execution_summary(job.job_id, [
            _action("file_read", output="size=99"),
        ])

        result = _agent(db).run(job.job_id, summary)

        assert result["before_state"]["size_bytes"] == 99

    def test_after_size_is_current_file_size(self, db, workspace):
        content = "rewritten content here"
        target = workspace / "notes.txt"
        target.write_text(content)
        (config.BACKUPS_DIR / "notes.txt.bak").write_text("old")

        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path=str(target), engine=db)

        summary = _make_execution_summary(job.job_id, [_action("file_read", output="size=5")])
        result = _agent(db).run(job.job_id, summary)

        assert result["after_state"]["size_bytes"] == len(content.encode())

    def test_status_advances_to_reporting(self, db, workspace):
        target = workspace / "notes.txt"
        target.write_text("content")
        (config.BACKUPS_DIR / "notes.txt.bak").write_text("original")

        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path=str(target), engine=db)

        summary = _make_execution_summary(job.job_id, [])
        _agent(db).run(job.job_id, summary)

        assert get_job(job.job_id, engine=db).status == JobStatus.REPORTING


class TestRewriteMissingTarget:
    """Missing target file after rewrite: all_passed=False."""

    def test_all_passed_false(self, db, workspace):
        target = workspace / "notes.txt"
        # Do NOT create the target file
        (config.BACKUPS_DIR / "notes.txt.bak").write_text("original")

        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path=str(target), engine=db)

        summary = _make_execution_summary(job.job_id, [])
        result = _agent(db).run(job.job_id, summary)

        assert result["all_passed"] is False

    def test_target_file_exists_check_fails(self, db, workspace):
        target = workspace / "notes.txt"
        (config.BACKUPS_DIR / "notes.txt.bak").write_text("original")

        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path=str(target), engine=db)

        summary = _make_execution_summary(job.job_id, [])
        result = _agent(db).run(job.job_id, summary)

        target_check = next(c for c in result["checks"] if c["name"] == "target_file_exists")
        assert target_check["passed"] is False


class TestRewriteMissingBackup:
    """Missing backup: all_passed=False."""

    def test_all_passed_false(self, db, workspace):
        target = workspace / "notes.txt"
        target.write_text("rewritten")
        # Do NOT create the backup

        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path=str(target), engine=db)

        summary = _make_execution_summary(job.job_id, [])
        result = _agent(db).run(job.job_id, summary)

        assert result["all_passed"] is False

    def test_backup_exists_check_fails(self, db, workspace):
        target = workspace / "notes.txt"
        target.write_text("rewritten")

        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path=str(target), engine=db)

        summary = _make_execution_summary(job.job_id, [])
        result = _agent(db).run(job.job_id, summary)

        backup_check = next(c for c in result["checks"] if c["name"] == "backup_exists")
        assert backup_check["passed"] is False


# ---------------------------------------------------------------------------
# run_script tests
# ---------------------------------------------------------------------------

class TestRunScriptSuccess:
    """Successful script: all_passed=True, correct exit code."""

    def test_all_passed_true(self, db, workspace):
        target = workspace / "cleanup.py"
        target.write_text("print('done')")

        job = create_job("Run cleanup.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path=str(target), engine=db)

        output = "exit_code=0\nstdout=done\nstderr="
        summary = _make_execution_summary(job.job_id, [_action("cmd_execute", output=output)])
        result = _agent(db).run(job.job_id, summary)

        assert result["all_passed"] is True

    def test_exit_code_in_after_state(self, db, workspace):
        target = workspace / "cleanup.py"
        target.write_text("print('ok')")

        job = create_job("Run cleanup.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path=str(target), engine=db)

        output = "exit_code=0\nstdout=ok\nstderr="
        summary = _make_execution_summary(job.job_id, [_action("cmd_execute", output=output)])
        result = _agent(db).run(job.job_id, summary)

        assert result["after_state"]["exit_code"] == 0

    def test_stdout_lines_in_after_state(self, db, workspace):
        target = workspace / "cleanup.py"
        target.write_text("print('hello')")

        job = create_job("Run cleanup.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path=str(target), engine=db)

        output = "exit_code=0\nstdout=hello\nstderr="
        summary = _make_execution_summary(job.job_id, [_action("cmd_execute", output=output)])
        result = _agent(db).run(job.job_id, summary)

        assert "hello" in result["after_state"]["output_lines"]


class TestRunScriptFailure:
    """Failed script (exit_code 1): all_passed=False."""

    def test_all_passed_false(self, db, workspace):
        target = workspace / "bad.py"
        target.write_text("raise ValueError")

        job = create_job("Run bad.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path=str(target), engine=db)

        output = "exit_code=1\nstdout=\nstderr=ValueError"
        summary = _make_execution_summary(job.job_id, [_action("cmd_execute", output=output)])
        result = _agent(db).run(job.job_id, summary)

        assert result["all_passed"] is False

    def test_exit_code_check_fails(self, db, workspace):
        target = workspace / "bad.py"
        target.write_text("raise ValueError")

        job = create_job("Run bad.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path=str(target), engine=db)

        output = "exit_code=1\nstdout=\nstderr=err"
        summary = _make_execution_summary(job.job_id, [_action("cmd_execute", output=output)])
        result = _agent(db).run(job.job_id, summary)

        exit_check = next(c for c in result["checks"] if c["name"] == "exit_code_zero")
        assert exit_check["passed"] is False


# ---------------------------------------------------------------------------
# Timestamped backup tests
# ---------------------------------------------------------------------------

class TestTimestampedBackup:
    """Verification must find the timestamped backup from FILE_BACKUP execution_output."""

    def test_verification_finds_timestamped_backup(self, db, workspace):
        """Verification passes when backup path comes from FILE_BACKUP execution_output."""
        from raoc.config import make_timestamped_stem
        from raoc.db.queries import get_job

        target = workspace / "notes.txt"
        target.write_text("rewritten content")

        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path=str(target), engine=db)

        created_at = get_job(job.job_id, engine=db).created_at
        ts_stem = make_timestamped_stem(target.name, created_at)
        bak_name = f"{ts_stem}{target.suffix}.bak"
        bak_path = config.BACKUPS_DIR / bak_name
        bak_path.write_text("original content")

        summary = _make_execution_summary(job.job_id, [
            _action("file_read", output="size=16"),
            {"action_id": "bak1", "step_index": 1, "action_type": "file_backup",
             "status": "succeeded", "output": str(bak_path)},
            _action("file_write"),
        ])

        result = _agent(db).run(job.job_id, summary)

        assert result["all_passed"] is True
        backup_check = next(c for c in result["checks"] if c["name"] == "backup_exists")
        assert backup_check["passed"] is True
        assert bak_name in result["after_state"]["backup_path"]

    def test_verification_fails_if_backup_missing(self, db, workspace):
        """Verification fails when the backup file referenced in execution_output is absent."""
        target = workspace / "notes.txt"
        target.write_text("rewritten content")

        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path=str(target), engine=db)

        # Reference a backup path that does NOT exist
        missing_bak = config.BACKUPS_DIR / "notes_20260101_000000.txt.bak"

        summary = _make_execution_summary(job.job_id, [
            {"action_id": "bak1", "step_index": 1, "action_type": "file_backup",
             "status": "succeeded", "output": str(missing_bak)},
            _action("file_write"),
        ])

        result = _agent(db).run(job.job_id, summary)

        assert result["all_passed"] is False
        backup_check = next(c for c in result["checks"] if c["name"] == "backup_exists")
        assert backup_check["passed"] is False
