# ClaudeViewer

A local web tool for browsing and managing your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) session history.

## Features

- Browse all Claude Code sessions grouped by project
- Three-panel UI: session list, conversation history, message details
- Filter messages by type (user, assistant, tool-use, tool-result, etc.)
- Full-text search across sessions and messages
- Incremental sync from local Claude Code files to SQLite
- Delete sessions along with associated agent files

## Quick Start

**Prerequisites:** Python 3.7+, Claude Code installed with existing sessions

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
python server.py
```

Open `http://localhost:8000` in your browser.

Click **Reload** to sync sessions from `~/.claude/projects/`, then browse your conversations.
