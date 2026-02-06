"""
Business logic layer for Claude Viewer
Handles session management, sync operations, and deletion
"""

from pathlib import Path
from datetime import datetime
from threading import RLock
from functools import wraps
from db import Database
from claudeFile import ClaudeFiles


def with_cache_lock(func):
    """Decorator to ensure thread-safe access to _session_cache"""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        with self._cache_lock:
            return func(self, *args, **kwargs)
    return wrapper


class ClaudeViewerService:
    """Service layer for all Claude Viewer operations"""

    def __init__(self):
        self._db = Database()
        self._claude_files = ClaudeFiles()
        self._session_cache = {}
        self._cache_lock = RLock()

    def initialize(self):
        print(f"Initializing database...")
        self._db.init_schema()
        print(f"Database initialized")

        print(f"Syncing sessions from filesystem...")
        try:
            self.sync_sessions_from_claude_files()
        except Exception as e:
            print(f"Warning: Sync failed: {e}")
            print(f"Service started with empty cache")

    def shutdown(self):
        print(f"Shutting down service...")
        self._session_cache.clear()
        print(f"Service shutdown complete")

    def get_all_sessions(self, project_filter=None):
        sessions = list(self._session_cache.values())

        if project_filter:
            sessions = [s for s in sessions if s.get('project') == project_filter]

        sessions.sort(key=lambda s: s.get('lastModified', 0), reverse=True)
        return sessions

    @with_cache_lock
    def sync_sessions_from_claude_files(self):
        try:
            db_sessions = {s['id']: s for s in self._db.get_all_sessions()}
            file_sessions = self._claude_files.scan_sessions()

            synced_count = 0

            for file_session in file_sessions:
                session_id = file_session['session_id']
                project_name = file_session['project']
                file_mtime = file_session['mtime']

                try:
                    db_session = db_sessions.get(session_id)

                    if db_session is None:
                        messages = self._claude_files.read_messages(project_name, session_id)
                        message_count = len(messages)

                        self._db.upsert_session(session_id, project_name, message_count, file_mtime)

                        if messages:
                            self._db.insert_messages_batch(messages)

                        db_sessions[session_id] = {
                            'id': session_id,
                            'project': project_name,
                            'messageCount': message_count,
                            'lastModified': file_mtime
                        }

                        synced_count += 1

                    elif file_mtime > db_session['lastModified']:
                        # Check for gaps in message sequence
                        actual_count = self._db.get_actual_message_count(session_id)
                        max_order_id = self._db.get_max_order_id(session_id)

                        # Detect gap: max_order_id + 1 should equal actual_count
                        has_gap = (max_order_id is not None and max_order_id + 1 != actual_count)

                        if has_gap:
                            # Gap detected, delete and re-sync from scratch
                            print(f"Gap detected in {session_id}: max_order={max_order_id}, count={actual_count}")
                            deleted = self._db.delete_messages(session_id)
                            print(f"  Deleted {deleted} messages, re-syncing from scratch")
                            skip_count = 0
                        elif max_order_id is None:
                            # No messages yet, full sync
                            skip_count = 0
                        else:
                            # No gap, incremental sync from end
                            skip_count = actual_count

                        # Read messages from file
                        messages = self._claude_files.read_messages(project_name, session_id, skip=skip_count)

                        # Insert new messages
                        if messages:
                            try:
                                self._db.insert_messages_batch(messages)
                            except Exception as insert_error:
                                print(f"Error inserting messages for {session_id}: {insert_error}")
                                continue

                        # Always calculate message_count from actual database content
                        new_message_count = self._db.get_actual_message_count(session_id)

                        # Update session metadata
                        self._db.upsert_session(session_id, project_name, new_message_count, file_mtime)

                        db_sessions[session_id].update({
                            'messageCount': new_message_count,
                            'lastModified': file_mtime
                        })

                        synced_count += 1

                except Exception as e:
                    print(f"Error processing {project_name}/{session_id}: {e}")
                    continue

            print(f"Synced {synced_count} sessions")

            self._session_cache = db_sessions
            return self.get_all_sessions()

        except Exception as e:
            print(f"Error syncing sessions: {e}")
            raise

    def get_session_with_messages(self, session_id):
        session_info = self._session_cache.get(session_id)
        if not session_info:
            return {
                'project': None,
                'session_id': session_id,
                'messages': [],
                'error': 'Session not found in database',
                'error_type': 'not_found'
            }

        messages = self._db.get_messages_by_session(session_id)

        return {
            'project': session_info['project'],
            'session_id': session_id,
            'messages': messages,
            'error': None,
            'error_type': None
        }

    @with_cache_lock
    def delete_session(self, session_id):
        session_info = self._session_cache.get(session_id)
        if not session_info:
            return {
                'success': False,
                'deleted': [],
                'errors': ['Session not found']
            }

        project_name = session_info['project']
        messages = self._db.get_messages_by_session(session_id)

        try:
            deleted = self._claude_files.delete_session_files(project_name, session_id, messages)
        except Exception as e:
            return {
                'success': False,
                'deleted': [],
                'errors': [str(e)]
            }

        if self._db.delete_session(session_id):
            deleted.append(f"DB record: {session_id} (messages cascaded)")
            del self._session_cache[session_id]
            return {
                'success': True,
                'deleted': deleted
            }
        else:
            return {
                'success': False,
                'deleted': deleted,
                'errors': ['Failed to delete DB record (critical)']
            }

    def search_sessions_by_content(self, search_term):
        """
        Search for sessions containing the search term in their messages.
        Returns filtered session list sorted by last_update_time DESC.

        Args:
            search_term: Non-empty string to search for

        Returns:
            List of session objects matching the search
        """
        try:
            # Get matching session IDs from database
            matching_ids = self._db.search_sessions_by_content(search_term)

            # Filter cached sessions by matching IDs
            sessions = [
                self._session_cache[sid]
                for sid in matching_ids
                if sid in self._session_cache
            ]

            # Sort by last_update_time DESC (same as get_all_sessions)
            sessions.sort(key=lambda s: s.get('lastModified', 0), reverse=True)

            return sessions

        except Exception as e:
            print(f"Error in search_sessions_by_content: {e}")
            import traceback
            traceback.print_exc()
            return []

