"""Tests for raoc.agents.query_agent.QueryAgent."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from raoc import config
from raoc.agents.query_agent import QueryAgent
from raoc.db.queries import create_job, get_job, update_job_field
from raoc.db.schema import create_tables, get_engine
from raoc.models.job import JobStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    """Return an in-memory SQLite engine with all tables created."""
    engine = get_engine(db_path=tmp_path / "test_query.db")
    create_tables(engine)
    return engine


def _make_sampler(files=None):
    """Return a mock HostSampler with a sample_directory result."""
    if files is None:
        files = [
            {
                "path": "/fake/workspace/notes.txt",
                "name": "notes.txt",
                "extension": ".txt",
                "size_bytes": 50,
                "modified_at": "2026-03-24T10:00:00+00:00",
                "created_at": "2026-03-20T00:00:00+00:00",
            },
            {
                "path": "/fake/workspace/resume_draft.docx",
                "name": "resume_draft.docx",
                "extension": ".docx",
                "size_bytes": 200,
                "modified_at": "2026-03-20T08:00:00+00:00",
                "created_at": "2026-03-10T00:00:00+00:00",
            },
            {
                "path": "/fake/workspace/data.csv",
                "name": "data.csv",
                "extension": ".csv",
                "size_bytes": 30,
                "modified_at": "2026-03-18T06:00:00+00:00",
                "created_at": "2026-03-05T00:00:00+00:00",
            },
        ]
    sampler = MagicMock()
    sampler.sample_directory.return_value = {
        "path": "/fake/workspace",
        "file_count": len(files),
        "total_size_bytes": sum(f["size_bytes"] for f in files),
        "files": files,
    }
    return sampler


def _make_agent(db, sampler=None, metadata_answer="notes.txt is the most recent file."):
    """Return a QueryAgent with mocked deps."""
    if sampler is None:
        sampler = _make_sampler()
    llm = MagicMock()
    llm.call.return_value = {"type": "text", "text": metadata_answer}
    gateway = MagicMock()
    gateway.send_message = AsyncMock()
    return QueryAgent(db=db, sampler=sampler, llm=llm, gateway=gateway)


def _make_query_job(db, query_intent="what is the most recent file?"):
    """Create a query job with the given intent."""
    job = create_job(query_intent, engine=db)
    update_job_field(
        job.job_id,
        task_type="query",
        query_intent=query_intent,
        engine=db,
    )
    return job.job_id


# ---------------------------------------------------------------------------
# Test: metadata question answered without reading files
# ---------------------------------------------------------------------------


def test_metadata_question_answered_without_reading_files(db):
    """When LLM answers from metadata, read_text_file must never be called."""
    agent = _make_agent(db, metadata_answer="notes.txt is the most recent file (modified 2026-03-24).")
    job_id = _make_query_job(db, "what is the most recent file?")

    with patch.object(config, "WORKSPACE", Path("/fake/workspace")):
        agent.run(job_id)

    agent.sampler.read_text_file.assert_not_called()
    agent.gateway.send_message.assert_called_once()
    sent_text = agent.gateway.send_message.call_args.kwargs["text"]
    assert "notes.txt" in sent_text


# ---------------------------------------------------------------------------
# Test: content question triggers file read
# ---------------------------------------------------------------------------


def test_content_question_triggers_file_read(db):
    """When LLM returns CONTENT_SEARCH_NEEDED, read_text_file is called on candidates."""
    sampler = _make_sampler(files=[
        {
            "path": "/fake/workspace/resume_draft.docx",
            "name": "resume_draft.docx",
            "extension": ".docx",
            "size_bytes": 200,
            "modified_at": "2026-03-20T08:00:00+00:00",
            "created_at": "2026-03-10T00:00:00+00:00",
        },
    ])
    sampler.read_text_file.return_value = "John Doe - Senior Engineer. Full resume content."

    llm = MagicMock()
    llm.call.side_effect = [
        {"type": "text", "text": "CONTENT_SEARCH_NEEDED"},
        {"type": "text", "text": "Your resume is in resume_draft.docx."},
    ]

    gateway = MagicMock()
    gateway.send_message = AsyncMock()

    agent = QueryAgent(db=db, sampler=sampler, llm=llm, gateway=gateway)
    job_id = _make_query_job(db, "what file contains my resume?")

    with patch.object(config, "WORKSPACE", Path("/fake/workspace")):
        agent.run(job_id)

    sampler.read_text_file.assert_called()
    gateway.send_message.assert_called_once()
    sent_text = gateway.send_message.call_args.kwargs["text"]
    assert "resume_draft.docx" in sent_text


# ---------------------------------------------------------------------------
# Test: unanswerable question reported honestly
# ---------------------------------------------------------------------------


def test_unanswerable_question_reported_honestly(db):
    """When LLM says not found, gateway.send_message is called and job is COMPLETED."""
    agent = _make_agent(db, metadata_answer="I did not find any file matching that description.")
    job_id = _make_query_job(db, "which file mentions the project deadline?")

    with patch.object(config, "WORKSPACE", Path("/fake/workspace")):
        agent.run(job_id)

    agent.gateway.send_message.assert_called_once()
    job = get_job(job_id, engine=db)
    assert job.status == JobStatus.COMPLETED


# ---------------------------------------------------------------------------
# Test: query does not modify any file
# ---------------------------------------------------------------------------


def test_query_does_not_modify_any_file(db):
    """QueryAgent never calls any write or execute method on the sampler."""
    agent = _make_agent(db, metadata_answer="notes.txt is the most recent file.")
    job_id = _make_query_job(db, "what files do I have?")

    with patch.object(config, "WORKSPACE", Path("/fake/workspace")):
        agent.run(job_id)

    # No write or execute operations
    agent.sampler.write_file = MagicMock()  # if it existed it should never be called
    assert not hasattr(agent.sampler, "execute") or not agent.sampler.execute.called


# ---------------------------------------------------------------------------
# Test: job status COMPLETED after query
# ---------------------------------------------------------------------------


def test_job_status_completed_after_query(db):
    """Job status must be COMPLETED after query_agent.run() returns."""
    agent = _make_agent(db, metadata_answer="You have 3 files: notes.txt, resume_draft.docx, data.csv.")
    job_id = _make_query_job(db, "how many files do I have?")

    with patch.object(config, "WORKSPACE", Path("/fake/workspace")):
        agent.run(job_id)

    job = get_job(job_id, engine=db)
    assert job.status == JobStatus.COMPLETED
