"""QueryAgent — answers read-only information requests about the workspace.

Searches metadata first. Only reads file content when metadata cannot answer.
Never modifies any file. Never executes any command.
"""

import asyncio
import json
import logging
from pathlib import Path

from raoc import config
from raoc.db import queries
from raoc.models.job import JobStatus

logger = logging.getLogger(__name__)


def _fire(coro) -> None:
    """Run an async coroutine from synchronous code."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        asyncio.run(coro)


_METADATA_SYSTEM = (
    "You are answering a question about a user's workspace based "
    "on file metadata only (names, sizes, dates, extensions). "
    "Answer directly and specifically using only the metadata. "
    "If the question cannot be answered from metadata alone, "
    "respond with exactly: CONTENT_SEARCH_NEEDED "
    "Do not answer partially — either answer fully or request "
    "content search."
)

_CONTENT_SYSTEM = (
    "You are searching a user's workspace files to answer their "
    "question. You have been given the content of candidate files. "
    "Answer the question directly and specifically. If the answer "
    "is in a specific file, name that file. If the answer spans "
    "multiple files, summarize what each relevant file contains. "
    "If the answer is not found in any file, say so plainly. "
    "Never make up information that is not in the files provided."
)

_TEXT_EXTENSIONS = {
    '.txt', '.md', '.py', '.sh', '.json', '.csv',
    '.html', '.xml', '.yaml', '.yml', '.log',
}

_SEARCH_SYSTEM = (
    "You are searching a user's workspace to find a specific file. "
    "You are given file metadata (names, sizes, modification dates, extensions). "
    "Based on the metadata, identify the file that best matches the search query. "
    "If you can identify the file from its name or extension alone, return it. "
    "If no plausible candidate exists, set file_found to false."
)

_SEARCH_TOOL = {
    "name": "report_file_search_result",
    "description": "Report the result of searching the workspace metadata for a specific file.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_found": {
                "type": "boolean",
                "description": "True if a matching file was identified in the workspace.",
            },
            "file_path": {
                "type": ["string", "null"],
                "description": "Full absolute path to the found file. Null if not found.",
            },
            "file_name": {
                "type": ["string", "null"],
                "description": "Just the filename (e.g. 'resume_draft.docx'). Null if not found.",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score 0.0-1.0 that this is the right file.",
            },
            "summary": {
                "type": "string",
                "description": (
                    "Plain language 1-2 sentence description of what was found and why it "
                    "matches. E.g. 'cover_letter_draft.docx — a cover letter for an "
                    "engineering role, last modified 5 days ago.' If not found, say so."
                ),
            },
        },
        "required": ["file_found", "confidence", "summary"],
    },
}


class QueryAgent:
    """Answers information questions about the workspace.

    Read-only. Never modifies anything. Never executes anything.
    Uses HostSampler for metadata and file reading.
    Uses LLMClient to interpret results and form answers.
    """

    def __init__(self, db, sampler, llm, gateway) -> None:
        """Initialise with database engine, sampler, LLM client, and gateway."""
        self.db = db if (db is None or hasattr(db, "connect")) else None
        self.sampler = sampler
        self.llm = llm
        self.gateway = gateway

    def run(self, job_id: str) -> str:
        """Answer the query and send the result to the user.

        Returns the answer as a plain-language string.
        Advances job status to REPORTING then COMPLETED.
        Writes audit entries at start and end.
        """
        try:
            # 1. Read job and query_intent
            job = queries.get_job(job_id, engine=self.db)
            query_intent = job.query_intent or job.raw_request

            # 2. Write audit: query started
            queries.write_audit(job_id, "query_started", engine=self.db)

            # 3. Metadata scan of workspace
            metadata = self.sampler.sample_directory(config.WORKSPACE)

            # 4. Ask LLM: can this question be answered from metadata alone?
            metadata_user = (
                f"Query: {query_intent}\n\n"
                f"Workspace metadata:\n{json.dumps(metadata, default=str, indent=2)}"
            )
            metadata_response = self.llm.call(
                system=_METADATA_SYSTEM,
                user=metadata_user,
            )
            answer_text = (
                metadata_response.get("text", "")
                if isinstance(metadata_response, dict)
                else str(metadata_response)
            )
            answer_text = answer_text.strip()

            # 5. If metadata sufficient, use this answer
            if "CONTENT_SEARCH_NEEDED" not in answer_text:
                answer = answer_text
            else:
                # 6. Content search — read only candidate files
                answer = self._search_content(query_intent, metadata)

            # 7. Update status to REPORTING
            queries.update_job_status(job_id, JobStatus.REPORTING, engine=self.db)

            # 8. Send answer to phone
            _fire(self.gateway.send_message(text=answer))

            # 9. Update status to COMPLETED
            queries.update_job_status(job_id, JobStatus.COMPLETED, engine=self.db)
            queries.write_audit(job_id, "query_complete", engine=self.db)

            return answer

        except Exception as exc:
            queries.update_job_status(
                job_id, JobStatus.FAILED, error=str(exc), engine=self.db
            )
            queries.write_audit(job_id, "job_failed", detail=str(exc), engine=self.db)
            raise

    def run_search_for_action(self, job_id: str) -> dict:
        """Search for the file described in query_intent for a query_action job.

        Returns a result dict with keys:
          file_found (bool), file_path (str|None), file_name (str|None),
          confidence (float), summary (str).

        If file not found: sends a not-found message to the user and sets job
        status to AWAITING_APPROVAL. The caller must check file_found.
        """
        job = queries.get_job(job_id, engine=self.db)
        query_intent = job.query_intent or job.raw_request

        queries.write_audit(job_id, "search_started", engine=self.db)

        # Metadata scan
        metadata = self.sampler.sample_directory(config.WORKSPACE)

        # Ask LLM to find the file using structured tool_use
        search_user = (
            f"Search query: {query_intent}\n\n"
            f"Workspace metadata:\n{json.dumps(metadata, default=str, indent=2)}"
        )
        response = self.llm.call(
            system=_SEARCH_SYSTEM,
            user=search_user,
            tools=[_SEARCH_TOOL],
        )

        tool_input = response.get("input", {}) if isinstance(response, dict) else {}
        file_found = bool(tool_input.get("file_found", False))
        file_path = tool_input.get("file_path") or None
        file_name = tool_input.get("file_name") or None
        confidence = float(tool_input.get("confidence", 0.0))
        summary = tool_input.get("summary", "")

        result = {
            "file_found": file_found,
            "file_path": file_path,
            "file_name": file_name,
            "confidence": confidence,
            "summary": summary,
        }

        if not file_found:
            queries.write_audit(job_id, "search_not_found", detail=query_intent, engine=self.db)
            queries.update_job_status(job_id, JobStatus.AWAITING_APPROVAL, engine=self.db)
            not_found_msg = (
                f"I searched your workspace but couldn't find {query_intent}. "
                f"Could you point me to the file directly?"
            )
            _fire(self.gateway.send_message(text=not_found_msg))
        else:
            queries.write_audit(
                job_id, "search_found", detail=f"{file_name}: {summary}", engine=self.db
            )

        return result

    def _search_content(self, query_intent: str, metadata: dict) -> str:
        """Read candidate files and ask Claude to find the answer.

        Candidates are files whose extension suggests they contain readable text.
        Reads at most 10 candidates to avoid excessive token usage.
        """
        files = metadata.get("files", [])

        # Filter to text-readable candidates; fall back to all files if none
        candidates = [
            f for f in files
            if Path(f["name"]).suffix.lower() in _TEXT_EXTENSIONS
]
        if not candidates:
            candidates = files

        content_parts = []
        for f in candidates[:10]:
            try:
                content = self.sampler.read_text_file(Path(f["path"]))
                content_parts.append(f"File: {f['name']}\n---\n{content}")
            except Exception:
                pass

        if not content_parts:
            return "I could not find any readable files in the workspace to answer your question."

        content_user = (
            f"Query: {query_intent}\n\n"
            f"File contents:\n\n" + "\n\n".join(content_parts)
        )
        content_response = self.llm.call(
            system=_CONTENT_SYSTEM,
            user=content_user,
        )
        answer = (
            content_response.get("text", "")
            if isinstance(content_response, dict)
            else str(content_response)
        )
        return answer.strip() or "I could not find an answer in the workspace files."
