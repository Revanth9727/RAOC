"""End-to-end integration tests for the full RAOC pipeline.

Uses a real SQLite DB and real substrate components with:
  - LLMClient.call mocked (no real Anthropic calls)
  - TelegramGateway send methods mocked (no real Telegram)
All file operations go to tmp_path.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from raoc import config
from raoc.coordinator import PipelineCoordinator
from raoc.db.queries import get_job
from raoc.db.schema import create_tables, get_engine
from raoc.models.job import JobStatus
from raoc.substrate.command_wrapper import CommandWrapper
from raoc.substrate.host_sampler import HostSampler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_rewrite():
    """LLMClient mock for a rewrite_file pipeline."""
    llm = MagicMock()

    def _call(system, user, tools=None):
        name = (tools or [{}])[0].get("name", "")
        if name == "create_task_object":
            return {
                "type": "tool_use",
                "input": {
                    "task_type": "rewrite_file",
                    "rewrite_target": "notes.txt",
                    "instruction": user,
                    "risk_level": "high",
                    "requires_clarification": False,
                },
            }
        # generate_rewritten_content
        return {
            "type": "tool_use",
            "input": {
                "rewritten_content": "This is the rewritten content.",
                "change_summary": "The file was condensed and made more concise.",
            },
        }

    llm.call = MagicMock(side_effect=_call)
    return llm


def _make_llm_script():
    """LLMClient mock for a run_script pipeline (existing script — no planning LLM call)."""
    llm = MagicMock()

    def _call(system, user, tools=None):
        # Only intake uses LLM for existing-script jobs
        return {
            "type": "tool_use",
            "input": {
                "task_type": "run_script",
                "existing_script_path": "hello.py",
                "instruction": user,
                "risk_level": "medium",
                "requires_clarification": False,
            },
        }

    llm.call = MagicMock(side_effect=_call)
    return llm


def _make_gateway():
    """Mock TelegramGateway with AsyncMock send methods."""
    gw = MagicMock()
    gw.send_message = AsyncMock()
    gw.send_approval_request = AsyncMock()
    gw.send_status = AsyncMock()
    return gw


def _build_coordinator(engine, llm, gateway):
    """Create a PipelineCoordinator with real substrate, mocked I/O."""
    return PipelineCoordinator(
        db=engine,
        llm=llm,
        sampler=HostSampler(),
        command_wrapper=CommandWrapper(),
        gateway=gateway,
    )


# ---------------------------------------------------------------------------
# rewrite_file end-to-end
# ---------------------------------------------------------------------------

async def test_rewrite_file_end_to_end(tmp_path, monkeypatch):
    """Full pipeline: intake → discovery → planning → approval → execution → report."""
    # 1-3. Redirect all workspace paths to tmp_path
    monkeypatch.setattr(config, "WORKSPACE", tmp_path)
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / ".backups")
    (tmp_path / ".backups").mkdir()

    # 4. Test file
    notes = tmp_path / "notes.txt"
    notes.write_text("hello world")

    # DB
    engine = get_engine(db_path=tmp_path / "e2e.db")
    create_tables(engine)

    # 5. LLM mock
    llm = _make_llm_rewrite()

    # 6-8. Gateway mock
    gateway = _make_gateway()

    # 9. Coordinator
    coord = _build_coordinator(engine, llm, gateway)

    # 10. Kick off pipeline
    job_id = await coord.handle_new_message("Rewrite notes.txt to be shorter")

    # 11. Job is awaiting human approval
    assert get_job(job_id, engine=engine).status == JobStatus.AWAITING_APPROVAL

    # 12. Approval request sent with the correct job_id
    gateway.send_approval_request.assert_called_once()
    sent_job_id = gateway.send_approval_request.call_args.args[0]
    assert sent_job_id == job_id

    # 13. Human approves
    await coord.handle_approval(job_id, approved=True)

    # 14. Job completed
    assert get_job(job_id, engine=engine).status == JobStatus.COMPLETED

    # 15. File rewritten with LLM-generated content
    assert notes.exists()
    assert notes.read_text() == "This is the rewritten content."

    # 16. A timestamped backup of the original exists in the backups dir
    backups = list((tmp_path / ".backups").glob("notes_*.txt.bak"))
    assert len(backups) == 1, f"Expected 1 timestamped backup, found: {backups}"

    # 17. Success report sent
    gateway.send_message.assert_called_once()
    text = gateway.send_message.call_args.kwargs["text"]
    assert "✅" in text
    assert "rewritten" in text.lower()
    assert "condensed" in text  # from change_summary


# ---------------------------------------------------------------------------
# run_script end-to-end
# ---------------------------------------------------------------------------

async def test_run_script_end_to_end(tmp_path, monkeypatch):
    """Full pipeline: intake → discovery → planning → approval → execution → report."""
    # Redirect workspace paths
    monkeypatch.setattr(config, "WORKSPACE", tmp_path)
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / ".backups")
    (tmp_path / ".backups").mkdir()

    # Existing script
    script = tmp_path / "hello.py"
    script.write_text("print('hello from script')")

    # DB
    engine = get_engine(db_path=tmp_path / "e2e_script.db")
    create_tables(engine)

    # LLM mock (intake only — existing script skips planning LLM call)
    llm = _make_llm_script()

    # Gateway mock
    gateway = _make_gateway()

    # Coordinator
    coord = _build_coordinator(engine, llm, gateway)

    # Mock CommandWrapper.run — script "runs" successfully
    coord.execution.command_wrapper.run = MagicMock(return_value={
        "exit_code": 0,
        "stdout": "hello from script",
        "stderr": "",
        "timed_out": False,
        "duration_ms": 50,
    })

    # Start pipeline
    job_id = await coord.handle_new_message("Run hello.py")

    # Awaiting approval
    assert get_job(job_id, engine=engine).status == JobStatus.AWAITING_APPROVAL

    # Approve
    await coord.handle_approval(job_id, approved=True)

    # Job completed
    assert get_job(job_id, engine=engine).status == JobStatus.COMPLETED

    # Success report contains script output
    gateway.send_message.assert_called_once()
    text = gateway.send_message.call_args.kwargs["text"]
    assert "✅" in text
    assert "hello from script" in text


# ---------------------------------------------------------------------------
# query_action end-to-end
# ---------------------------------------------------------------------------

async def test_query_action_full_flow(tmp_path, monkeypatch):
    """Full flow: query_action → search → confirm Yes → discovery → plan → approve → execute → report."""
    monkeypatch.setattr(config, "WORKSPACE", tmp_path)
    monkeypatch.setattr(config, "BACKUPS_DIR", tmp_path / ".backups")
    (tmp_path / ".backups").mkdir()

    # Create the file that will be found and rewritten
    cover_letter = tmp_path / "cover_letter_draft.txt"
    cover_letter.write_text("Dear Hiring Manager, I want to apply for this role.")

    engine = get_engine(db_path=tmp_path / "e2e_qa.db")
    create_tables(engine)

    # LLM mock: intake returns query_action; planning returns rewritten content
    llm = MagicMock()

    def _llm_call(system, user, tools=None, model=None):
        tool_name = (tools or [{}])[0].get("name", "")
        if tool_name == "create_task_object":
            return {
                "type": "tool_use",
                "input": {
                    "task_type": "query_action",
                    "query_intent": "find the cover letter file",
                    "action_instruction": "rewrite to be more formal for a senior engineering role",
                    "implied_task_type": "rewrite_file",
                    "instruction": "find my cover letter and rewrite it formally",
                    "risk_level": "high",
                    "requires_clarification": False,
                },
            }
        # Planning: generate_rewritten_content
        return {
            "type": "tool_use",
            "input": {
                "rewritten_content": "Dear Hiring Manager, I formally wish to apply.",
                "change_summary": "Made more formal and professional.",
            },
        }

    llm.call = MagicMock(side_effect=_llm_call)

    gateway = MagicMock()
    gateway.send_message = AsyncMock()
    gateway.send_approval_request = AsyncMock()
    gateway.send_confirmation = AsyncMock()
    gateway.send_status = AsyncMock()

    coord = _build_coordinator(engine, llm, gateway)

    # Mock run_search_for_action to return the cover letter
    coord.query_agent.run_search_for_action = MagicMock(return_value={
        "file_found": True,
        "file_path": str(cover_letter),
        "file_name": "cover_letter_draft.txt",
        "confidence": 0.95,
        "summary": "a cover letter for a hiring manager, last modified recently",
    })

    # Start pipeline
    job_id = await coord.handle_new_message("find my cover letter and rewrite it formally")

    # After search: job should be CONFIRMING
    assert get_job(job_id, engine=engine).status == JobStatus.CONFIRMING
    gateway.send_confirmation.assert_called_once()

    # User says Yes (file confirmed)
    await coord.handle_approval(job_id, approved=True)

    # After confirmation: job should be AWAITING_APPROVAL (plan built, waiting for exec approval)
    assert get_job(job_id, engine=engine).status == JobStatus.AWAITING_APPROVAL

    # target_path is the found file
    job = get_job(job_id, engine=engine)
    assert "cover_letter_draft.txt" in job.target_path
    assert job.task_type == "rewrite_file"

    # User approves execution
    await coord.handle_approval(job_id, approved=True)

    # Job completed
    assert get_job(job_id, engine=engine).status == JobStatus.COMPLETED
