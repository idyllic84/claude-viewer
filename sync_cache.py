"""Private per-sync projection spool: parse/replay each changed file only once.

Pickle preserves shared strings in very large snapshots. Only our own generated
files are loaded, and an in-memory per-job HMAC is verified BEFORE unpickling.
No source files, user-supplied pickle, or cache from a previous job are trusted.
"""
import gzip
import hashlib
import hmac
import io
import os
from pathlib import Path
import pickle
import tempfile


class _AuthenticatedWriter:
    def __init__(self, stream, digest):
        self.stream = stream
        self.digest = digest

    def write(self, data):
        size = self.stream.write(data)
        self.digest.update(data[:size])
        return size

    def flush(self):
        self.stream.flush()


class ProjectionCache:
    def __enter__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix='viewer-sync-')
        self.directory = Path(self._temporary.name)
        self._key = os.urandom(32)
        self._entries = {}
        self._next_token = 0
        return self

    def save(self, document):
        token = self._next_token
        self._next_token += 1
        path = self.directory / f'{token}.cache'
        digest = hmac.new(self._key, digestmod=hashlib.sha256)
        with path.open('xb') as raw:
            with gzip.GzipFile(fileobj=_AuthenticatedWriter(raw, digest), mode='wb', compresslevel=1) as compressed:
                pickle.dump(document, compressed, protocol=pickle.HIGHEST_PROTOCOL)
        self._entries[token] = (path, digest.digest())
        return token

    def load(self, token):
        path, expected = self._entries[token]
        # Unpickle the exact immutable bytes authenticated, not a mutable file
        # reopened after verification (which would introduce a TOCTOU window).
        payload = path.read_bytes()
        digest = hmac.digest(self._key, payload, 'sha256')
        if not hmac.compare_digest(digest, expected):
            raise ValueError('Projection cache authentication failed')
        with gzip.GzipFile(fileobj=io.BytesIO(payload), mode='rb') as compressed:
            return pickle.load(compressed)

    def __exit__(self, *exc):
        self._entries.clear()
        self._key = None
        self._temporary.cleanup()
