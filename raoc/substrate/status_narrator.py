"""StatusNarrator — generates plain-language pipeline status messages via Claude.

All narration uses NARRATOR_MODEL (Haiku) — never the main LLM_MODEL.
Narrate failures are swallowed: the pipeline never stops due to a narration error.
"""

import json
import logging

from raoc import config

logger = logging.getLogger(__name__)

_NARRATOR_SYSTEM = (
    "You are the voice of a remote computer operator sending status updates to a user's phone. "
    "Your job is to narrate what is happening right now in plain language.\n\n"
    "Rules:\n"
    "- Maximum two sentences per message\n"
    "- Use the real filenames, sizes, and details from the context\n"
    "- No technical jargon — write for someone reading on a phone\n"
    "- No sycophantic openers — never start with Great, Sure, Certainly\n"
    "- Never mention Claude, AI, LLM, API, or model names\n"
    "- If something failed, say what failed and what state things are in now — plainly, "
    "without drama\n"
    "- Write in present tense — what is happening now, not what happened"
)


class StatusNarrator:
    """Generates plain-language status messages for each pipeline stage.

    Wraps LLMClient with a narration-focused system prompt and always uses
    NARRATOR_MODEL (Haiku).  All failures are swallowed — the pipeline never
    stops due to a narration error.
    """

    def __init__(self, llm) -> None:
        """Initialise with an LLMClient instance."""
        self.llm = llm

    def narrate(self, stage: str, context: dict) -> str:
        """Generate a plain-language status message for the given pipeline stage.

        Returns a non-empty string.  If the Claude call fails for any reason,
        returns a plain fallback built from the stage name.  Never raises.
        """
        user_message = (
            f"Pipeline stage: {stage}\n"
            f"Context: {json.dumps(context, default=str, indent=2)}\n"
            f"Write the status message for the user now."
        )
        try:
            response = self.llm.call(
                system=_NARRATOR_SYSTEM,
                user=user_message,
                model=config.NARRATOR_MODEL,
            )
            text = response.get("text", "") if isinstance(response, dict) else str(response)
            text = text.strip()
            if text:
                return text
        except Exception as exc:
            logger.warning("Narrator LLM call failed for stage %s: %s", stage, exc)

        # Fallback: plain string derived from stage name
        return stage.replace("_", " ").capitalize() + "."

    async def narrate_async(self, stage: str, context: dict) -> str:
        """Async version of narrate() — uses call_async to avoid blocking the event loop.

        Returns a non-empty string.  If the Claude call fails for any reason,
        returns a plain fallback built from the stage name.  Never raises.
        """
        user_message = (
            f"Pipeline stage: {stage}\n"
            f"Context: {json.dumps(context, default=str, indent=2)}\n"
            f"Write the status message for the user now."
        )
        try:
            response = await self.llm.call_async(
                system=_NARRATOR_SYSTEM,
                user=user_message,
                model=config.NARRATOR_MODEL,
            )
            text = response.get("text", "") if isinstance(response, dict) else str(response)
            text = text.strip()
            if text:
                return text
        except Exception as exc:
            logger.warning("Narrator async LLM call failed for stage %s: %s", stage, exc)

        return stage.replace("_", " ").capitalize() + "."
