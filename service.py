"""Read-only multi-source discovery and atomic, idempotent archive sync."""
from collections import defaultdict
from pathlib import Path
from threading import Lock, Thread
from copy import deepcopy
import time
from db import Database
from sources import default_adapters
from sources.base import SourceError


class SyncBusy(RuntimeError):
    pass


class ClaudeViewerService:
    # Keep the old class name for callers; the service is now source-independent.
    def __init__(self, db_path=None, adapters=None):
        self._db = Database(db_path)
        self.adapters = default_adapters() if adapters is None else adapters
        self._sync_lock = Lock()
        self._status_lock = Lock()
        self._started_monotonic = None
        self._status = {'running': False, 'result': None, 'progress': None}

    def initialize(self, sync=False):
        self._db.init_schema()
        if sync:
            return self.sync_sessions()

    def shutdown(self):
        # Connections are scoped per operation; never touch agent processes/files.
        pass

    def get_sources(self):
        return [adapter.info() for adapter in self.adapters]

    def get_all_sessions(self, project_filter=None, source=None):
        return self._db.list_sessions(project=project_filter, source=source)[0]

    def list_sessions(self, **filters):
        return self._db.list_sessions(**filters)

    def search_sessions_by_content(self, search_term, source=None):
        return self._db.list_sessions(query=search_term, source=source)[0]

    def get_messages(self, session_id, **options):
        return self._db.get_messages(session_id, **options)

    def get_raw_message(self, message_id):
        return self._db.get_raw_message(message_id)

    def get_records(self, session_id, **options):
        return self._db.get_records(session_id, **options)

    def sync_status(self):
        with self._status_lock:
            status = deepcopy(self._status)
            if status['running'] and self._started_monotonic is not None:
                status['elapsedSeconds'] = round(time.monotonic() - self._started_monotonic, 1)
            return status

    def _prepare_sync(self):
        with self._status_lock:
            self._started_monotonic = time.monotonic()
            self._status = {'running': True, 'result': None, 'startedAt': int(time.time() * 1000),
                            'progress': {'phase': 'discovering', 'filesDone': 0, 'filesTotal': 0,
                                         'sessionsDone': 0, 'sessionsTotal': 0, 'source': '', 'currentFile': ''}}

    def _progress(self, **values):
        with self._status_lock:
            self._status['progress'].update(values)

    def start_sync(self):
        """Reserve the writer before spawning. Other callers attach to the same job."""
        if not self._sync_lock.acquire(blocking=False):
            return self.sync_status()
        self._prepare_sync()
        worker = Thread(target=self._background_sync, name='history-sync', daemon=True)
        try:
            worker.start()
        except Exception:
            self._sync_lock.release()
            with self._status_lock:
                self._status['running'] = False
            raise
        return self.sync_status()

    def _background_sync(self):
        try:
            self._sync_sessions_locked()
        except Exception as exc:
            print(f'Sync failed: {exc}', flush=True)

    def sync_sessions(self):
        if not self._sync_lock.acquire(blocking=False):
            raise SyncBusy('A sync is already running')
        self._prepare_sync()
        return self._sync_sessions_locked()

    def _sync_sessions_locked(self):
        result = {'added': 0, 'updated': 0, 'unchanged': 0, 'failed': 0, 'errors': [], 'warnings': []}
        try:
            states = self._db.file_states()
            groups = defaultdict(list)
            discovered = set()
            failed_paths = set()
            def failure(source, path, exc):
                failed_paths.add(str(path))
                result['failed'] += 1
                result['errors'].append({'source': source, 'path': str(path), 'error': str(exc)})

            sources = []
            for adapter in self.adapters:
                self._progress(source=adapter.label)
                try:
                    sources.append((adapter, adapter.discover()))
                except Exception as exc:
                    failure(adapter.source, '<discovery>', exc)
            files_total = sum(len(entries) for _, entries in sources)
            files_done = 0
            self._progress(phase='scanning', filesTotal=files_total)
            print(f'Sync: scanning {files_total} source files', flush=True)
            for adapter, entries in sources:
                for entry in entries:
                    self._progress(source=adapter.label, currentFile=entry.path.name)
                    path = str(entry.path)
                    discovered.add(path)
                    try:
                        fingerprint = entry.fingerprint()
                        modified = entry.path.stat().st_mtime_ns // 1_000_000
                        state = states.get(path)
                        cached = state and state['source'] == adapter.source and (
                            state['fingerprint'] == fingerprint and state['parser_version'] == adapter.parser_version
                            and state['modified'] == modified)
                        document = None if cached else adapter.parse(entry)
                        if document is not None:
                            fingerprint = entry.snapshot_fingerprint(document.records)
                            modified = document.records.modified_ns // 1_000_000
                        sid = state['session_id'] if cached else document.id
                        count = state['message_count'] if cached else len(document.messages)
                        # Keep only candidate metadata, not every parsed transcript. A full archive
                        # can be gigabytes; at most one document should remain in memory at a time.
                        groups[sid].append({'adapter': adapter, 'entry': entry,
                                            'fingerprint': fingerprint, 'modified': modified, 'count': count})
                        document = None
                    except Exception as exc:
                        failure(adapter.source, path, exc)
                    files_done += 1
                    self._progress(filesDone=files_done, counts={k: result[k] for k in ('added', 'updated', 'unchanged', 'failed')})
                    print(f'Sync scan {files_done}/{files_total}: {adapter.label} / {entry.path.name}', flush=True)

            sessions_done = 0
            self._progress(phase='importing', sessionsTotal=len(groups))
            print(f'Sync: importing {len(groups)} logical sessions', flush=True)
            for sid, candidates in groups.items():
                # Empty migration copies must not replace nonempty history. Then prefer newest copy.
                winner = max(candidates, key=lambda c: (c['count'] > 0, c['modified'], c['count'], str(c['entry'].path)))
                adapter, entry = winner['adapter'], winner['entry']
                self._progress(source=adapter.label, currentFile=entry.path.name)
                files = [(str(c['entry'].path), c['adapter'].source, sid, c['fingerprint'],
                          c['adapter'].parser_version, c['modified'], c['count']) for c in candidates]
                try:
                    existing = self._db.get_session(sid)
                    with self._db.get_connection() as conn:
                        primary = conn.execute('SELECT path, fingerprint, parser_version, last_modified FROM sessions WHERE id=?', (sid,)).fetchone()
                    if primary and primary['path'] in failed_paths:
                        # A corrupt newer copy must not demote its last good projection to an
                        # older duplicate merely because that older duplicate still parses.
                        continue
                    if existing and existing['messageCount'] > 0 and winner['count'] == 0:
                        # An empty migration copy/header-only write cannot erase an archive.
                        self._db.update_files(files)
                        result['unchanged'] += 1
                        result['warnings'].append({'source': adapter.source, 'sessionId': sid,
                                                   'warning': 'Empty projection ignored; previous history retained'})
                        continue
                    unchanged = primary and primary['path'] == str(entry.path) and (
                        primary['fingerprint'] == winner['fingerprint'] and primary['parser_version'] == adapter.parser_version
                        and primary['last_modified'] == winner['modified'])
                    if unchanged:
                        self._db.update_files(files)
                        result['unchanged'] += 1
                    else:
                        document = adapter.parse(entry)
                        if document.id != sid:
                            raise SourceError('Source session identity changed; retry sync')
                        # Import the fresh read, not the earlier discovery hash. Appends during
                        # a long import are valid; edits to already-read bytes are not.
                        fingerprint = entry.snapshot_fingerprint(document.records)
                        modified = document.records.modified_ns // 1_000_000
                        files = [(path, source, session, fingerprint if path == str(entry.path) else fp,
                                  version, modified if path == str(entry.path) else mtime,
                                  len(document.messages) if path == str(entry.path) else count)
                                 for path, source, session, fp, version, mtime, count in files]
                        if existing and existing['messageCount'] > 0 and not document.messages:
                            self._db.update_files(files)
                            result['unchanged'] += 1
                            result['warnings'].append({'source': adapter.source, 'sessionId': sid,
                                                       'warning': 'Empty projection ignored; previous history retained'})
                            continue
                        self._db.replace_session(document, entry.path, fingerprint, modified,
                                                 adapter.parser_version, files)
                        result['updated' if existing else 'added'] += 1
                        for warning in document.warnings:
                            result['warnings'].append({'source': adapter.source, 'sessionId': sid, 'warning': warning})
                        document = None
                except Exception as exc:
                    failure(adapter.source, entry.path, exc)
                finally:
                    sessions_done += 1
                    self._progress(sessionsDone=sessions_done,
                                   counts={k: result[k] for k in ('added', 'updated', 'unchanged', 'failed')})
                    print(f'Sync import {sessions_done}/{len(groups)}: {adapter.label} / {entry.path.name}', flush=True)
            # Disappearing source files do NOT delete archived conversations.
            missing = [path for path in states if path not in discovered and not Path(path).exists()]
            self._progress(phase='finishing', currentFile='')
            self._db.mark_missing(missing)
            with self._status_lock:
                self._status.update(running=False, result=result,
                                    elapsedSeconds=round(time.monotonic() - self._started_monotonic, 1))
                self._status['progress']['phase'] = 'complete'
            print(f"Sync complete: {result['added']} added, {result['updated']} updated, "
                  f"{result['unchanged']} unchanged, {result['failed']} failed", flush=True)
            return result
        except Exception as exc:
            failure('archive', str(self._db.db_path), exc)
            with self._status_lock:
                self._status.update(running=False, result=result,
                                    elapsedSeconds=round(time.monotonic() - self._started_monotonic, 1))
                self._status['progress']['phase'] = 'failed'
            raise
        finally:
            with self._status_lock:
                self._status['running'] = False
            self._sync_lock.release()

    def sync_sessions_from_claude_files(self):
        """Compatibility name. Syncs all configured sources and returns real statistics."""
        return self.sync_sessions()
