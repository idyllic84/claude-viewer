"""Source-independent archive model. Original records are stored separately."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json


def timestamp_ms(value, unit=None):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (float, int)):
        try:
            return int(value if unit == 'ms' or (unit is None and abs(value) >= 100_000_000_000) else value * 1000)
        except (ValueError, OverflowError):
            return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (ValueError, TypeError, OverflowError):
        return None


def text(value):
    if value is None:
        return ''
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def blocks(content):
    """Normalize typed content without discarding unknown or non-text blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}] if content else []
    if not isinstance(content, list):
        return [{'type': 'unknown', 'value': content}]
    result = []
    for block in content:
        if not isinstance(block, dict):
            result.append({'type': 'unknown', 'value': block})
            continue
        kind = block.get('type')
        if kind in ('text', 'Text', 'input_text', 'output_text'):
            result.append({'type': 'text', 'text': text(block.get('text', ''))})
        elif kind in ('thinking', 'reasoning'):
            result.append({'type': 'thinking', 'thinking': text(block.get('thinking', block.get('text', '')))})
        elif kind in ('toolCall', 'tool_use'):
            result.append({'type': 'tool_use', 'id': block.get('id'), 'name': block.get('name'),
                           'input': block.get('arguments', block.get('input', {}))})
        else:
            result.append(dict(block))
    return result


def searchable_text(content):
    parts = []
    for block in content:
        kind = block.get('type')
        if kind == 'text':
            parts.append(text(block.get('text')))
        elif kind == 'thinking':
            parts.append(text(block.get('thinking')))
        elif kind == 'tool_use':
            parts.extend([text(block.get('name')), text(block.get('input'))])
        elif kind == 'tool_result':
            value = block.get('content')
            parts.append(searchable_text(blocks(value)) if isinstance(value, list) else text(value))
        elif kind not in ('image', 'input_image', 'output_image', 'encrypted_reasoning'):
            parts.append(text(block))
    return '\n'.join(parts)


@dataclass
class SessionDocument:
    source: str
    external_id: str
    project: str
    records: list
    name: str = ''
    messages: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    _subindices: dict = field(default_factory=dict, init=False, repr=False)

    @property
    def id(self):
        return f'{self.source}:{self.external_id}'

    def add(self, record_index, role, content, *, native_id=None, parent_id=None,
            timestamp=None, timestamp_unit=None, kind=None, **metadata):
        content = blocks(content)
        if kind is None:
            types = {b.get('type') for b in content}
            kind = 'tool-result' if types == {'tool_result'} else (
                'tool-use' if types == {'tool_use'} else role)
        subindex = self._subindices.get(record_index, 0)
        self._subindices[record_index] = subindex + 1
        message = {
            'id': f'{self.id}:{record_index}:{subindex}', 'sessionId': self.id,
            'source': self.source, 'type': kind, 'role': role,
            'uuid': str(native_id) if native_id is not None else f'record-{record_index}',
            'parentUuid': parent_id, 'timestamp': timestamp_ms(timestamp, timestamp_unit),
            'timestampUnit': 'ms', 'message': {'role': role, 'content': content},
            'rawRecord': record_index, 'relatedRawRecords': [], **metadata,
        }
        self.messages.append(message)
        return message

    def finish(self):
        if not self.name:
            for m in self.messages:
                if m['type'] == 'user':
                    preview = searchable_text(m['message']['content']).strip()
                    if preview:
                        self.name = preview.replace('\n', ' ')[:160]
                        break
        self.name = self.name or self.external_id
        return self
