"""Tests for raoc.agents.discovery.DiscoveryAgent."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from raoc import config
from raoc.agents.discovery import DiscoveryAgent
from raoc.db.queries import create_job, get_job, update_job_field
from raoc.db.schema import create_tables, get_engine
from raoc.models.job import JobStatus
from raoc.substrate.exceptions import ExtractionError, FileTooLargeError, ScopeViolationError, ZipFileDetectedError
from raoc.substrate.host_sampler import HostSampler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """In-memory SQLite engine with all tables created."""
    engine = get_engine(db_path=tmp_path / "test_discovery.db")
    create_tables(engine)
    return engine


def _mock_sampler(
    *,
    exists: bool = True,
    is_locked: bool = False,
    content: str = "hello world",
    file_content_error: Exception | None = None,
) -> MagicMock:
    """Build a HostSampler mock with sensible defaults."""
    sampler = MagicMock(spec=HostSampler)
    sampler.sample_file.return_value = {
        "path": "/fake/workspace/file_a.txt",
        "name": "file_a.txt",
        "extension": ".txt",
        "size_bytes": len(content),
        "modified_at": "2026-01-01T00:00:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00",
        "exists": exists,
        "is_locked": is_locked,
    }
    if file_content_error:
        sampler.read_text_file.side_effect = file_content_error
        sampler.extract_text_for_rewrite.side_effect = file_content_error
    else:
        sampler.read_text_file.return_value = content
        sampler.extract_text_for_rewrite.side_effect = lambda path: (content, 'text', path, [], 'text')
    return sampler


def _mock_llm_high_confidence(best_match: str) -> MagicMock:
    """LLM returns high-confidence match."""
    llm = MagicMock()
    llm.call.return_value = {
        "type": "tool_use",
        "input": {
            "best_match": best_match,
            "confidence": "high",
            "clarification_needed": False,
            "clarification_question": None,
        },
    }
    return llm


def _mock_llm_low_confidence(question: str) -> MagicMock:
    """LLM returns low confidence and requests clarification."""
    llm = MagicMock()
    llm.call.return_value = {
        "type": "tool_use",
        "input": {
            "best_match": None,
            "confidence": "low",
            "clarification_needed": True,
            "clarification_question": question,
        },
    }
    return llm


def _create_rewrite_job(db, target: str = "file_a.txt", scope_root: str | None = None) -> str:
    job = create_job(f"Rewrite {target}", engine=db)
    update_job_field(job.job_id, task_type="rewrite_file", target_path=target, scope_root=scope_root, engine=db)
    return job.job_id


def _create_run_job(db, target: str = "script_a.py", scope_root: str | None = None) -> str:
    job = create_job(f"Run {target}", engine=db)
    update_job_field(job.job_id, task_type="run_script", target_path=target, scope_root=scope_root, engine=db)
    return job.job_id


# ---------------------------------------------------------------------------
# File found by exact match — pipeline continues normally
# ---------------------------------------------------------------------------

class TestDiscoveryFileFound:
    """File found in workspace → ContextPackage returned, status PLANNING."""

    def test_returns_context_package(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "file_a.txt").write_text("hello world")

        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler(content="hello world")

        with patch.object(config, "WORKSPACE", workspace):
            agent = DiscoveryAgent(db=db, sampler=sampler)
            result = agent.run(job_id)

        assert result["job_id"] == job_id
        assert result["task_type"] == "rewrite_file"
        assert result["file_content"] == "hello world"
        assert result["script_exists"] is True
        assert "discovered_at" in result

    def test_status_advances_to_planning(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "file_a.txt").write_text("content")

        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler()

        with patch.object(config, "WORKSPACE", workspace):
            DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        assert get_job(job_id, engine=db).status == JobStatus.PLANNING


# ---------------------------------------------------------------------------
# File not found — LLM high confidence auto-resolves
# ---------------------------------------------------------------------------

class TestDiscoveryHighConfidenceResolve:
    """LLM returns confidence=high → pipeline continues, user never interrupted."""

    def test_returns_context_package(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "file_b.txt").write_text("actual content")

        # User typed "file_a.txt" but workspace has "file_b.txt"
        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler(content="actual content")
        llm = _mock_llm_high_confidence("file_b.txt")

        with patch.object(config, "WORKSPACE", workspace):
            result = DiscoveryAgent(db=db, sampler=sampler, llm=llm).run(job_id)

        assert result is not None
        assert result["job_id"] == job_id

    def test_status_advances_to_planning(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "file_b.txt").write_text("content")

        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler()
        llm = _mock_llm_high_confidence("file_b.txt")

        with patch.object(config, "WORKSPACE", workspace):
            DiscoveryAgent(db=db, sampler=sampler, llm=llm).run(job_id)

        assert get_job(job_id, engine=db).status == JobStatus.PLANNING


# ---------------------------------------------------------------------------
# File not found — LLM low confidence triggers clarification
# ---------------------------------------------------------------------------

class TestDiscoveryLowConfidenceClarification:
    """LLM returns confidence=low + clarification_needed → status AWAITING_APPROVAL."""

    def test_status_is_awaiting_approval(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "file_b.txt").write_text("content")

        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler()
        llm = _mock_llm_low_confidence("Which file did you mean — file_b.txt?")

        with patch.object(config, "WORKSPACE", workspace):
            result = DiscoveryAgent(db=db, sampler=sampler, llm=llm).run(job_id)

        assert result is None
        assert get_job(job_id, engine=db).status == JobStatus.AWAITING_APPROVAL

    def test_clarification_question_saved_to_job(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "file_b.txt").write_text("content")

        question = "Which file did you mean — file_b.txt?"
        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler()
        llm = _mock_llm_low_confidence(question)

        with patch.object(config, "WORKSPACE", workspace):
            DiscoveryAgent(db=db, sampler=sampler, llm=llm).run(job_id)

        updated = get_job(job_id, engine=db)
        assert updated.clarification_question == question

    def test_never_raises_runtime_error(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()

        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler()
        llm = _mock_llm_low_confidence("Which file?")

        with patch.object(config, "WORKSPACE", workspace):
            result = DiscoveryAgent(db=db, sampler=sampler, llm=llm).run(job_id)

        assert result is None  # returns None, does not raise


# ---------------------------------------------------------------------------
# File not found — no LLM match → workspace file list sent
# ---------------------------------------------------------------------------

class TestDiscoveryNoMatch:
    """No match at all → status AWAITING_APPROVAL, file list in clarification_question."""

    def test_status_is_awaiting_approval(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "file_b.txt").write_text("content")

        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler()
        # No LLM → falls through to file-list path

        with patch.object(config, "WORKSPACE", workspace):
            result = DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        assert result is None
        assert get_job(job_id, engine=db).status == JobStatus.AWAITING_APPROVAL

    def test_clarification_question_lists_workspace_files(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "file_b.txt").write_text("content")

        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler()

        with patch.object(config, "WORKSPACE", workspace):
            DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        updated = get_job(job_id, engine=db)
        # With no match in scope, clarification should mention the filename
        assert "file_a.txt" in updated.clarification_question

    def test_never_raises_runtime_error(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()

        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler()

        with patch.object(config, "WORKSPACE", workspace):
            # Must not raise even with empty workspace
            result = DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        assert result is None


# ---------------------------------------------------------------------------
# run_script with non-script file — script_exists must be False
# ---------------------------------------------------------------------------

class TestDiscoveryRunScriptNonScriptFile:
    """run_script resolved to a .txt file → script_exists=False, status PLANNING."""

    def test_script_exists_false_for_txt_file(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "data.txt").write_text("some data")

        job = create_job("Run data.txt", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="data.txt",
                         scope_root=str(workspace), engine=db)
        sampler = _mock_sampler(content="some data")

        with patch.object(config, "WORKSPACE", workspace):
            result = DiscoveryAgent(db=db, sampler=sampler).run(job.job_id)

        assert result["script_exists"] is False

    def test_status_planning_for_non_script_file(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "data.txt").write_text("some data")

        job = create_job("Run data.txt", engine=db)
        update_job_field(job.job_id, task_type="run_script", target_path="data.txt",
                         scope_root=str(workspace), engine=db)
        sampler = _mock_sampler(content="some data")

        with patch.object(config, "WORKSPACE", workspace):
            DiscoveryAgent(db=db, sampler=sampler).run(job.job_id)

        assert get_job(job.job_id, engine=db).status == JobStatus.PLANNING

    def test_script_exists_true_for_py_file(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "cleanup.py").write_text("print('hello')")

        job_id = _create_run_job(db, target="cleanup.py", scope_root=str(workspace))
        sampler = _mock_sampler(content="print('hello')")

        with patch.object(config, "WORKSPACE", workspace):
            result = DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        assert result["script_exists"] is True


# ---------------------------------------------------------------------------
# Scope violation — absolute path outside workspace
# ---------------------------------------------------------------------------

class TestDiscoveryScopeViolation:
    """Absolute path outside scope → NeedsPermission; forbidden → ScopeViolationError."""

    def test_outside_scope_returns_needs_permission(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        scope_dir = workspace / "documents"
        scope_dir.mkdir()

        job = create_job("Run data.txt", engine=db)
        # Set target to a path OUTSIDE scope_root but NOT forbidden
        outside_path = str(workspace / "other_folder" / "data.txt")
        # Pre-set scope_root so the early-set doesn't override it
        update_job_field(
            job.job_id,
            task_type="run_script",
            target_path=outside_path,
            scope_root=str(scope_dir),
            engine=db,
        )
        sampler = _mock_sampler()

        from raoc.models.scope import NeedsPermission
        from raoc.substrate.zone_resolver import ZoneResolver

        with patch.object(config, "WORKSPACE", workspace):
            agent = DiscoveryAgent(db=db, sampler=sampler, zone_resolver=ZoneResolver())
            result = agent.run(job.job_id)

        assert isinstance(result, NeedsPermission)
        assert result.reason == 'path_outside_scope'

    def test_forbidden_path_raises_scope_violation(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()

        job = create_job("Read ~/.ssh/id_rsa", engine=db)
        from pathlib import Path as P
        forbidden_path = str(P.home() / ".ssh" / "id_rsa")
        update_job_field(job.job_id, task_type="run_script", target_path=forbidden_path, engine=db)
        sampler = _mock_sampler()

        from raoc.substrate.zone_resolver import ZoneResolver

        with patch.object(config, "WORKSPACE", workspace):
            with pytest.raises(ScopeViolationError):
                DiscoveryAgent(db=db, sampler=sampler, zone_resolver=ZoneResolver()).run(job.job_id)

        assert get_job(job.job_id, engine=db).status == JobStatus.FAILED


# ---------------------------------------------------------------------------
# Locked file
# ---------------------------------------------------------------------------

class TestDiscoveryLockedFile:
    """Locked file → status FAILED, correct message."""

    def test_status_is_failed(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "file_a.txt").write_text("content")

        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler(is_locked=True)

        with patch.object(config, "WORKSPACE", workspace):
            with pytest.raises(RuntimeError, match="open in another application"):
                DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        assert get_job(job_id, engine=db).status == JobStatus.FAILED


# ---------------------------------------------------------------------------
# File too large
# ---------------------------------------------------------------------------

class TestDiscoveryFileTooLarge:
    """File exceeds size limit → status FAILED."""

    def test_status_is_failed(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "file_a.txt").write_text("x")

        job_id = _create_rewrite_job(db, target="file_a.txt", scope_root=str(workspace))
        sampler = _mock_sampler(
            file_content_error=FileTooLargeError("File exceeds 50000 chars: 99999")
        )

        with patch.object(config, "WORKSPACE", workspace):
            with pytest.raises(RuntimeError):
                DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        assert get_job(job_id, engine=db).status == JobStatus.FAILED


# ---------------------------------------------------------------------------
# Non-text file for rewrite
# ---------------------------------------------------------------------------

class TestDiscoveryNonTextRewrite:
    """Binary file for rewrite task → ExtractionError → status FAILED."""

    def test_status_is_failed(self, db, tmp_path):
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "image.png").write_bytes(b"\x89PNG")

        job = create_job("Rewrite image.png", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path="image.png",
                         scope_root=str(workspace), engine=db)
        sampler = _mock_sampler(
            file_content_error=ExtractionError(
                "Could not extract text from image.png. "
                "The file appears to be a binary format that cannot be rewritten as text."
            )
        )

        with patch.object(config, "WORKSPACE", workspace):
            with pytest.raises(RuntimeError):
                DiscoveryAgent(db=db, sampler=sampler).run(job.job_id)

        assert get_job(job.job_id, engine=db).status == JobStatus.FAILED


# ---------------------------------------------------------------------------
# PDF format change — output_path is .docx, format_change is True
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# ZIP file detection
# ---------------------------------------------------------------------------

class TestDiscoveryZipDetection:
    """ZIP detection → status AWAITING_APPROVAL, clarification_question set, zip_source_path set."""

    def test_zip_detection_sets_awaiting_approval(self, db, tmp_path):
        """When extract_text_for_rewrite raises ZipFileDetectedError, job goes to AWAITING_APPROVAL."""
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "archive.zip").write_bytes(b'PK\x03\x04')  # placeholder

        job_id = _create_rewrite_job(db, target="archive.zip", scope_root=str(workspace))
        sampler = MagicMock(spec=HostSampler)
        sampler.sample_file.return_value = {
            "path": str(workspace / "archive.zip"),
            "name": "archive.zip",
            "extension": ".zip",
            "size_bytes": 10,
            "modified_at": "2026-01-01T00:00:00+00:00",
            "created_at": "2026-01-01T00:00:00+00:00",
            "exists": True,
            "is_locked": False,
        }
        sampler.extract_text_for_rewrite.side_effect = ZipFileDetectedError(
            workspace / "archive.zip",
            ["readme.txt", "data.csv"],
        )

        with patch.object(config, "WORKSPACE", workspace):
            result = DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        assert result is None
        assert get_job(job_id, engine=db).status == JobStatus.AWAITING_APPROVAL

    def test_zip_detection_sets_clarification_question(self, db, tmp_path):
        """clarification_question contains the list of ZIP contents."""
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "archive.zip").write_bytes(b'PK\x03\x04')  # must exist for _resolve_path

        job_id = _create_rewrite_job(db, target="archive.zip", scope_root=str(workspace))
        sampler = MagicMock(spec=HostSampler)
        sampler.sample_file.return_value = {
            "path": str(workspace / "archive.zip"),
            "name": "archive.zip",
            "extension": ".zip",
            "size_bytes": 10,
            "modified_at": "2026-01-01T00:00:00+00:00",
            "created_at": "2026-01-01T00:00:00+00:00",
            "exists": True,
            "is_locked": False,
        }
        sampler.extract_text_for_rewrite.side_effect = ZipFileDetectedError(
            workspace / "archive.zip",
            ["readme.txt", "data.csv"],
        )

        with patch.object(config, "WORKSPACE", workspace):
            DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        updated = get_job(job_id, engine=db)
        assert "readme.txt" in updated.clarification_question
        assert "data.csv" in updated.clarification_question
        assert updated.zip_source_path is not None


class TestDiscoveryPdfFormatChange:
    """PDF rewrite → context_package has format_change=True and output_path ends in .docx."""

    def test_discovery_pdf_sets_format_change_true(self, db, tmp_path):
        """Discovery of a .pdf file sets format_change=True in the context package."""
        from reportlab.pdfgen import canvas as rl_canvas
        from raoc.substrate.host_sampler import HostSampler

        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()

        # Create a real PDF in the workspace
        pdf_path = workspace / "report.pdf"
        c = rl_canvas.Canvas(str(pdf_path))
        c.drawString(100, 750, "Report content for testing")
        c.save()

        job = create_job("Rewrite report.pdf to be more formal", engine=db)
        update_job_field(job.job_id, task_type="rewrite_file", target_path="report.pdf",
                         scope_root=str(workspace), engine=db)

        with patch.object(config, "WORKSPACE", workspace):
            with patch.object(config, "BACKUPS_DIR", workspace / ".backups"):
                sampler = HostSampler()
                result = DiscoveryAgent(db=db, sampler=sampler).run(job.job_id)

        assert result is not None
        assert result["format_change"] is True
        assert result["output_path"].endswith(".docx")


# ---------------------------------------------------------------------------
# Empty extracted content — pipeline must stop at discovery
# ---------------------------------------------------------------------------

class TestDiscoveryEmptyContent:
    """extract_text_for_rewrite returns empty string → status FAILED, RuntimeError raised."""

    def test_empty_extracted_content_fails_discovery(self, db, tmp_path):
        """When extracted content is empty, discovery fails with a plain-language message
        that includes the word 'empty' and the real file size in bytes."""
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()

        # Create a real DOCX file with actual bytes on disk
        from docx import Document
        doc = Document()
        doc.add_paragraph("Some content that the extractor will silently drop.")
        docx_path = workspace / "report.docx"
        doc.save(str(docx_path))
        real_size = docx_path.stat().st_size
        assert real_size > 0, "Test setup: DOCX must have non-zero size on disk"

        job_id = _create_rewrite_job(db, target="report.docx", scope_root=str(workspace))

        # Mock sampler: sample_file reports real size; extract_text_for_rewrite returns ""
        sampler = MagicMock(spec=HostSampler)
        sampler.sample_file.return_value = {
            "path": str(docx_path),
            "name": "report.docx",
            "extension": ".docx",
            "size_bytes": real_size,
            "modified_at": "2026-01-01T00:00:00+00:00",
            "created_at": "2026-01-01T00:00:00+00:00",
            "exists": True,
            "is_locked": False,
        }
        sampler.extract_text_for_rewrite.side_effect = lambda path: (
            "",        # empty content
            "docx",
            path,
            [],
            "text",
        )

        with patch.object(config, "WORKSPACE", workspace):
            with pytest.raises(RuntimeError) as exc_info:
                DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        job = get_job(job_id, engine=db)
        assert job.status == JobStatus.FAILED
        # Assert exact error category: extraction-failed (not "empty")
        assert "not empty on disk" in (job.error_message or "")
        assert "text extraction returned no content" in (job.error_message or "")
        assert "docx" in (job.error_message or "").lower()
        assert str(real_size) in (job.error_message or "")


# ---------------------------------------------------------------------------
# Message assertion tests — verify exact error categories
# ---------------------------------------------------------------------------

class TestDiscoveryErrorMessages:
    """Verify the exact user-facing message for each failure category."""

    def test_zero_byte_file_says_empty(self, db, tmp_path):
        """0-byte file → message says 'empty on disk'."""
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        zero_file = workspace / "empty.txt"
        zero_file.write_text("")  # 0 bytes

        job_id = _create_rewrite_job(db, target="empty.txt", scope_root=str(workspace))

        sampler = MagicMock(spec=HostSampler)
        sampler.sample_file.return_value = {
            "path": str(zero_file),
            "name": "empty.txt",
            "extension": ".txt",
            "size_bytes": 0,
            "modified_at": "2026-01-01T00:00:00+00:00",
            "created_at": "2026-01-01T00:00:00+00:00",
            "exists": True,
            "is_locked": False,
        }
        sampler.extract_text_for_rewrite.side_effect = lambda path: ("", "text", path, [], "text")

        with patch.object(config, "WORKSPACE", workspace):
            with pytest.raises(RuntimeError) as exc_info:
                DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        assert "empty on disk" in str(exc_info.value)
        assert "0 bytes" in str(exc_info.value)
        job = get_job(job_id, engine=db)
        assert "empty on disk" in (job.error_message or "")

    def test_extraction_failed_says_extraction_not_empty(self, db, tmp_path):
        """Non-empty file with blank extraction → message says 'not empty on disk, but
        text extraction returned no content' with format and method."""
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "report.docx").write_bytes(b"x" * 42000)

        job_id = _create_rewrite_job(db, target="report.docx", scope_root=str(workspace))

        sampler = MagicMock(spec=HostSampler)
        sampler.sample_file.return_value = {
            "path": str(workspace / "report.docx"),
            "name": "report.docx",
            "extension": ".docx",
            "size_bytes": 42000,
            "modified_at": "2026-01-01T00:00:00+00:00",
            "created_at": "2026-01-01T00:00:00+00:00",
            "exists": True,
            "is_locked": False,
        }
        sampler.extract_text_for_rewrite.side_effect = lambda path: ("", "docx", path, [], "text")

        with patch.object(config, "WORKSPACE", workspace):
            with pytest.raises(RuntimeError) as exc_info:
                DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        msg = str(exc_info.value)
        assert "not empty on disk" in msg
        assert "text extraction returned no content" in msg
        assert "42000" in msg
        assert "docx" in msg.lower()

    def test_outside_scope_says_permission_not_unreadable(self, db, tmp_path):
        """Outside-scope path → NeedsPermission result, not 'empty' or 'unreadable'."""
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        scope_dir = workspace / "documents"
        scope_dir.mkdir()
        other_dir = workspace / "other"
        other_dir.mkdir()
        (other_dir / "data.txt").write_text("content")

        from raoc.models.scope import NeedsPermission
        from raoc.substrate.zone_resolver import ZoneResolver

        job = create_job("Rewrite data.txt", engine=db)
        update_job_field(
            job.job_id, task_type="rewrite_file",
            target_path=str(other_dir / "data.txt"),
            scope_root=str(scope_dir), engine=db,
        )

        sampler = _mock_sampler()

        with patch.object(config, "WORKSPACE", workspace):
            result = DiscoveryAgent(
                db=db, sampler=sampler, zone_resolver=ZoneResolver(),
            ).run(job.job_id)

        assert isinstance(result, NeedsPermission)
        assert result.reason == 'path_outside_scope'
        # Must NOT contain "empty" or "unreadable"
        assert 'empty' not in str(result).lower()
        assert 'unreadable' not in str(result).lower()

    def test_filename_no_scope_asks_clarification(self, db, tmp_path):
        """Filename-only with no scope_root → asks for clarification, not broad search."""
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        (workspace / "report.docx").write_text("content")

        # No scope_root set
        job_id = _create_rewrite_job(db, target="report.docx")
        sampler = _mock_sampler()

        with patch.object(config, "WORKSPACE", workspace):
            result = DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        assert result is None
        job = get_job(job_id, engine=db)
        assert job.status == JobStatus.AWAITING_APPROVAL
        assert "report.docx" in (job.clarification_question or "")
        assert "folder" in (job.clarification_question or "").lower()

    def test_duplicate_filenames_asks_clarification(self, db, tmp_path):
        """Same filename in two folders → asks user to choose."""
        workspace = tmp_path / "raoc_workspace"
        workspace.mkdir()
        dir_a = workspace / "a"
        dir_a.mkdir()
        dir_b = workspace / "b"
        dir_b.mkdir()
        (dir_a / "report.txt").write_text("version A")
        (dir_b / "report.txt").write_text("version B")

        job_id = _create_rewrite_job(db, target="report.txt", scope_root=str(workspace))
        sampler = _mock_sampler()

        with patch.object(config, "WORKSPACE", workspace):
            result = DiscoveryAgent(db=db, sampler=sampler).run(job_id)

        assert result is None
        job = get_job(job_id, engine=db)
        assert job.status == JobStatus.AWAITING_APPROVAL
        assert "report.txt" in (job.clarification_question or "")
        assert "2" in (job.clarification_question or "")  # found 2 files

