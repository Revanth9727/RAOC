"""Tests for raoc.agents.planning.PlanningAgent."""

from unittest.mock import MagicMock, patch

import pytest

from raoc import config
from raoc.agents.planning import PlanningAgent
from raoc.db.queries import create_job, get_audit_log, get_job, update_job_field
from raoc.db.schema import create_tables, get_engine
from raoc.models.action import ActionObject, ActionType
from raoc.models.job import JobStatus
from raoc.substrate.exceptions import CommandBlockedError
from raoc.substrate.llm_client import LLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """In-memory SQLite engine with all tables created."""
    engine = get_engine(db_path=tmp_path / "test_planning.db")
    create_tables(engine)
    return engine


_DEFAULT_REWRITE_SUMMARY = (
    "The notes were condensed to remove repetition. "
    "The tone was made more formal. "
    "Three redundant paragraphs were removed."
)
_DEFAULT_SCRIPT_SUMMARY = (
    "This script prints a greeting. "
    "It runs quickly with no side effects."
)


def _mock_llm(
    rewritten: str = "rewritten content",
    rewrite_summary: str = _DEFAULT_REWRITE_SUMMARY,
    script: str = "print('hello')",
    script_type: str = "python3",
    script_summary: str = _DEFAULT_SCRIPT_SUMMARY,
    script_filename: str = "generated_script.py",
) -> MagicMock:
    """Return an LLMClient mock that returns tool_use blocks."""
    llm = MagicMock(spec=LLMClient)

    def _side_effect(system, user, tools=None):
        tool_name = (tools or [{}])[0].get("name", "")
        if tool_name == "generate_rewritten_content":
            return {"type": "tool_use", "id": "t1", "name": tool_name,
                    "input": {"rewritten_content": rewritten,
                              "change_summary": rewrite_summary}}
        if tool_name == "describe_script":
            return {"type": "tool_use", "id": "t3", "name": tool_name,
                    "input": {"description": script_summary}}
        # generate_script
        return {"type": "tool_use", "id": "t2", "name": "generate_script",
                "input": {"script_filename": script_filename,
                          "script_content": script, "script_type": script_type,
                          "change_summary": script_summary}}

    llm.call.side_effect = _side_effect
    return llm


def _run_script_context(
    target: str = "/workspace/script_a.py",
    content: str = "print('ok')",
    exists: bool = True,
) -> dict:
    return {
        "job_id": "placeholder",
        "task_type": "run_script",
        "target_path": target,
        "file_content": content if exists else None,
        "script_exists": exists,
    }


def _rewrite_context(
    target: str = "/workspace/data_file.txt",
    content: str = "draft content",
) -> dict:
    return {
        "job_id": "placeholder",
        "task_type": "rewrite_file",
        "target_path": target,
        "file_content": content,
        "script_exists": True,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRunScriptExisting:
    """run_script with existing script → CMD_INSPECT + CMD_EXECUTE."""

    def test_returns_two_actions(self, db):
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/script_a.py", engine=db)

        context = _run_script_context()
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm())
        actions = agent.run(job.job_id, context)

        assert len(actions) == 2

    def test_step_types_in_order(self, db):
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/script_a.py", engine=db)

        context = _run_script_context()
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm())
        actions = agent.run(job.job_id, context)

        types = [a.action_type for a in actions]
        assert types == [ActionType.CMD_INSPECT, ActionType.CMD_EXECUTE]

    def test_step_indexes_sequential(self, db):
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/script_a.py", engine=db)

        context = _run_script_context()
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm())
        actions = agent.run(job.job_id, context)

        assert [a.step_index for a in actions] == [0, 1]

    def test_cmd_execute_target_ends_in_script_extension(self, db):
        target = "/workspace/script_a.py"
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path=target, engine=db)

        context = _run_script_context(target=target)
        context["job_id"] = job.job_id

        actions = PlanningAgent(db=db, llm=_mock_llm()).run(job.job_id, context)

        execute = next(a for a in actions if a.action_type == ActionType.CMD_EXECUTE)
        assert execute.target_path.endswith(".py") or execute.target_path.endswith(".sh")

    def test_cmd_execute_command_starts_with_runner(self, db):
        target = "/workspace/script_a.py"
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path=target, engine=db)

        context = _run_script_context(target=target)
        context["job_id"] = job.job_id

        actions = PlanningAgent(db=db, llm=_mock_llm()).run(job.job_id, context)

        execute = next(a for a in actions if a.action_type == ActionType.CMD_EXECUTE)
        cmd = execute.command or ""
        assert any(cmd.strip().startswith(r) for r in ("python3", "bash", "sh"))

    def test_no_file_write_step(self, db):
        target = "/workspace/script_a.py"
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path=target, engine=db)

        context = _run_script_context(target=target)
        context["job_id"] = job.job_id

        actions = PlanningAgent(db=db, llm=_mock_llm()).run(job.job_id, context)

        assert not any(a.action_type == ActionType.FILE_WRITE for a in actions)


