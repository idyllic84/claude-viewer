from models import SessionDocument, blocks
from .base import SourceAdapter, SourceFile, SourceError, read_records


class ClaudeAdapter(SourceAdapter):
    source = 'claude'
    label = 'Claude Code'

    def discover(self):
        # Include old agent-* and modern <session>/subagents transcripts separately.
        return [SourceFile(path, path.parent.name) for path in self.files(['**/*.jsonl'])
                if path.name != 'journal.jsonl']

    def parse(self, entry):
        records, warnings = read_records(entry.path)
        if not records:
            raise SourceError('No complete records yet')
        rows = [r for _, r in records]
        if not any(r.get('type') in ('user', 'assistant', 'summary', 'system', 'file-history-snapshot',
                                     'last-prompt', 'mode', 'permission-mode', 'custom-title') for r in rows):
            raise SourceError('Not a recognized Claude conversation transcript')
        sid = next((r.get('sessionId') for r in rows if r.get('sessionId')), entry.path.stem)
        # Old agent logs may carry the parent's sessionId; they must not replace it.
        if entry.path.stem.startswith('agent-'):
            parent_sid = str(sid) if sid != entry.path.stem else (
                entry.path.parent.parent.name if 'subagents' in entry.path.parts else entry.project)
            sid = f'{parent_sid}:{entry.path.stem}'
        cwd = next((r.get('cwd') for r in rows if r.get('cwd')), entry.project)
        doc = SessionDocument(self.source, str(sid), str(cwd), records, warnings=warnings)
        doc.metadata['metadataOnly'] = not any(r.get('type') in ('user', 'assistant', 'summary') for r in rows)
        if 'subagents' in entry.path.parts:
            doc.metadata.update(parentSessionId=f'claude:{entry.path.parent.parent.name}', relationship='subagent')
        elif entry.path.stem.startswith('agent-'):
            parent = next((r.get('sessionId') for r in rows if r.get('sessionId')), None)
            if parent:
                doc.metadata.update(parentSessionId=f'claude:{parent}', relationship='subagent')
        for index, row in records:
            kind = row.get('type', 'unknown')
            native = row.get('message')
            if kind in ('user', 'assistant') and isinstance(native, dict):
                doc.add(index, kind, blocks(native.get('content')), native_id=row.get('uuid'),
                        parent_id=row.get('parentUuid'), timestamp=row.get('timestamp'),
                        model=native.get('model'), usage=native.get('usage'),
                        nativeType=kind)
            else:
                content = row.get('summary', row.get('content', ''))
                if not content and kind == 'file-history-snapshot':
                    content = list((row.get('snapshot') or {}).get('trackedFileBackups', {}))
                doc.add(index, 'system', content, kind=kind, native_id=row.get('uuid'),
                        parent_id=row.get('parentUuid'), timestamp=row.get('timestamp'), nativeType=kind)
        custom_title = next((r['customTitle'] for r in reversed(rows) if r.get('customTitle')), None)
        slug = next((r['slug'] for r in reversed(rows) if r.get('slug')), None)
        doc.name = str(custom_title or slug or '')
        return doc.finish()
