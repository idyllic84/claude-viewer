"""
Database operations for Claude Viewer
Handles SQLite database management for sessions and messages
"""

import json
import sqlite3
import os
from pathlib import Path
from contextlib import contextmanager


class Database:
    """Database manager for Claude Viewer"""

    def __init__(self):
        db_dir = Path(__file__).parent
        self.db_path = db_dir / 'claude.db'

    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('PRAGMA foreign_keys = ON')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    message_count INTEGER NOT NULL,
                    last_update_time INTEGER NOT NULL
                )
            ''')

            cursor.execute('CREATE INDEX IF NOT EXISTS idx_last_update_time ON sessions(last_update_time DESC)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_project ON sessions(project)')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    session_id TEXT NOT NULL,
                    order_id INTEGER NOT NULL,
                    timestamp INTEGER,
                    type TEXT,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY (session_id, order_id),
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
            ''')

            cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_session_type ON messages(session_id, type)')

    def get_all_sessions(self, project_filter=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if project_filter:
                cursor.execute('''
                    SELECT id, project, message_count, last_update_time
                    FROM sessions
                    WHERE project = ?
                    ORDER BY last_update_time DESC
                ''', (project_filter,))
            else:
                cursor.execute('''
                    SELECT id, project, message_count, last_update_time
                    FROM sessions
                    ORDER BY last_update_time DESC
                ''')

            rows = cursor.fetchall()

            sessions = []
            for row in rows:
                sessions.append({
                    'id': row['id'],
                    'project': row['project'],
                    'messageCount': row['message_count'],
                    'lastModified': row['last_update_time']
                })

            return sessions

    def get_messages_by_session(self, session_id):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT session_id, order_id, timestamp, type, raw_json
                    FROM messages
                    WHERE session_id = ?
                    ORDER BY order_id ASC
                ''', (session_id,))

                return cursor.fetchall()
        except Exception as e:
            print(f"Error getting messages for session {session_id}: {e}")
            return []

    def upsert_session(self, session_id, project, message_count, last_update_time):
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Use INSERT ... ON CONFLICT to avoid triggering CASCADE DELETE
                cursor.execute('''
                    INSERT INTO sessions (id, project, message_count, last_update_time)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        project = excluded.project,
                        message_count = excluded.message_count,
                        last_update_time = excluded.last_update_time
                ''', (session_id, project, message_count, last_update_time))
            return True
        except Exception as e:
            print(f"Error upserting session: {e}")
            return False

    def insert_messages_batch(self, messages_data):
        session_id = messages_data[0][0] if messages_data else None

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.executemany('''
                    INSERT OR IGNORE INTO messages (session_id, order_id, timestamp, type, raw_json)
                    VALUES (?, ?, ?, ?, ?)
                ''', messages_data)
                inserted_count = cursor.rowcount

            # After commit, query the actual count
            if session_id:
                with self.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session_id,))
                    total_count = cursor.fetchone()[0]
                    print(f"Session {session_id[:12]}...: Inserted {inserted_count} messages, total now {total_count}")
            else:
                print(f"Inserted {inserted_count} messages (attempted {len(messages_data)})")

            return inserted_count
        except Exception as e:
            print(f"Error inserting messages batch: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def delete_session(self, session_id):
        """Delete a session from database (CASCADE deletes messages)"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
            return True
        except Exception as e:
            print(f"Error deleting session from DB: {e}")
            return False

    def search_sessions_by_content(self, search_term):
        """
        Search for sessions containing the search term in their messages' raw_json.
        Returns list of unique session IDs.

        Args:
            search_term: Non-empty string to search for

        Returns:
            List of session IDs that contain the search term
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Search in raw_json using LIKE (case-insensitive)
                search_pattern = f'%{search_term}%'
                cursor.execute('''
                    SELECT DISTINCT m.session_id
                    FROM messages m
                    WHERE m.raw_json LIKE ? COLLATE NOCASE
                    ORDER BY m.session_id
                ''', (search_pattern,))

                rows = cursor.fetchall()
                return [row[0] for row in rows]

        except Exception as e:
            print(f"Error searching sessions by content: {e}")
            import traceback
            traceback.print_exc()
            return []

    def get_actual_message_count(self, session_id):
        """Get the actual count of messages in the database for a session"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM messages WHERE session_id = ?', (session_id,))
                return cursor.fetchone()[0]
        except Exception as e:
            print(f"Error getting actual message count: {e}")
            return 0

    def get_max_order_id(self, session_id):
        """Get the maximum order_id for a session (returns None if no messages)"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT MAX(order_id) FROM messages WHERE session_id = ?', (session_id,))
                result = cursor.fetchone()[0]
                return result
        except Exception as e:
            print(f"Error getting max order_id: {e}")
            return None

    def get_inconsistent_sessions(self):
        """Find sessions where message_count doesn't match actual message count"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT s.id, s.project, s.message_count,
                           COUNT(m.session_id) as actual_count,
                           s.last_update_time
                    FROM sessions s
                    LEFT JOIN messages m ON s.id = m.session_id
                    GROUP BY s.id
                    HAVING s.message_count != actual_count
                    ORDER BY s.last_update_time DESC
                ''')
                rows = cursor.fetchall()
                return [{
                    'id': row[0],
                    'project': row[1],
                    'expected_count': row[2],
                    'actual_count': row[3],
                    'last_update_time': row[4]
                } for row in rows]
        except Exception as e:
            print(f"Error getting inconsistent sessions: {e}")
            return []

    def delete_messages(self, session_id):
        """Delete all messages for a session (without deleting the session itself)"""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
                return cursor.rowcount
        except Exception as e:
            print(f"Error deleting messages for session: {e}")
            return 0