class TestRewriteFile:
    """rewrite_file → FILE_READ + FILE_BACKUP + FILE_WRITE."""

    def test_returns_three_actions(self, db):
        job = create_job("Rewrite data_file.txt to be concise", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path="/workspace/data_file.txt", engine=db)

        context = _rewrite_context()
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm())
        actions = agent.run(job.job_id, context)

        assert len(actions) == 3

    def test_step_types_in_order(self, db):
        job = create_job("Rewrite data_file.txt to be concise", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path="/workspace/data_file.txt", engine=db)

        context = _rewrite_context()
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm())
        actions = agent.run(job.job_id, context)

        types = [a.action_type for a in actions]
        assert types == [ActionType.FILE_READ, ActionType.FILE_BACKUP, ActionType.FILE_WRITE]

    def test_file_write_command_is_rewritten_content(self, db):
        job = create_job("Rewrite data_file.txt to be concise", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path="/workspace/data_file.txt", engine=db)

        context = _rewrite_context()
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm(rewritten="concise version"))
        actions = agent.run(job.job_id, context)

        file_write = actions[2]
        assert file_write.action_type == ActionType.FILE_WRITE
        assert file_write.command == "concise version"

    def test_step_indexes_sequential(self, db):
        job = create_job("Rewrite data_file.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path="/workspace/data_file.txt", engine=db)

        context = _rewrite_context()
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm())
        actions = agent.run(job.job_id, context)

        assert [a.step_index for a in actions] == [0, 1, 2]


class TestBlockedScript:
    """Script containing 'rm -rf' → job BLOCKED, CommandBlockedError raised."""

    def test_raises_command_blocked_error(self, db):
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/script_a.py", engine=db)

        context = _run_script_context(content="import os\nos.system('rm -rf /')")
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm())
        with pytest.raises(CommandBlockedError):
            agent.run(job.job_id, context)

    def test_job_status_is_blocked(self, db):
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/script_a.py", engine=db)

        context = _run_script_context(content="rm -rf /home")
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm())
        with pytest.raises(CommandBlockedError):
            agent.run(job.job_id, context)

        assert get_job(job.job_id, engine=db).status == JobStatus.BLOCKED


class TestActionsPersistedToDb:
    """All ActionObjects must be saved to the database."""

    def test_actions_saved_for_run_script(self, db):
        from sqlalchemy import text

        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/script_a.py", engine=db)

        context = _run_script_context()
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm())
        actions = agent.run(job.job_id, context)

        with db.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM actions WHERE job_id = :jid"),
                {"jid": job.job_id}
            ).fetchall()

        assert len(rows) == len(actions)

    def test_actions_saved_for_rewrite_file(self, db):
        from sqlalchemy import text

        job = create_job("Rewrite data_file.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path="/workspace/data_file.txt", engine=db)

        context = _rewrite_context()
        context["job_id"] = job.job_id

        agent = PlanningAgent(db=db, llm=_mock_llm())
        actions = agent.run(job.job_id, context)

        with db.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM actions WHERE job_id = :jid"),
                {"jid": job.job_id}
            ).fetchall()

        assert len(rows) == len(actions)


class TestPlanningStatusAndAudit:
    """Status advances to AWAITING_APPROVAL and audit log is written."""

    def test_status_is_awaiting_approval(self, db):
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/script_a.py", engine=db)

        context = _run_script_context()
        context["job_id"] = job.job_id

        PlanningAgent(db=db, llm=_mock_llm()).run(job.job_id, context)

        assert get_job(job.job_id, engine=db).status == JobStatus.AWAITING_APPROVAL

    def test_audit_contains_plan_built(self, db):
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/script_a.py", engine=db)

        context = _run_script_context()
        context["job_id"] = job.job_id

        PlanningAgent(db=db, llm=_mock_llm()).run(job.job_id, context)

        events = [e["event"] for e in get_audit_log(job.job_id, engine=db)]
        assert "plan_built" in events


