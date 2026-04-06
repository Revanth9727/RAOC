# RAOC — Remote Autonomous OS Controller

Control your computer from your phone using Telegram and AI. Send a message like "rewrite my notes file to be more professional" or "run a script to analyze my data," review the plan on your phone, tap approve, and get proof it worked. Nothing happens without your explicit permission.

---

## Table of Contents

1. [What is RAOC?](#what-is-raoc)
2. [How it Works](#how-it-works)
3. [What You Need Before Starting](#what-you-need-before-starting)
4. [Step-by-Step Setup](#step-by-step-setup)
5. [How to Use RAOC](#how-to-use-raoc)
6. [Troubleshooting](#troubleshooting)
7. [Project Structure](#project-structure)
8. [Database Schema](#database-schema)
9. [Configuration](#configuration)
10. [Running Tests](#running-tests)

---

## What is RAOC?

RAOC is your personal AI assistant that runs on your Mac and listens for commands from your phone via Telegram. It can:

- **Rewrite files**: "Make my cover letter more formal"
- **Run scripts**: "Count how many lines are in all my Python files"
- **Find and act**: "Find my resume and update it with my new job"

### Why it's safe

- Every action requires your approval on your phone before it runs
- All file changes are backed up automatically
- It can only access files in your designated workspace folder
- Dangerous commands (like `rm -rf` or `sudo`) are blocked

---

## How it Works

```
You send a message from your phone
         ↓
   Telegram Bot receives it
         ↓
   Intake Agent understands what you want
         ↓
   Discovery Agent finds the target file
         ↓
   Planning Agent creates a step-by-step plan
         ↓
   Plan is sent to your phone → You tap Approve or Deny
         ↓
   Execution Agent carries out the plan
         ↓
   Verification Agent checks everything worked
         ↓
   Reporter sends you proof (screenshot + results)
```

If you tap **Deny**, nothing happens. Zero side effects.

---

## What You Need Before Starting

### 1. A Mac computer
RAOC is built for macOS (uses macOS Keychain for secrets).

### 2. Python 3.12 or newer
Check if you have it:
```bash
python3 --version
```
If you don't have it, install from [python.org](https://python.org) or use Homebrew:
```bash
brew install python@3.12
```

### 3. The `uv` package manager
This manages Python packages. Install it:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Or with Homebrew:
```bash
brew install uv
```

### 4. An Anthropic API key (for Claude AI)
RAOC uses Claude to understand your requests and rewrite files.

**How to get it:**
1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an account or sign in
3. Click "Get API keys"
4. Click "Create Key"
5. Copy the key (starts with `sk-ant-...`)
6. Save it somewhere safe — you'll need it in step 5

### 5. A Telegram bot token
This lets RAOC receive messages from your phone.

**How to get it:**
1. Open Telegram on your phone
2. Search for `@BotFather` and start a chat
3. Send the message: `/newbot`
4. Follow the prompts:
   - Give your bot a name (e.g., "My RAOC Bot")
   - Give it a username ending in "bot" (e.g., `myraoc_bot`)
5. BotFather will give you a token that looks like: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`
6. **Save this token** — you'll need it in step 5

### 6. Your Telegram User ID
RAOC only accepts messages from you (for security).

**How to get it:**
1. In Telegram, search for `@userinfobot`
2. Start a chat
3. It will reply with your user ID (a number like `123456789`)
4. **Save this number** — you'll need it in step 5

---

## Step-by-Step Setup

### Step 1: Download RAOC

```bash
cd ~
git clone <repository-url> raoc
cd raoc
```

### Step 2: Install Dependencies

```bash
uv sync
```
This downloads all required packages (may take a few minutes).

### Step 3: Create Your Workspace

This is where RAOC will read and write files:

```bash
mkdir -p ~/raoc_workspace/.backups
mkdir -p ~/raoc_workspace/scripts
mkdir -p ~/raoc_workspace/screenshots
```

Put any files you want RAOC to work on inside `~/raoc_workspace/`.

### Step 4: Install Additional Tools (Optional but Recommended)

For PDF support, install system dependencies:
```bash
# For PDF text extraction
# (Usually already installed on macOS)

# For OCR (reading text from images/PDFs)
brew install tesseract
```

### Step 5: Add Your API Keys to macOS Keychain

This is the most important step. Run these three commands in Terminal, replacing the placeholder values with your actual keys:

```bash
# Add your Anthropic API key
security add-generic-password -s raoc -a anthropic_api_key -w "sk-ant-your-actual-key-here"

# Add your Telegram bot token
security add-generic-password -s raoc -a telegram_bot_token -w "123456789:your-actual-token-here"

# Add your Telegram user ID
security add-generic-password -s raoc -a telegram_user_id -w "123456789"
```

**Important:**
- Replace the values in quotes with your actual keys
- Keep the quotes around the values
- The `-s raoc` part is required — this is the "service name"
- The `-a ...` part is the "account name" — don't change these

### Step 6: Create the Database

```bash
uv run python -c "from raoc.db.schema import create_tables, get_engine; create_tables(get_engine())"
```

You should see no output if this succeeds.

### Step 7: Test Your Setup

Run the tests to make sure everything works:
```bash
uv run pytest tests/ -v --tb=short
```

You should see something like:
```
=========================== 303 passed in 5.23s ===========================
```

### Step 8: Start RAOC

```bash
uv run python -m raoc.main
```

You should see:
```
2026/04/06 17:00:00 - telegram_bot - INFO - Bot started. Listening for messages...
```

**Leave this terminal window open.** This is RAOC running.

### Step 9: Test from Your Phone

1. Open Telegram
2. Find your bot (search for the username you created, like `@myraoc_bot`)
3. Send the message: `Hello`

You should get a response within a few seconds. If you do, everything is working!

---

## How to Use RAOC

### Example 1: Rewrite a File

1. Create a file in your workspace:
   ```bash
   echo "The meeting is at 3pm. We should discuss the project." > ~/raoc_workspace/notes.txt
   ```

2. From your phone, send:
   ```
   Rewrite notes.txt to be more professional
   ```

3. You'll see:
   - "Got it. Working on it..."
   - "Found notes.txt..."
   - A plan preview with Approve/Deny buttons

4. Tap **Approve**

5. You'll get a report showing what changed

### Example 2: Run a Script

From your phone:
```
Write a script that counts the files in my workspace and run it
```

RAOC will:
1. Write a Python script
2. Show you the script
3. Ask for approval
4. Run it after you approve
5. Send you the output

### Example 3: Find and Act

From your phone:
```
Find my cover letter and rewrite it for a senior engineering role
```

RAOC will:
1. Search your workspace for files that look like a cover letter
2. Ask you to confirm it found the right file
3. Build a rewrite plan
4. Ask for approval
5. Execute and report back

### Supported File Types

RAOC can rewrite:
- Plain text files (.txt, .md, .py, .sh, etc.)
- CSV files
- JSON files
- Microsoft Word documents (.docx)
- PDF files (converts to .docx for editing)
- ZIP files (extracts and asks which file to work on)

---

## Troubleshooting

### "security add-generic-password" fails

**Problem:** You get an error when adding API keys.

**Solution:** Try adding the `-T` flag to allow Terminal access:
```bash
security add-generic-password -s raoc -a anthropic_api_key -w "your-key" -T
```

Or add via the GUI:
1. Open "Keychain Access" app
2. Click "login" keychain
3. Click "+" to add
4. Keychain Item Name: `raoc`
5. Account Name: `anthropic_api_key`
6. Password: your actual API key

### Bot doesn't respond to messages

**Check 1:** Is RAOC running? Look at the terminal window where you started it.

**Check 2:** Is your Telegram User ID correct? The bot ignores messages from anyone else.

**Check 3:** Check the logs:
```bash
tail -f ~/raoc/data/raoc.log
```

### "uv: command not found"

**Solution:** Install uv or use the full path:
```bash
# Find where uv was installed
which uv

# Or restart your terminal and try again
```

### Tests fail

**Problem:** Some tests fail with import errors.

**Solution:** Make sure you ran `uv sync` in the raoc directory:
```bash
cd ~/raoc
uv sync
```

### "Permission denied" when RAOC tries to write files

**Solution:** Check your workspace permissions:
```bash
ls -la ~/raoc_workspace
```

The owner should be your user. If not:
```bash
sudo chown -R $(whoami) ~/raoc_workspace
```

### API key errors

**Problem:** RAOC says it can't connect to Claude or Telegram.

**Check 1:** Verify your keys are in Keychain:
```bash
security find-generic-password -s raoc -a anthropic_api_key -w
```
This should print your key.

**Check 2:** Make sure you used the right account names:
- `anthropic_api_key` (not `anthropic-key`)
- `telegram_bot_token`
- `telegram_user_id`

---

## Project Structure

```
raoc/
├── config.py              All paths and constants
├── coordinator.py         Routes jobs through the agent pipeline
├── main.py                Entry point — starts the Telegram bot
│
├── agents/
│   ├── intake.py          Understands your request
│   ├── discovery.py       Finds and reads files
│   ├── planning.py        Creates action plans
│   ├── execution.py       Executes actions safely
│   ├── verification.py    Verifies results
│   └── reporter.py        Sends reports to your phone
│
├── substrate/
│   ├── command_wrapper.py Safe shell command execution
│   ├── host_sampler.py    File and system information
│   ├── screenshot.py      Takes screenshots
│   ├── secret_broker.py   Reads from macOS Keychain
│   ├── llm_client.py      Talks to Claude API
│   └── zone_resolver.py   Enforces file access boundaries
│
├── gateway/
│   └── telegram_bot.py    Telegram interface
│
├── models/
│   ├── job.py             Job data structures
│   ├── action.py          Action data structures
│   └── scope.py           Permission and scope models
│
└── db/
    ├── schema.py          Database table definitions
    └── queries.py         Database operations
```

---

## Security Model

| Boundary | Protection |
|---|---|
| **Workspace** | All file operations stay inside `~/raoc_workspace/`. Paths outside raise an error. |
| **Commands** | Dangerous patterns (`rm -rf`, `sudo`, etc.) are blocked. |
| **Approval** | No action runs without `approval_granted == True`. Enforced in code. |
| **Secrets** | API keys stored in macOS Keychain, never in code or database. |
| **Backup** | Every file is backed up before rewriting. Auto-restored on failure. |
| **Identity** | Only your Telegram user ID can send commands. Old messages are ignored. |

---

## Database Schema

RAOC uses SQLite to track all jobs and actions.

### Jobs Table

| Column | Description |
|---|---|
| job_id | Unique ID for each job |
| raw_request | Your original message |
| task_type | `run_script`, `rewrite_file`, `query`, or `query_action` |
| target_path | File being worked on |
| status | Current state of the job |
| approval_granted | Whether you approved the plan |
| created_at | When the job started |

### Actions Table

Each step in a plan gets a row:

| Column | Description |
|---|---|
| action_id | Unique ID for the action |
| job_id | Which job this belongs to |
| step_index | Order of execution (0, 1, 2...) |
| action_type | `file_read`, `file_write`, `file_backup`, `cmd_execute`, `screenshot` |
| risk_level | `low`, `medium`, or `high` |
| intent | Plain English description |
| status | `pending`, `running`, `succeeded`, or `failed` |

### Job Status Flow

```
RECEIVED → UNDERSTANDING → DISCOVERING → PLANNING → AWAITING_APPROVAL
                                                            ↓
                            EXECUTING → VERIFYING → REPORTING → COMPLETED
                                        (or FAILED / CANCELLED / BLOCKED)
```

---

## Configuration

All settings are in `raoc/config.py`. Common ones:

| Setting | Default | Description |
|---|---|---|
| `WORKSPACE` | `~/raoc_workspace` | Where your files live |
| `MAX_FILE_SIZE_CHARS` | 50,000 | Largest file RAOC will rewrite |
| `MAX_COMMAND_TIMEOUT` | 30 seconds | Scripts killed if they run longer |
| `LLM_MODEL` | `claude-sonnet-4-5` | AI model for understanding requests |
| `NARRATOR_MODEL` | `claude-haiku-4-5` | AI model for status messages |

---

## Running Tests

### Run all tests
```bash
uv run pytest tests/ -v
```

### Run specific test file
```bash
uv run pytest tests/test_coordinator.py -v
```

### Run with coverage
```bash
uv run pytest tests/ --cov=raoc
```

Tests use temporary directories — your real workspace is never touched.

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Package Manager | uv |
| AI | Anthropic Claude |
| Database | SQLite with SQLAlchemy |
| Telegram | python-telegram-bot |
| Validation | Pydantic v2 |
| Screenshots | pyautogui + Pillow |
| PDF Support | pdfplumber, pymupdf, pdf2docx |
| Secrets | macOS Keychain via keyring |

---

## What's Not Included (Yet)

These features are planned for future phases:

- Web browsing
- GUI automation (clicking desktop apps)
- Gmail/Calendar integration
- Multi-file operations in one job
- Semantic search across files
- Background daemon mode
- Memory and preference learning

---

## Getting Help

1. Check the logs: `tail -f ~/raoc/data/raoc.log`
2. Run tests to verify setup: `uv run pytest tests/ -v`
3. Check that your API keys are correct in Keychain Access
4. Make sure your Telegram bot token and user ID are correct

---

## License

[Add your license information here]

