"""Tests for raoc.agents.reporter.ReporterAgent."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from raoc.agents.reporter import ReporterAgent
from raoc.db.queries import create_job, get_job, save_action, update_job_field
from raoc.db.schema import create_tables, get_engine
from raoc.models.action import ActionObject, ActionType
from raoc.models.job import JobStatus
from raoc.gateway.telegram_bot import TelegramGateway


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    engine = get_engine(db_path=tmp_path / "test_reporter.db")
    create_tables(engine)
    return engine


def _mock_gateway() -> MagicMock:
    gw = MagicMock(spec=TelegramGateway)
    gw.send_message = AsyncMock()
    return gw


def _agent(db, gateway=None) -> ReporterAgent:
    return ReporterAgent(db=db, gateway=gateway or _mock_gateway())


_REWRITE_SUMMARY = (
    "The notes were condensed to remove repetition. "
    "The tone was made more formal. "
    "Three redundant paragraphs were removed."
)

_SCRIPT_SUMMARY = (
    "This script cleans up temporary files in the workspace. "
    "It removes all .tmp files and prints a count of deleted files."
)


def _create_file_write_action(job_id: str, db, summary: str = _REWRITE_SUMMARY) -> None:
    """Insert a FILE_WRITE action with the given change_summary."""
    action = ActionObject(
        job_id=job_id,
        step_index=3,
        action_type=ActionType.FILE_WRITE,
        risk_level="medium",
        target_path="/ws/notes.txt",
        intent="Write rewritten content",
        change_summary=summary,
    )
    save_action(action, engine=db)


def _create_cmd_execute_action(job_id: str, db, summary: str = _SCRIPT_SUMMARY) -> None:
    """Insert a CMD_EXECUTE action with the given change_summary."""
    action = ActionObject(
        job_id=job_id,
        step_index=2,
        action_type=ActionType.CMD_EXECUTE,
        risk_level="medium",
        target_path="/ws/cleanup.py",
        intent="Run the script",
        change_summary=summary,
    )
    save_action(action, engine=db)


def _rewrite_result(job_id: str, all_passed: bool = True,
                    file_path: str = "/ws/notes.txt",
                    backup_path: str = "/ws/.backups/notes.txt.bak") -> dict:
    checks = [
        {"name": "target_file_exists", "passed": all_passed, "detail": file_path if all_passed else "File not found"},
        {"name": "backup_exists", "passed": all_passed, "detail": backup_path},
        {"name": "target_not_empty", "passed": all_passed, "detail": "size=120"},
        {"name": "content_changed", "passed": True, "detail": "changed"},
    ]
    return {
        "job_id": job_id,
        "task_type": "rewrite_file",
        "all_passed": all_passed,
        "checks": checks,
        "before_state": {"file_path": file_path, "size_bytes": 100},
        "after_state": {"file_path": file_path, "size_bytes": 120, "backup_path": backup_path},
    }


def _script_result(job_id: str, all_passed: bool = True,
                   exit_code: int = 0, output_lines: list = None) -> dict:
    if output_lines is None:
        output_lines = ["done"] if all_passed else []
    checks = [
        {"name": "exit_code_zero", "passed": exit_code == 0, "detail": f"exit_code={exit_code}"},
        {"name": "has_stdout", "passed": True, "detail": "stdout present"},
        {"name": "script_file_exists", "passed": all_passed, "detail": "/ws/cleanup.py"},
    ]
    return {
        "job_id": job_id,
        "task_type": "run_script",
        "all_passed": all_passed,
        "checks": checks,
        "before_state": {"script_path": "/ws/cleanup.py", "script_existed": True},
        "after_state": {"exit_code": exit_code, "output_lines": output_lines,
                        "stderr": "SyntaxError: bad token" if not all_passed else ""},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRewriteSuccess:
    """Success rewrite: message contains change_summary, backup name, no byte sizes."""

    def test_send_message_called(self, db):
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", engine=db)
        _create_file_write_action(job.job_id, db)
        gw = _mock_gateway()

        _agent(db, gw).run(job.job_id, _rewrite_result(job.job_id))

        gw.send_message.assert_called_once()

    def test_message_contains_success_marker(self, db):
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", engine=db)
        _create_file_write_action(job.job_id, db)
        gw = _mock_gateway()

        _agent(db, gw).run(job.job_id, _rewrite_result(job.job_id))

        text = gw.send_message.call_args.kwargs["text"]
        assert "✅" in text
        assert "rewritten" in text.lower()

    def test_message_contains_change_summary(self, db):
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", engine=db)
        _create_file_write_action(job.job_id, db, summary=_REWRITE_SUMMARY)
        gw = _mock_gateway()

        _agent(db, gw).run(job.job_id, _rewrite_result(job.job_id))

        text = gw.send_message.call_args.kwargs["text"]
        assert "condensed" in text  # part of _REWRITE_SUMMARY
        assert "redundant paragraphs" in text

    def test_message_contains_backup_filename(self, db):
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", engine=db)
        _create_file_write_action(job.job_id, db)
        gw = _mock_gateway()

        _agent(db, gw).run(job.job_id, _rewrite_result(job.job_id))

        text = gw.send_message.call_args.kwargs["text"]
        assert "notes.txt.bak" in text

    def test_message_does_not_contain_byte_sizes(self, db):
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", engine=db)
        _create_file_write_action(job.job_id, db)
        gw = _mock_gateway()

        _agent(db, gw).run(job.job_id, _rewrite_result(job.job_id))

        text = gw.send_message.call_args.kwargs["text"]
        assert " bytes" not in text
        assert "100 " not in text
        assert "120 " not in text

    def test_message_does_not_contain_full_path(self, db):
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", engine=db)
        _create_file_write_action(job.job_id, db)
        gw = _mock_gateway()

        _agent(db, gw).run(job.job_id, _rewrite_result(job.job_id))

        text = gw.send_message.call_args.kwargs["text"]
        assert "/ws/" not in text

    def test_job_status_completed(self, db):
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", engine=db)
        _create_file_write_action(job.job_id, db)

        _agent(db).run(job.job_id, _rewrite_result(job.job_id))

        assert get_job(job.job_id, engine=db).status == JobStatus.COMPLETED


class TestRewriteFailure:
    """Failed rewrite: message contains failure marker and reason."""

    def test_message_contains_failure_marker(self, db):
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", engine=db)
        gw = _mock_gateway()

        _agent(db, gw).run(job.job_id, _rewrite_result(job.job_id, all_passed=False))

        text = gw.send_message.call_args.kwargs["text"]
        assert "❌" in text
        assert "not changed" in text

    def test_message_contains_restore_note(self, db):
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", engine=db)
        gw = _mock_gateway()

        _agent(db, gw).run(job.job_id, _rewrite_result(job.job_id, all_passed=False))

        text = gw.send_message.call_args.kwargs["text"]
        assert "original" in text.lower() or "backup" in text.lower()

    def test_job_status_failed(self, db):
        job = create_job("Rewrite notes.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", engine=db)

        _agent(db).run(job.job_id, _rewrite_result(job.job_id, all_passed=False))

        assert get_job(job.job_id, engine=db).status == JobStatus.FAILED


class TestScriptSuccess:
    """Success script: output lines and change_summary included in message."""

    def test_message_contains_output_lines(self, db):
        job = create_job("Run cleanup.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", engine=db)
        _create_cmd_execute_action(job.job_id, db)
        gw = _mock_gateway()

        _agent(db, gw).run(
            job.job_id,
            _script_result(job.job_id, output_lines=["Files cleaned: 42", "Done."]),
        )

        text = gw.send_message.call_args.kwargs["text"]
        assert "Files cleaned: 42" in text
        assert "Done." in text

    def test_message_contains_change_summary(self, db):
        job = create_job("Run cleanup.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", engine=db)
        _create_cmd_execute_action(job.job_id, db, summary=_SCRIPT_SUMMARY)
        gw = _mock_gateway()

        _agent(db, gw).run(job.job_id, _script_result(job.job_id))

        text = gw.send_message.call_args.kwargs["text"]
        assert "cleans up temporary files" in text

    def test_message_contains_success_marker(self, db):
        job = create_job("Run cleanup.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", engine=db)
        _create_cmd_execute_action(job.job_id, db)
        gw = _mock_gateway()

        _agent(db, gw).run(job.job_id, _script_result(job.job_id))

        text = gw.send_message.call_args.kwargs["text"]
        assert "✅" in text

    def test_job_status_completed(self, db):
        job = create_job("Run cleanup.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", engine=db)
        _create_cmd_execute_action(job.job_id, db)

        _agent(db).run(job.job_id, _script_result(job.job_id))

        assert get_job(job.job_id, engine=db).status == JobStatus.COMPLETED


class TestScriptFailure:
    """Failed script: error lines included in message."""

    def test_message_contains_failure_marker(self, db):
        job = create_job("Run bad.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", engine=db)
        gw = _mock_gateway()

        _agent(db, gw).run(
            job.job_id,
            _script_result(job.job_id, all_passed=False, exit_code=1),
        )

        text = gw.send_message.call_args.kwargs["text"]
        assert "❌" in text
        assert "did not complete" in text

    def test_message_contains_exit_code(self, db):
        job = create_job("Run bad.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", engine=db)
        gw = _mock_gateway()

        _agent(db, gw).run(
            job.job_id,
            _script_result(job.job_id, all_passed=False, exit_code=1),
        )

        text = gw.send_message.call_args.kwargs["text"]
        assert "Exit code 1" in text

    def test_job_status_failed(self, db):
        job = create_job("Run bad.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", engine=db)

        _agent(db).run(
            job.job_id,
            _script_result(job.job_id, all_passed=False, exit_code=1),
        )

        assert get_job(job.job_id, engine=db).status == JobStatus.FAILED