class TestChangeSummary:
    """FILE_WRITE and CMD_EXECUTE actions must carry a non-empty change_summary."""

    def test_file_write_has_change_summary(self, db):
        job = create_job("Rewrite data_file.txt to be concise", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path="/workspace/data_file.txt", engine=db)

        context = _rewrite_context()
        context["job_id"] = job.job_id

        actions = PlanningAgent(db=db, llm=_mock_llm()).run(job.job_id, context)

        file_write = next(a for a in actions if a.action_type == ActionType.FILE_WRITE)
        assert file_write.change_summary is not None
        assert len(file_write.change_summary) > 10

    def test_file_write_summary_is_not_file_content(self, db):
        """change_summary must be a description, not a dump of the rewritten content."""
        job = create_job("Rewrite data_file.txt", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path="/workspace/data_file.txt", engine=db)

        context = _rewrite_context()
        context["job_id"] = job.job_id

        actions = PlanningAgent(db=db, llm=_mock_llm(rewritten="new file content here")).run(job.job_id, context)

        file_write = next(a for a in actions if a.action_type == ActionType.FILE_WRITE)
        assert file_write.change_summary != file_write.command

    def test_cmd_execute_has_change_summary(self, db):
        job = create_job("Run script_a.py", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/script_a.py", engine=db)

        context = _run_script_context()
        context["job_id"] = job.job_id

        actions = PlanningAgent(db=db, llm=_mock_llm()).run(job.job_id, context)

        cmd_execute = next(a for a in actions if a.action_type == ActionType.CMD_EXECUTE)
        assert cmd_execute.change_summary is not None
        assert len(cmd_execute.change_summary) > 10


# ---------------------------------------------------------------------------
# New script (write-and-run)
# ---------------------------------------------------------------------------

class TestRunScriptNew:
    """run_script with new script → FILE_WRITE + CMD_INSPECT + CMD_EXECUTE."""

    def _new_context(self, job_id: str) -> dict:
        return {
            "job_id": job_id,
            "task_type": "run_script",
            "target_path": "/workspace/input_data.csv",  # input file, NOT the script
            "file_content": None,
            "script_exists": False,
        }

    def test_file_write_step_exists(self, db, tmp_path):
        scripts_dir = tmp_path / "scripts"
        job = create_job("Write a script that processes input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/input_data.csv", engine=db)

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            actions = PlanningAgent(db=db, llm=_mock_llm(script_filename="process_csv.py")).run(
                job.job_id, self._new_context(job.job_id)
            )

        assert any(a.action_type == ActionType.FILE_WRITE for a in actions)

    def test_file_write_target_path_ends_in_py(self, db, tmp_path):
        scripts_dir = tmp_path / "scripts"
        job = create_job("Write a script that processes input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/input_data.csv", engine=db)

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            actions = PlanningAgent(db=db, llm=_mock_llm(script_filename="process_csv.py")).run(
                job.job_id, self._new_context(job.job_id)
            )

        write = next(a for a in actions if a.action_type == ActionType.FILE_WRITE)
        assert write.target_path.endswith(".py")

    def test_file_write_target_inside_scripts_dir(self, db, tmp_path):
        scripts_dir = tmp_path / "scripts"
        job = create_job("Write a script that processes input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/input_data.csv", engine=db)

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            actions = PlanningAgent(db=db, llm=_mock_llm(script_filename="process_csv.py")).run(
                job.job_id, self._new_context(job.job_id)
            )

        write = next(a for a in actions if a.action_type == ActionType.FILE_WRITE)
        assert str(scripts_dir) in write.target_path

    def test_cmd_execute_target_ends_in_py_or_sh(self, db, tmp_path):
        scripts_dir = tmp_path / "scripts"
        job = create_job("Write a script that processes input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/input_data.csv", engine=db)

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            actions = PlanningAgent(db=db, llm=_mock_llm(script_filename="process_csv.py")).run(
                job.job_id, self._new_context(job.job_id)
            )

        execute = next(a for a in actions if a.action_type == ActionType.CMD_EXECUTE)
        assert execute.target_path.endswith(".py") or execute.target_path.endswith(".sh")

    def test_cmd_execute_command_starts_with_python3_or_bash(self, db, tmp_path):
        scripts_dir = tmp_path / "scripts"
        job = create_job("Write a script that processes input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/input_data.csv", engine=db)

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            actions = PlanningAgent(db=db, llm=_mock_llm(script_filename="process_csv.py")).run(
                job.job_id, self._new_context(job.job_id)
            )

        execute = next(a for a in actions if a.action_type == ActionType.CMD_EXECUTE)
        cmd = execute.command or ""
        assert any(cmd.strip().startswith(r) for r in ("python3", "bash", "sh"))

    def test_file_write_command_is_nonempty(self, db, tmp_path):
        scripts_dir = tmp_path / "scripts"
        job = create_job("Write a script that processes input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/input_data.csv", engine=db)

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            actions = PlanningAgent(db=db, llm=_mock_llm(script="print('hello')", script_filename="process_csv.py")).run(
                job.job_id, self._new_context(job.job_id)
            )

        write = next(a for a in actions if a.action_type == ActionType.FILE_WRITE)
        assert write.command and len(write.command) > 0

    def test_job_target_path_updated_to_generated_script(self, db, tmp_path):
        """After planning a new script, job.target_path must be the generated script path.

        Verification and reporter depend on job.target_path being non-None.
        """
        scripts_dir = tmp_path / "scripts"
        job = create_job("Write a script that processes input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path=None, engine=db)

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            actions = PlanningAgent(db=db, llm=_mock_llm(script_filename="process_csv.py")).run(
                job.job_id, self._new_context(job.job_id)
            )

        updated = get_job(job.job_id, engine=db)
        assert updated.target_path is not None
        assert updated.target_path.endswith(".py")
        assert str(scripts_dir) in updated.target_path


# ---------------------------------------------------------------------------
# Validation and retry
# ---------------------------------------------------------------------------

class TestValidationRetry:
    """Bad plan triggers one retry; two consecutive failures → job FAILED."""

    def _new_context(self, job_id: str) -> dict:
        return {
            "job_id": job_id,
            "task_type": "run_script",
            "target_path": "/workspace/input_data.csv",
            "file_content": None,
            "script_exists": False,
        }

    def _bad_response(self, bad_filename: str = "input_data.csv") -> dict:
        """LLM response where script_filename is a data file — fails validation."""
        return {
            "type": "tool_use", "id": "t_bad", "name": "generate_script",
            "input": {
                "script_filename": bad_filename,
                "script_content": "print('hello')",
                "script_type": "python3",
                "change_summary": "Prints hello.",
            },
        }

    def _good_response(self) -> dict:
        """LLM response with a valid .py script_filename."""
        return {
            "type": "tool_use", "id": "t_good", "name": "generate_script",
            "input": {
                "script_filename": "process_data.py",
                "script_content": "print('hello')",
                "script_type": "python3",
                "change_summary": "Prints hello.",
            },
        }

    def test_llm_called_twice_when_first_plan_invalid(self, db, tmp_path):
        scripts_dir = tmp_path / "scripts"
        job = create_job("Process input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/input_data.csv", engine=db)

        llm = MagicMock(spec=LLMClient)
        llm.call.side_effect = [self._bad_response(), self._good_response()]

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            PlanningAgent(db=db, llm=llm).run(job.job_id, self._new_context(job.job_id))

        assert llm.call.call_count == 2

    def test_final_actions_pass_validation_after_retry(self, db, tmp_path):
        scripts_dir = tmp_path / "scripts"
        job = create_job("Process input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/input_data.csv", engine=db)

        llm = MagicMock(spec=LLMClient)
        llm.call.side_effect = [self._bad_response(), self._good_response()]

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            actions = PlanningAgent(db=db, llm=llm).run(job.job_id, self._new_context(job.job_id))

        execute = next(a for a in actions if a.action_type == ActionType.CMD_EXECUTE)
        assert execute.target_path.endswith(".py") or execute.target_path.endswith(".sh")

    def test_wrong_command_format_triggers_retry(self, db, tmp_path):
        """CMD_EXECUTE command not starting with a runner → retry."""
        scripts_dir = tmp_path / "scripts"
        job = create_job("Process input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/input_data.csv", engine=db)

        bad = {
            "type": "tool_use", "id": "t_bad", "name": "generate_script",
            "input": {
                "script_filename": "process_data.py",
                "script_content": "print('hello')",
                "script_type": "node",  # not a valid runner
                "change_summary": "Does something.",
            },
        }
        llm = MagicMock(spec=LLMClient)
        llm.call.side_effect = [bad, self._good_response()]

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            PlanningAgent(db=db, llm=llm).run(job.job_id, self._new_context(job.job_id))

        assert llm.call.call_count == 2

    def test_two_consecutive_failures_sets_job_failed(self, db, tmp_path):
        scripts_dir = tmp_path / "scripts"
        job = create_job("Process input_data.csv", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="/workspace/input_data.csv", engine=db)

        llm = MagicMock(spec=LLMClient)
        llm.call.side_effect = [self._bad_response(), self._bad_response()]

        with patch.object(config, "SCRIPTS_DIR", scripts_dir):
            with pytest.raises(ValueError, match="Could not build a valid plan"):
                PlanningAgent(db=db, llm=llm).run(job.job_id, self._new_context(job.job_id))

        assert get_job(job.job_id, engine=db).status == JobStatus.FAILED


# ---------------------------------------------------------------------------
# Timestamped intent tests
# ---------------------------------------------------------------------------

class TestTimestampedIntents:
    """FILE_BACKUP and FILE_WRITE intents must show the timestamped filenames."""

    def test_plan_preview_shows_timestamped_backup_name(self, db, tmp_path):
        """FILE_BACKUP action intent contains the timestamped filename."""
        from raoc.db.queries import get_job

        job = create_job("Rewrite notes.txt to be concise", engine=db)
        update_job_field(
            job.job_id,
            task_type="rewrite_file",
            target_path="/workspace/notes.txt",
            engine=db,
        )

        context = _rewrite_context(target="/workspace/notes.txt")
        context["job_id"] = job.job_id

        actions = PlanningAgent(db=db, llm=_mock_llm()).run(job.job_id, context)

        backup_action = next(a for a in actions if a.action_type == ActionType.FILE_BACKUP)
        # Intent must mention a timestamped name like "notes_20260324_143022.txt.bak"
        assert "_" in backup_action.intent, "Backup intent should contain a timestamped filename"
        assert ".txt.bak" in backup_action.intent, "Backup intent should show .txt.bak extension"


# ---------------------------------------------------------------------------
# PDF write strategy tests
# ---------------------------------------------------------------------------

class TestPdfWriteStrategy:
    """_determine_pdf_write_strategy returns correct strategy and user message."""

    def test_pdf_inplace_strategy_when_within_tolerance(self, db):
        """write_strategy == 'pdf_inplace' when rewritten text is within 15% of originals."""
        from raoc.agents.planning import _determine_pdf_write_strategy

        # Original total = 100 chars; rewritten total = 110 chars (10% longer — within tolerance)
        text_blocks = [
            {"page": 0, "bbox": (0, 0, 100, 20), "original_text": "A" * 60, "font_size": 12.0},
            {"page": 0, "bbox": (0, 25, 100, 45), "original_text": "B" * 40, "font_size": 12.0},
        ]
        rewritten = "C" * 110  # 10% longer than 100 — within 15%

        strategy, message = _determine_pdf_write_strategy(text_blocks, rewritten, 'pdf_native')

        assert strategy == 'pdf_inplace'
        assert "preserved" in message.lower() or "in place" in message.lower()

    def test_pdf_docx_fallback_when_exceeds_tolerance(self, db):
        """write_strategy == 'pdf_to_docx' when rewritten text exceeds 15% tolerance."""
        from raoc.agents.planning import _determine_pdf_write_strategy

        # Original total = 100 chars; rewritten total = 140 chars (40% longer — exceeds tolerance)
        text_blocks = [
            {"page": 0, "bbox": (0, 0, 100, 20), "original_text": "A" * 100, "font_size": 12.0},
        ]
        rewritten = "B" * 140  # 40% longer

        strategy, message = _determine_pdf_write_strategy(text_blocks, rewritten, 'pdf_native')

        assert strategy == 'pdf_to_docx'
        assert "%" in message  # message explains the percentage difference

    def test_pdf_ocr_always_uses_docx_strategy(self, db):
        """write_strategy == 'pdf_to_docx' when extraction_method == 'pdf_ocr'."""
        from raoc.agents.planning import _determine_pdf_write_strategy

        # Even if blocks are the same size, OCR always falls back
        text_blocks = [
            {"page": 0, "bbox": (0, 0, 100, 20), "original_text": "A" * 100, "font_size": 12.0},
        ]
        rewritten = "B" * 100  # same length — would be inplace if not OCR

        strategy, message = _determine_pdf_write_strategy(text_blocks, rewritten, 'pdf_ocr')

        assert strategy == 'pdf_to_docx'
        assert "ocr" in message.lower()
