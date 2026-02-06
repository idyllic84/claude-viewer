"""
File system operations for Claude Viewer
Handles reading/writing Claude session files and file-history
"""

import json
import shutil
import os
from pathlib import Path
from datetime import datetime


class ClaudeFiles:
    """File system manager for Claude Code session files"""

    def __init__(self):
        self.projects_base_dir = Path(os.path.expanduser('~/.claude/projects'))
        self.file_history_dir = Path(os.path.expanduser('~/.claude/file-history'))

    def read_messages(self, project: str, session_id: str, skip: int = 0):
        file_path = self._get_session_file_path(project, session_id)
        messages = []

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for order_id, line in enumerate(f):
                    if order_id < skip:
                        continue

                    try:
                        data = json.loads(line)
                        messages.append((
                            session_id,
                            order_id,
                            data.get('timestamp'),
                            data.get('type'),
                            line.strip()
                        ))
                    except json.JSONDecodeError:
                        pass

            return messages
        except Exception as e:
            print(f"Error reading messages from {project}/{session_id}: {e}")
            return []

    def scan_sessions(self):
        if not self.projects_base_dir.exists():
            return []

        sessions = []

        for project_dir in self.projects_base_dir.iterdir():
            if not project_dir.is_dir():
                continue

            project_name = project_dir.name

            for file_path in project_dir.glob('*.jsonl'):
                if file_path.name.startswith('agent-'):
                    continue

                if not file_path.name.endswith('.jsonl'):
                    continue

                session_id = file_path.name[:-6]
                file_stat = file_path.stat()
                file_mtime = int(file_stat.st_mtime)

                sessions.append({
                    'session_id': session_id,
                    'project': project_name,
                    'file_path': file_path,
                    'mtime': file_mtime
                })

        return sessions

    def delete_session_files(self, project_name: str, session_id: str, messages: list):
        deleted = []
        agent_ids = self._extract_agent_ids(messages)

        if agent_ids:
            print(f"Found {len(agent_ids)} agent(s) for session {session_id}: {agent_ids}")

        project_dir = self.projects_base_dir / project_name

        for agent_id in agent_ids:
            agent_file = project_dir / f"agent-{agent_id}.jsonl"
            if agent_file.exists():
                agent_file.unlink()
                deleted.append(f"{project_name}/agent-{agent_id}.jsonl")

        file_history_folder = self.file_history_dir / session_id
        file_history_folder = file_history_folder.resolve()
        if not str(file_history_folder).startswith(str(self.file_history_dir.resolve())):
            raise ValueError("Security: file-history path outside allowed directory")
        if file_history_folder.exists() and file_history_folder.is_dir():
            shutil.rmtree(file_history_folder)
            deleted.append(f"file-history/{session_id}/")

        session_file = project_dir / f"{session_id}.jsonl"
        session_file = session_file.resolve()
        if not str(session_file).startswith(str(self.projects_base_dir.resolve())):
            raise ValueError("Security: session file path outside allowed directory")
        if not session_file.exists():
            raise FileNotFoundError(f"Session file not found: {session_id}.jsonl")

        session_file.unlink()
        deleted.append(f"{project_name}/{session_id}.jsonl")

        return deleted

    def _extract_agent_ids(self, messages: list) -> set:
        agent_ids = set()

        for message in messages:
            try:
                # Parse raw_json from sqlite3.Row object
                msg_data = json.loads(message['raw_json'])
                if 'toolUseResult' in msg_data and isinstance(msg_data['toolUseResult'], dict):
                    agent_id = msg_data['toolUseResult'].get('agentId')
                    if agent_id:
                        agent_ids.add(agent_id)
            except Exception:
                continue

        return agent_ids

    def _get_session_file_path(self, project: str, session_id: str) -> Path:
        return self.projects_base_dir / project / f"{session_id}.jsonl"
        
