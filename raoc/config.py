"""Central configuration — all paths, constants, and settings for RAOC.

Every module imports from here. Nothing is hardcoded elsewhere.
"""

from datetime import datetime
from pathlib import Path

# ── Workspace paths ───────────────────────────────────────────────
HOME            = Path.home()
WORKSPACE       = HOME / 'raoc_workspace'
BACKUPS_DIR     = WORKSPACE / '.backups'
SCRIPTS_DIR     = WORKSPACE / 'scripts'
SCREENSHOTS_DIR = WORKSPACE / 'screenshots'

# ── Project paths ─────────────────────────────────────────────────
PROJECT_ROOT    = Path(__file__).parent.parent
DATA_DIR        = PROJECT_ROOT / 'data'
DB_PATH         = DATA_DIR / 'raoc.db'
LOG_PATH        = DATA_DIR / 'raoc.log'
ZONE_CONFIG     = PROJECT_ROOT / 'zone_config.yaml'

# PDF rewrite produces a DOCX. Format changes from PDF to DOCX.
# This is intentional — communicated to user in plan preview and report.
PDF_OUTPUT_EXTENSION = '.docx'

# Blocks within this fraction of original character count are safe for
# in-place rewriting. Blocks exceeding this trigger DOCX fallback.
PDF_REWRITE_LENGTH_TOLERANCE = 0.15

# If more than this fraction of sampled bytes are non-printable, treat as binary.
MAX_BINARY_NONPRINTABLE_RATIO = 0.10

# ── Execution limits ──────────────────────────────────────────────
MAX_FILE_SIZE_CHARS = 50_000
MAX_COMMAND_TIMEOUT = 30        # seconds
MAX_OUTPUT_CHARS    = 10_240    # truncate stdout/stderr

# ── Blocked command patterns ──────────────────────────────────────
BLOCKED_PATTERNS = [
    'rm -rf', 'sudo', 'chmod', 'chown',
    'mkfs', 'dd if=', '> /dev/', 'kill -9',
]

# ── LLM config ────────────────────────────────────────────────────
LLM_MODEL      = 'claude-sonnet-4-5'
LLM_MAX_TOKENS = 2048
NARRATOR_MODEL = 'claude-haiku-4-5-20251001'  # fast, cheap; used for all status narration

# Ordering delays: let background narration arrive before the next user-visible message
NARRATION_DELAY_BEFORE_PLAN: float      = 1.5  # seconds before plan preview is sent
NARRATION_DELAY_BEFORE_EXECUTION: float = 1.0  # seconds before execution starts

# ── Filename utilities ────────────────────────────────────────────

def make_timestamped_stem(original_name: str, created_at: datetime) -> str:
    """Return the file stem with a job timestamp appended.

    The timestamp is derived from created_at, never from datetime.now().
    Example: make_timestamped_stem('notes.txt', dt) → 'notes_20260324_143022'
    """
    ts = created_at.strftime('%Y%m%d_%H%M%S')
    stem = Path(original_name).stem
    return f"{stem}_{ts}"


# ── Keychain keys ─────────────────────────────────────────────────
KEYCHAIN_SERVICE          = 'raoc'
KEYCHAIN_ANTHROPIC_KEY    = 'anthropic_api_key'
KEYCHAIN_TELEGRAM_TOKEN   = 'telegram_bot_token'
KEYCHAIN_TELEGRAM_USER_ID = 'telegram_user_id'