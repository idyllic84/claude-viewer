"""Synthetic local server for the optional dependency-free Edge smoke test."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import create_app
from service import ClaudeViewerService
from sources.pi import PiAdapter
from sources.claude import ClaudeAdapter
from sources.codex import CodexAdapter
from sources.copilot_cli import CopilotCLIAdapter
from sources.vscode_chat import VSCodeChatAdapter
from werkzeug.serving import make_server


def main():
    root = Path(sys.argv[1])
    pi = root / 'pi'; pi.mkdir()
    records = [{'type': 'session', 'version': 3, 'id': 'pi-test', 'cwd': 'C:/synthetic/project'},
               {'type': 'message', 'id': 'u', 'parentId': None,
                'message': {'role': 'user', 'content': '<img src=x onerror="window.__xss=1"> synthetic question'}}]
    parent = 'u'
    for i in range(205):
        content = [{'type': 'text', 'text': f'assistant answer {i}'}]
        if i == 0:
            content += [{'type': 'thinking', 'thinking': 'synthetic reasoning'},
                        {'type': 'toolCall', 'id': 'call', 'name': 'bash', 'arguments': {'command': 'echo test'}}]
        records.append({'type': 'message', 'id': f'a{i}', 'parentId': parent,
                        'message': {'role': 'assistant', 'content': content}})
        parent = f'a{i}'
    records.append({'type': 'message', 'id': 'alternate', 'parentId': 'u',
                    'message': {'role': 'assistant', 'content': 'alternate saved branch'}})
    (pi / 's.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
    claude = root / 'claude'; claude.mkdir()
    (claude / 's.jsonl').write_text(json.dumps({'type': 'user', 'sessionId': 'claude-test',
        'cwd': 'C:/synthetic/other', 'message': {'content': 'claude question'}}) + '\n', encoding='utf-8')
    adapters = [PiAdapter([pi]), ClaudeAdapter([claude]), CodexAdapter([]), CopilotCLIAdapter([]), VSCodeChatAdapter([])]
    service = ClaudeViewerService(root / 'archive.db', adapters)
    service.initialize(); service.sync_sessions()
    app = create_app(service)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
