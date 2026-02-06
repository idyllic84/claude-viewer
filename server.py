"""
Flask server for Claude Viewer
HTTP API layer
"""

import json
import sys
import signal
from pathlib import Path
from flask import Flask, jsonify, send_from_directory, request
from flask_compress import Compress

from service import ClaudeViewerService

BASE_DIR = Path(__file__).parent

app = Flask(__name__,
            static_folder=str(BASE_DIR / 'static'),
            static_url_path='/static')

Compress(app)

service = ClaudeViewerService()

ERROR_STATUS_MAP = {
    'invalid_input': 400,
    'not_found': 404,
    'server_error': 500
}


def handle_service_error(result):
    if result.get('error'):
        error_type = result.get('error_type', 'server_error')
        status_code = ERROR_STATUS_MAP.get(error_type, 500)
        return jsonify({'error': result['error']}), status_code
    return None


@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({'error': str(e)}), 500


@app.route('/')
def index():
    return send_from_directory(BASE_DIR / 'static', 'index.html')


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(BASE_DIR / 'static', 'favicon.ico')


@app.route('/sessions')
def get_sessions():
    project_filter = request.args.get('project')
    sessions = service.get_all_sessions(project_filter)
    return jsonify(sessions)


@app.route('/sessions/search')
def search_sessions():
    """Search sessions by message content"""
    search_term = request.args.get('q', '')

    # If empty search, return all sessions directly (no DB search needed)
    if not search_term or search_term.strip() == '':
        sessions = service.get_all_sessions()
    else:
        sessions = service.search_sessions_by_content(search_term)

    return jsonify(sessions)


@app.route('/sessions/sync', methods=['POST'])
def sync_sessions_endpoint():
    sessions = service.sync_sessions_from_claude_files()
    return jsonify(sessions)


@app.route('/session/<session_id>')
def get_session_messages(session_id):
    result = service.get_session_with_messages(session_id)

    error_response = handle_service_error(result)
    if error_response:
        return error_response

    messages = [json.loads(row['raw_json']) for row in result['messages']]
    return jsonify(messages)


@app.route('/session/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    result = service.delete_session(session_id)

    if result['success']:
        return jsonify({
            'success': True,
            'deleted': result['deleted']
        }), 200
    else:
        return jsonify({
            'success': False,
            'deleted': result['deleted'],
            'errors': result['errors']
        }), 500


def shutdown_handler(signum, frame):
    print(f"\nReceived signal {signum}, shutting down gracefully...")
    service.shutdown()
    sys.exit(0)


if __name__ == '__main__':
    debug_mode = '--debug' in sys.argv

    print(f"Claude Viewer Server")

    service.initialize()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    print(f"Starting server on http://localhost:8000")
    print(f"Press Ctrl+C to stop")

    if not debug_mode:
        print(f"\nNote: Running in production mode. Use --debug for development mode.")

    try:
        app.run(
            host='localhost',
            port=8000,
            debug=debug_mode,
            use_reloader=debug_mode
        )
    finally:
        service.shutdown()
