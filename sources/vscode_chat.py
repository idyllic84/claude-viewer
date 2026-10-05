"""VS Code state snapshots and official objectMutationLog operations.

Push(i) truncates the entire suffix before appending; it is not replacement of
len(v) elements. The raw operation log is never mutated during reconstruction.
"""
from copy import deepcopy
from urllib.parse import unquote, urlparse
from models import SessionDocument, blocks, text
from .base import SourceAdapter, SourceFile, SourceError, SnapshotRecords, read_records, read_json


def folder_path(uri):
    if not isinstance(uri, str):
        return ''
    parsed = urlparse(uri)
    if parsed.scheme != 'file':
        return uri
    path = unquote(parsed.path)
    if parsed.netloc:
        return '//' + parsed.netloc + path
    if len(path) > 2 and path[0] == '/' and path[2] == ':':
        path = path[1:]
    return path


def replay_operations(records):
    state = None
    for index, operation in records:
        kind = operation.get('kind')
        if kind == 0:
            if not isinstance(operation.get('v'), dict):
                raise SourceError(f'Invalid snapshot at line {index + 1}')
            state = deepcopy(operation['v'])
            continue
        if state is None:
            raise SourceError('Mutation precedes Initial snapshot')
        keys = operation.get('k')
        if not isinstance(keys, list) or not keys:
            raise SourceError(f'Invalid mutation path at line {index + 1}')
        try:
            parent = state
            for key in keys[:-1]:
                parent = parent[key]
            key = keys[-1]
            if kind == 1:
                parent[key] = deepcopy(operation.get('v'))
            elif kind == 2:
                array = parent.get(key, []) if isinstance(parent, dict) else parent[key]
                if not isinstance(array, list):
                    raise TypeError('Push target is not an array')
                values = operation.get('v') or []
                if not isinstance(values, list):
                    raise TypeError('Push values must be an array')
                if 'i' in operation:
                    start = operation['i']
                    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
                        raise ValueError('Invalid array length')
                    if start > 1_000_000:
                        raise ValueError('Array length exceeds replay limit')
                    del array[start:]
                    if start > len(array):
                        array.extend([None] * (start - len(array)))
                array.extend(deepcopy(values))
                parent[key] = array
            elif kind == 3:
                if isinstance(parent, dict):
                    parent.pop(key, None)
                else:
                    parent[key] = None  # JS undefined; retain array position.
            else:
                raise ValueError(f'Unknown operation kind: {kind}')
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            raise SourceError(f'Invalid mutation at line {index + 1}: {exc}') from exc
    if state is None:
        raise SourceError('No complete snapshot yet')
    return state


class VSCodeChatAdapter(SourceAdapter):
    source = 'vscode_chat'
    label = 'VS Code Chat'

    def discover(self):
        entries = []
        patterns = ['workspaceStorage/*/chatSessions/*.json', 'workspaceStorage/*/chatSessions/*.jsonl',
                    'globalStorage/emptyWindowChatSessions/*.json', 'globalStorage/emptyWindowChatSessions/*.jsonl',
                    'profiles/*/globalStorage/emptyWindowChatSessions/*.json',
                    'profiles/*/globalStorage/emptyWindowChatSessions/*.jsonl']
        for path in self.files(patterns):
            dependencies = ()
            if path.parent.name == 'chatSessions':
                workspace = path.parent.parent / 'workspace.json'
                # workspace.json is a sidecar inside the same authorized root.
                if workspace.exists() and any(workspace.resolve().is_relative_to(r) for r in self.roots if r.is_dir()):
                    dependencies = (workspace.resolve(),)
            entries.append(SourceFile(path, dependencies=dependencies))
        return entries

    def parse(self, entry):
        if entry.path.suffix == '.jsonl':
            records, warnings = read_records(entry.path)
            state = replay_operations(records)
        else:
            state = read_json(entry.path)
            records, warnings = SnapshotRecords.from_json(entry.path, state), []
        project = ''
        if entry.dependencies:
            workspace = read_json(entry.dependencies[0], keep_bytes=True)
            records.dependencies[str(entry.dependencies[0])] = workspace.snapshot_bytes
            project = folder_path(workspace.get('folder') or workspace.get('workspace'))
        doc = SessionDocument(self.source, str(state.get('sessionId') or entry.path.stem), project,
                              records, name=str(state.get('customTitle') or ''), warnings=warnings)
        doc.metadata['formatVersion'] = state.get('version')
        requests = state.get('requests') or []
        if not isinstance(requests, list):
            raise SourceError('requests is not an array')
        # Keep raw provenance local to a request without copying raw snapshots into every message.
        last_operation = {}
        for index, row in records:
            keys = row.get('k', [])
            if len(keys) >= 2 and keys[0] == 'requests' and isinstance(keys[1], int):
                last_operation[keys[1]] = index
        for request_index, request in enumerate(requests):
            if not isinstance(request, dict):
                raise SourceError('Invalid request')
            raw = last_operation.get(request_index, records[0][0])
            rid = str(request.get('requestId') or f'request-{request_index}')
            timestamp = request.get('timestamp', state.get('creationDate'))
            common = dict(timestamp=timestamp, timestamp_unit='ms', model=request.get('modelId'),
                          nativeType='request', sourceLocator={'requestIndex': request_index})
            user = request.get('message') or {}
            content = blocks(user.get('text')) if isinstance(user, dict) else blocks(user)
            if content:
                doc.add(raw, 'user', content, native_id=rid, **common)
            response = request.get('response') or []
            if isinstance(response, str):
                response = [{'value': response}]
            if not isinstance(response, list):
                raise SourceError('Invalid response chunks')
            for chunk_index, chunk in enumerate(response):
                if not isinstance(chunk, dict):
                    chunk = {'value': chunk}
                kind = chunk.get('kind', 'markdown')
                args = {**common, 'native_id': f'{rid}:response-{chunk_index}', 'nativeType': kind,
                        'sourceLocator': {'requestIndex': request_index, 'chunkIndex': chunk_index}}
                if kind in ('markdown', 'markdownContent') or 'kind' not in chunk:
                    if chunk.get('value'):
                        doc.add(raw, 'assistant', text(chunk['value']), **args)
                elif kind == 'thinking':
                    doc.add(raw, 'assistant', [{'type': 'thinking', 'thinking': text(
                        chunk.get('value', chunk.get('text', chunk.get('thinking', ''))))}], kind='thinking', **args)
                elif kind == 'toolInvocationSerialized':
                    call_id = chunk.get('toolCallId')
                    doc.add(raw, 'assistant', [{'type': 'tool_use', 'id': call_id,
                            'name': chunk.get('toolId', chunk.get('toolName', 'tool')),
                            'input': chunk.get('toolSpecificData', chunk.get('invocationMessage'))}],
                            toolCallId=call_id, status=chunk.get('isComplete'), **args)
                    if chunk.get('resultDetails') is not None:
                        doc.add(raw, 'tool', [{'type': 'tool_result', 'tool_use_id': call_id,
                                'content': chunk['resultDetails']}], toolCallId=call_id, **args)
                else:
                    # Keep references, edits, questions and future chunk kinds as structured blocks.
                    doc.add(raw, 'system', [{'type': kind, 'value': chunk}], **args)
            if request.get('result'):
                result = request['result']
                doc.add(raw, 'system', [{'type': 'request_result', 'value': result}],
                        native_id=f'{rid}:result', **common)
        return doc.finish()
