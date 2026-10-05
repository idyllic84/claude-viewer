"""Viewer-owned archive v2: compressed JSON, shared text, position-free trigram FTS."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from codec import pack_json, unpack_json
from models import searchable_text

SCHEMA_VERSION = 2
ARCHIVE_APPLICATION_ID = 0x43565732


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def trigram_query(query):
    """detail=none accepts only three-character tokens, not long phrase queries.

    AND a bounded set of grams to select candidates; literal LIKE verifies exact
    order/adjacency, preserving punctuation, CJK and true substring semantics.
    """
    grams = list(dict.fromkeys(query[i:i + 3] for i in range(len(query) - 2)))
    if len(grams) > 32:
        grams = [grams[i * (len(grams) - 1) // 31] for i in range(32)]
    return ' AND '.join('"' + gram.replace('"', '""') + '"' for gram in grams)


class Database:
    def __init__(self, db_path=None):
        self.db_path = Path(db_path or os.environ.get('VIEWER_DB') or Path(__file__).parent / 'viewer.db')
        if self.db_path.resolve() == (Path(__file__).parent / 'claude.db').resolve():
            raise ValueError('The legacy claude.db is preserved; choose a separate VIEWER_DB')
        self.has_trigram = False

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
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
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.get_connection() as conn:
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            application_id = conn.execute('PRAGMA application_id').fetchone()[0]
            if (tables and application_id != ARCHIVE_APPLICATION_ID) or (
                    not tables and application_id not in (0, ARCHIVE_APPLICATION_ID)):
                raise ValueError('Not a viewer-owned archive; existing databases will not be modified')
            if version == 1:
                raise ValueError('Archive schema v1 requires rebuilding. Stop the viewer and run python rebuild.py')
            if version not in (0, SCHEMA_VERSION):
                raise ValueError(f'Unsupported archive schema version: {version}')
            conn.execute(f'PRAGMA application_id = {ARCHIVE_APPLICATION_ID}')
            conn.execute('PRAGMA journal_mode = WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
                    project TEXT NOT NULL, name TEXT NOT NULL, message_count INTEGER NOT NULL,
                    last_modified INTEGER NOT NULL, path TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    parser_version INTEGER NOT NULL, metadata TEXT NOT NULL, warnings TEXT NOT NULL,
                    available INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS sessions_modified ON sessions(last_modified DESC, id);
                CREATE INDEX IF NOT EXISTS sessions_source_project ON sessions(source, project);
                CREATE TABLE IF NOT EXISTS raw_records (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    record_index INTEGER NOT NULL, raw_json BLOB NOT NULL,
                    PRIMARY KEY (session_id, record_index)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_pk INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    order_id INTEGER NOT NULL, native_id TEXT, parent_id TEXT,
                    raw_record INTEGER NOT NULL, normalized_json BLOB NOT NULL,
                    UNIQUE (session_id, order_id)
                );
                CREATE TABLE IF NOT EXISTS search_content (
                    content_id INTEGER PRIMARY KEY, digest BLOB NOT NULL UNIQUE, search_text TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS message_texts (
                    message_pk INTEGER PRIMARY KEY REFERENCES messages(message_pk) ON DELETE CASCADE,
                    content_id INTEGER NOT NULL REFERENCES search_content(content_id)
                );
                CREATE INDEX IF NOT EXISTS message_texts_content ON message_texts(content_id);
                CREATE TABLE IF NOT EXISTS source_files (
                    path TEXT PRIMARY KEY, source TEXT NOT NULL, session_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, parser_version INTEGER NOT NULL,
                    modified INTEGER NOT NULL, message_count INTEGER NOT NULL,
                    available INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS source_files_session ON source_files(session_id);
            ''')
            try:
                conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS message_search USING fts5(
                    search_text, content='search_content', content_rowid='content_id',
                    tokenize='trigram', detail=none, columnsize=0)""")
                new_index = not conn.execute("SELECT 1 FROM sqlite_master WHERE name='content_search_insert'").fetchone()
                conn.executescript('''
                    CREATE TRIGGER IF NOT EXISTS content_search_insert AFTER INSERT ON search_content BEGIN
                        INSERT INTO message_search(rowid, search_text) VALUES (new.content_id, new.search_text);
                    END;
                    CREATE TRIGGER IF NOT EXISTS content_search_delete AFTER DELETE ON search_content BEGIN
                        INSERT INTO message_search(message_search, rowid, search_text)
                        VALUES ('delete', old.content_id, old.search_text);
                    END;
                ''')
                if new_index:
                    conn.execute("INSERT INTO message_search(message_search) VALUES ('rebuild')")
                self.has_trigram = True
            except sqlite3.OperationalError:
                self.has_trigram = False
            conn.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')

    def file_states(self):
        with self.get_connection() as conn:
            return {r['path']: dict(r) for r in conn.execute('SELECT * FROM source_files')}

    def replace_session(self, document, path, fingerprint, modified, parser_version, files):
        """Raw blobs, projection, shared text/FTS and watermarks commit atomically."""
        with self.get_connection() as conn:
            conn.execute('''INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET project=excluded.project, name=excluded.name,
                message_count=excluded.message_count, last_modified=excluded.last_modified,
                path=excluded.path, fingerprint=excluded.fingerprint, parser_version=excluded.parser_version,
                metadata=excluded.metadata, warnings=excluded.warnings, available=1''',
                (document.id, document.source, document.external_id, document.project, document.name,
                 len(document.messages), modified, str(path), fingerprint, parser_version,
                 encode(document.metadata), encode(document.warnings)))
            old_content = [r[0] for r in conn.execute('''SELECT DISTINCT t.content_id FROM message_texts t
                JOIN messages m ON m.message_pk=t.message_pk WHERE m.session_id=?''', (document.id,))]
            conn.execute('DELETE FROM messages WHERE session_id=?', (document.id,))
            # Do not delete shared content still used by another session/message.
            conn.executemany('''DELETE FROM search_content WHERE content_id=? AND NOT EXISTS
                (SELECT 1 FROM message_texts WHERE message_texts.content_id=search_content.content_id)''',
                ((content_id,) for content_id in old_content))
            conn.execute('DELETE FROM raw_records WHERE session_id=?', (document.id,))
            sizes = getattr(document.records, 'record_sizes', {})
            conn.executemany('INSERT INTO raw_records VALUES (?, ?, ?)',
                ((document.id, i, pack_json(r, streaming=sizes.get(i, 0) > 64 * 1024))
                 for i, r in document.records))
            first_pk = conn.execute('SELECT COALESCE(MAX(message_pk), 0)+1 FROM messages').fetchone()[0]
            conn.executemany('INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                ((first_pk + order, m['id'], document.id, order, m['uuid'], m.get('parentUuid'),
                  m['rawRecord'], pack_json(m)) for order, m in enumerate(document.messages)))
            content_cache = {}
            for order, message in enumerate(document.messages):
                search = searchable_text(message['message']['content'])
                if not search.strip():
                    continue
                digest = hashlib.sha256(search.encode('utf-8')).digest()
                content_id = content_cache.get(digest)
                if content_id is None:
                    row = conn.execute('SELECT content_id FROM search_content WHERE digest=?', (digest,)).fetchone()
                    if row:
                        content_id = row[0]
                    else:
                        cursor = conn.execute('INSERT INTO search_content(digest,search_text) VALUES (?,?)', (digest, search))
                        content_id = cursor.lastrowid
                    content_cache[digest] = content_id
                conn.execute('INSERT INTO message_texts VALUES (?,?)', (first_pk + order, content_id))
            self._store_files(conn, files)

    @staticmethod
    def _store_files(conn, files):
        conn.executemany('''INSERT INTO source_files VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(path) DO UPDATE SET session_id=excluded.session_id, fingerprint=excluded.fingerprint,
            parser_version=excluded.parser_version, modified=excluded.modified,
            message_count=excluded.message_count, available=1''', files)

    def update_files(self, files):
        with self.get_connection() as conn:
            self._store_files(conn, files)
            for f in files:
                conn.execute('UPDATE sessions SET available=1 WHERE id=?', (f[2],))

    def mark_missing(self, paths):
        with self.get_connection() as conn:
            conn.executemany('UPDATE source_files SET available=0 WHERE path=?', ((p,) for p in paths))
            conn.execute('''UPDATE sessions SET available=CASE WHEN EXISTS
                (SELECT 1 FROM source_files f WHERE f.session_id=sessions.id
                 AND f.path=sessions.path AND f.available=1) THEN 1 ELSE 0 END''')

    @staticmethod
    def _session(row):
        return {'id': row['id'], 'source': row['source'], 'externalId': row['external_id'],
                'project': row['project'], 'name': row['name'], 'messageCount': row['message_count'],
                'lastModified': row['last_modified'], 'timestampUnit': 'ms', 'available': bool(row['available']),
                'metadata': json.loads(row['metadata']), 'warnings': json.loads(row['warnings']),
                'capabilities': {'readOnly': True, 'sourceDelete': False}}

    def get_session(self, session_id):
        with self.get_connection() as conn:
            row = conn.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
            return self._session(row) if row else None

    def list_sessions(self, project=None, source=None, query='', offset=0, limit=None):
        clauses, params = [], []
        if project:
            clauses.append('s.project=?'); params.append(project)
        if source:
            clauses.append('s.source=?'); params.append(source)
        if query:
            pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            from_text = 'search_content c JOIN message_texts t ON t.content_id=c.content_id JOIN messages m ON m.message_pk=t.message_pk'
            if self.has_trigram and len(query) >= 3 and '\x00' not in query:
                from_text = 'message_search f JOIN search_content c ON c.content_id=f.rowid JOIN message_texts t ON t.content_id=c.content_id JOIN messages m ON m.message_pk=t.message_pk'
                condition = "message_search MATCH ? AND c.search_text LIKE ? ESCAPE '\\'"
                message_params = [trigram_query(query), pattern]
            else:
                condition = "c.search_text LIKE ? ESCAPE '\\'"
                message_params = [pattern]
            message_clause = f's.id IN (SELECT m.session_id FROM {from_text} WHERE {condition})'
            clauses.append(f"({message_clause} OR s.name LIKE ? ESCAPE '\\' OR s.project LIKE ? ESCAPE '\\' OR s.external_id LIKE ? ESCAPE '\\')")
            params.extend(message_params + [pattern, pattern, pattern])
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.get_connection() as conn:
            total = conn.execute('SELECT COUNT(*) FROM sessions s' + where, params).fetchone()[0]
            sql = 'SELECT s.* FROM sessions s' + where + ' ORDER BY s.last_modified DESC, s.id'
            if limit is not None:
                sql += ' LIMIT ? OFFSET ?'; params.extend([limit, offset])
            rows = conn.execute(sql, params).fetchall()
            return [self._session(r) for r in rows], total

    def get_messages(self, session_id, offset=0, limit=200, branch=None):
        session = self.get_session(session_id)
        if session is None:
            return None
        with self.get_connection() as conn:
            branch_orders = None
            if branch:
                leaf = session['metadata'].get('defaultLeaf') if branch == 'active' else branch
                if not leaf:
                    raise ValueError('This session has no branch metadata')
                nodes = {r['native_id']: r for r in conn.execute(
                    'SELECT native_id, parent_id, order_id FROM messages WHERE session_id=?', (session_id,))}
                if leaf not in nodes:
                    raise ValueError('Unknown branch leaf')
                seen, branch_orders = set(), []
                while leaf and leaf not in seen and leaf in nodes:
                    seen.add(leaf); node = nodes[leaf]
                    branch_orders.append(node['order_id']); leaf = node['parent_id']
            if branch_orders is None:
                rows = conn.execute('''SELECT normalized_json, order_id FROM messages WHERE session_id=?
                    ORDER BY order_id LIMIT ? OFFSET ?''', (session_id, limit, offset)).fetchall()
                total = session['messageCount']
            else:
                branch_orders.sort(); total = len(branch_orders)
                selected = branch_orders[offset:offset + limit]
                rows = [conn.execute('SELECT normalized_json, order_id FROM messages WHERE session_id=? AND order_id=?',
                                     (session_id, order)).fetchone() for order in selected]
            items = []
            for row in rows:
                message = unpack_json(row['normalized_json']); message['orderId'] = row['order_id']
                items.append(message)
            return {'items': items, 'total': total, 'session': session,
                    'nextCursor': offset + len(items) if offset + len(items) < total else None}

    def get_raw_message(self, message_id):
        with self.get_connection() as conn:
            row = conn.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
            if row is None:
                return None
            normalized = unpack_json(row['normalized_json'])
            indices = [row['raw_record'], *normalized.get('relatedRawRecords', [])]
            records = []
            for index in dict.fromkeys(indices):
                raw = conn.execute('SELECT raw_json FROM raw_records WHERE session_id=? AND record_index=?',
                                   (row['session_id'], index)).fetchone()
                if raw:
                    records.append({'recordIndex': index, 'record': unpack_json(raw[0])})
            path = conn.execute('SELECT path FROM sessions WHERE id=?', (row['session_id'],)).fetchone()[0]
            return {'sourcePath': path, 'sourceLocator': normalized.get('sourceLocator'), 'records': records}

    def get_records(self, session_id, offset=0, limit=50):
        if self.get_session(session_id) is None:
            return None
        with self.get_connection() as conn:
            total = conn.execute('SELECT COUNT(*) FROM raw_records WHERE session_id=?', (session_id,)).fetchone()[0]
            rows = conn.execute('''SELECT record_index, raw_json FROM raw_records WHERE session_id=?
                ORDER BY record_index LIMIT ? OFFSET ?''', (session_id, limit, offset)).fetchall()
            return {'items': [{'recordIndex': r[0], 'record': unpack_json(r[1])} for r in rows], 'total': total,
                    'nextCursor': offset + len(rows) if offset + len(rows) < total else None}
