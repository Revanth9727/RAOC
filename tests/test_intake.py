"""Tests for raoc.agents.intake.IntakeAgent."""

from unittest.mock import MagicMock

import pytest

from raoc.agents.intake import IntakeAgent
from raoc.db.queries import create_job, get_audit_log, get_job
from raoc.db.schema import create_tables, get_engine
from raoc.models.job import JobStatus
from raoc.models.task import TaskObject
from raoc.substrate.llm_client import LLMClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """Return an in-memory SQLite engine with all tables created."""
    engine = get_engine(db_path=tmp_path / "test_intake.db")
    create_tables(engine)
    return engine


def _make_agent(db, tool_input: dict) -> tuple[IntakeAgent, str]:
    """Return an IntakeAgent with a mocked LLM and a pre-created job_id.

    *tool_input* is what Claude's tool_use block would return as 'input'.
    """
    mock_llm = MagicMock(spec=LLMClient)
    mock_llm.call.return_value = {
        "type": "tool_use",
        "id": "tu_test",
        "name": "create_task_object",
        "input": tool_input,
    }
    return IntakeAgent(db=db, llm=mock_llm), mock_llm


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestIntakeRunScript:
    """'Run the cleanup.py script' → run_script, no clarification."""

    def test_task_type_is_run_script(self, db):
        job = create_job("Run the cleanup.py script", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": "cleanup.py",
            "instruction": "Run cleanup.py",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert isinstance(task, TaskObject)
        assert task.task_type == "run_script"
        assert task.requires_clarification is False

    def test_job_status_advances_to_discovering(self, db):
        job = create_job("Run the cleanup.py script", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": "cleanup.py",
            "instruction": "Run cleanup.py",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        agent.run(job.job_id)

        updated = get_job(job.job_id, engine=db)
        assert updated.status == JobStatus.DISCOVERING

    def test_target_path_written_to_job(self, db):
        job = create_job("Run the cleanup.py script", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": "cleanup.py",
            "instruction": "Run cleanup.py",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        agent.run(job.job_id)

        updated = get_job(job.job_id, engine=db)
        assert updated.target_path == "cleanup.py"
        assert updated.task_type == "run_script"


class TestIntakeRewriteFile:
    """'Rewrite my notes.txt to be more concise' → rewrite_file."""

    def test_task_type_is_rewrite_file(self, db):
        job = create_job("Rewrite my notes.txt to be more concise", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "rewrite_file",
            "rewrite_target": "notes.txt",
            "instruction": "Rewrite notes.txt to be more concise",
            "risk_level": "high",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.task_type == "rewrite_file"
        assert task.risk_level == "high"
        assert task.requires_clarification is False


class TestIntakeWriteAndRunScript:
    """'Write a script that counts words in notes.txt and run it' → run_script, target_path=None."""

    def test_task_type_is_run_script(self, db):
        job = create_job(
            "Write a script that counts words in notes.txt and run it", engine=db
        )
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": None,
            "instruction": "Write a word-count script for notes.txt and execute it",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.task_type == "run_script"
        assert task.requires_clarification is False

    def test_target_path_is_none_for_new_script(self, db):
        """Case B: data file in instruction must NOT become target_path."""
        job = create_job(
            "Write a script that counts words in notes.txt and run it", engine=db
        )
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": None,
            "instruction": "Write a word-count script for notes.txt and execute it",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.target_path is None

    def test_instruction_contains_full_context(self, db):
        """The instruction must carry any filenames the script should process."""
        job = create_job(
            "Write a script that counts words in notes.txt and run it", engine=db
        )
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": None,
            "instruction": "Write a word-count script for notes.txt and execute it",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert "notes.txt" in task.instruction


class TestIntakeCaseA:
    """Case A: user names an existing script → existing_script_path set → target_path matches."""

    def test_existing_script_path_becomes_target_path(self, db):
        job = create_job("Run cleanup.py", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": "cleanup.py",
            "instruction": "Run cleanup.py",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.target_path == "cleanup.py"


class TestIntakeCaseB:
    """Case B: user wants a new script written → existing_script_path null → target_path None."""

    def test_new_script_request_target_path_is_none(self, db):
        job = create_job("Write a script that prints hello world", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": None,
            "instruction": "Write a script that prints hello world",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.target_path is None


class TestIntakeRewriteFileTarget:
    """rewrite_file: rewrite_target is set, existing_script_path is null, target_path matches."""

    def test_rewrite_target_becomes_target_path(self, db):
        job = create_job("Rewrite notes.txt using bullet points", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "rewrite_file",
            "rewrite_target": "notes.txt",
            "existing_script_path": None,
            "instruction": "Rewrite notes.txt using bullet points",
            "risk_level": "high",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.target_path == "notes.txt"


class TestIntakeAmbiguous:
    """'Fix my document' (ambiguous) → requires_clarification=True."""

    def test_requires_clarification_is_true(self, db):
        job = create_job("Fix my document", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "rewrite_file",
            "rewrite_target": None,
            "instruction": "Fix the document",
            "risk_level": "high",
            "requires_clarification": True,
            "clarification_question": "Which file should I fix, and what changes do you want?",
        })
        task = agent.run(job.job_id)

        assert task.requires_clarification is True
        assert task.clarification_question is not None

    def test_job_status_is_awaiting_approval(self, db):
        job = create_job("Fix my document", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "rewrite_file",
            "rewrite_target": None,
            "instruction": "Fix the document",
            "risk_level": "high",
            "requires_clarification": True,
            "clarification_question": "Which file should I fix?",
        })
        agent.run(job.job_id)

        updated = get_job(job.job_id, engine=db)
        assert updated.status == JobStatus.AWAITING_APPROVAL

    def test_audit_entry_is_needs_clarification(self, db):
        job = create_job("Fix my document", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "rewrite_file",
            "rewrite_target": None,
            "instruction": "Fix the document",
            "risk_level": "high",
            "requires_clarification": True,
            "clarification_question": "Which file?",
        })
        agent.run(job.job_id)

        log = get_audit_log(job.job_id, engine=db)
        events = [e["event"] for e in log]
        assert "intake_needs_clarification" in events


class TestClarificationQuestion:
    """clarification_question field is set correctly on task and job."""

    def test_clarification_question_is_none_when_no_clarification_needed(self, db):
        job = create_job("Run cleanup.py", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": "cleanup.py",
            "instruction": "Run cleanup.py",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.clarification_question is None

    def test_clarification_question_is_string_when_clarification_needed(self, db):
        job = create_job("Fix my stuff", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "rewrite_file",
            "rewrite_target": None,
            "instruction": "Fix my stuff",
            "risk_level": "high",
            "requires_clarification": True,
            "clarification_question": "Which file should I fix, and what changes do you want?",
        })
        task = agent.run(job.job_id)

        assert isinstance(task.clarification_question, str)
        assert len(task.clarification_question) > 0

    def test_clarification_question_saved_to_job(self, db):
        job = create_job("Fix my stuff", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "rewrite_file",
            "rewrite_target": None,
            "instruction": "Fix my stuff",
            "risk_level": "high",
            "requires_clarification": True,
            "clarification_question": "Which file should I fix, and what changes do you want?",
        })
        agent.run(job.job_id)

        updated = get_job(job.job_id, engine=db)
        assert updated.clarification_question == "Which file should I fix, and what changes do you want?"


class TestIntakeOutsideWorkspacePath:
    """'Run /etc/passwd' → classified with risk_level=high."""

    def test_risk_level_is_high(self, db):
        job = create_job("Run /etc/passwd", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": "passwd",
            "instruction": "Run the file /etc/passwd",
            "risk_level": "high",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.risk_level == "high"


class TestIntakeAuditTrail:
    """Audit log should contain intake_started and intake_complete on success."""

    def test_audit_log_has_started_and_complete(self, db):
        job = create_job("Run cleanup.py", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "run_script",
            "existing_script_path": "cleanup.py",
            "instruction": "Run it",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        agent.run(job.job_id)

        log = get_audit_log(job.job_id, engine=db)
        events = [e["event"] for e in log]
        assert "intake_started" in events
        assert "intake_complete" in events


class TestIntakeQuery:
    """Query task type is classified correctly and query_intent is populated."""

    def test_query_classified_correctly(self, db):
        job = create_job("what files do I have?", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "query",
            "query_intent": "list all files in the workspace",
            "instruction": "list all files",
            "risk_level": "low",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.task_type == "query"
        assert task.query_intent == "list all files in the workspace"

    def test_information_request_not_classified_as_run_script(self, db):
        job = create_job("what is the most recent file in my workspace?", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "query",
            "query_intent": "find the most recently modified file",
            "instruction": "find most recent file",
            "risk_level": "low",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.task_type == "query"
        assert task.task_type != "run_script"

    def test_find_file_without_action_is_query(self, db):
        job = create_job("find the file with my resume", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "query",
            "query_intent": "find which file contains resume content",
            "instruction": "find resume file",
            "risk_level": "low",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.task_type == "query"


class TestIntakeQueryAction:
    """query_action task type is classified correctly with all required fields."""

    def test_query_action_classified_correctly(self, db):
        job = create_job("find my resume and make it more formal", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "query_action",
            "query_intent": "find the file containing resume content",
            "action_instruction": "rewrite to be more formal",
            "implied_task_type": "rewrite_file",
            "instruction": "find my resume and make it more formal",
            "risk_level": "high",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.task_type == "query_action"
        assert task.query_intent == "find the file containing resume content"
        assert task.action_instruction == "rewrite to be more formal"
        assert task.implied_task_type == "rewrite_file"

    def test_find_file_with_action_is_query_action_not_run_script(self, db):
        job = create_job("find the file with Q3 data and run an analysis", engine=db)
        agent, _ = _make_agent(db, {
            "task_type": "query_action",
            "query_intent": "find the file containing Q3 financial data",
            "action_instruction": "run an analysis script on it",
            "implied_task_type": "run_script",
            "instruction": "find Q3 data file and run analysis",
            "risk_level": "medium",
            "requires_clarification": False,
        })
        task = agent.run(job.job_id)

        assert task.task_type == "query_action"
        assert task.implied_task_type == "run_script"
