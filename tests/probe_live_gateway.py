"""Explicit small live acceptance probe; no backend restarts or configuration edits."""
import argparse
from http.client import HTTPConnection
import io
import json
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import control_core as core
import control_runtime as runtime
from backend_budget import Backend
from guarded_stream import events

parser = argparse.ArgumentParser()
parser.add_argument('--config', required=True)
args = parser.parse_args()
config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
with tempfile.TemporaryDirectory() as temporary:
    config['StateRoot'] = temporary
    state = core.ensure_state(config)
    profile = state['profiles'][0]
    profile['model']['reasoningEffort'] = 'none'
    # Only this disposable test state's profile changes. The active Harness and
    # owner's saved profiles are never switched for an acceptance probe.
    core.write_json(core.state_path(config), state)
    server = runtime.make_server(config, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    rows = []
    try:
        for label, payload in [('normal', {'messages': [{'role': 'user', 'content': 'Reply exactly READY.'}], 'max_tokens': 16}),
                               ('forced-cutoff', {'messages': [{'role': 'user', 'content': 'Write a numbered list of twenty short facts about the Moon.'}], 'max_tokens': 8})]:
            payload.update(model=profile['model']['modelId'], stream=True, stream_options={'include_usage': True})
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=180)
            try:
                conn.request('POST', '/profiles/default/v1/chat/completions', json.dumps(payload), {'Content-Type': 'application/json'})
                response = conn.getresponse()
                wire = response.read()
                if response.status != 200:
                    raise RuntimeError('Acceptance request failed: ' + wire.decode())
                parsed = list(events(io.BytesIO(wire)))
                finals = [e for e in parsed if e.get('choices') and e['choices'][0].get('finish_reason')]
                rows.append({'case': label, 'http_status': response.status, 'done_events': wire.count(b'data: [DONE]'),
                             'finish': finals[-1]['choices'][0]['finish_reason'],
                             'guard': finals[-1].get('aibrainworx'),
                             'usage': next((e['usage'] for e in parsed if 'usage' in e), None)})
            finally:
                conn.close()
        # Responses and tool schemas: count only, do not submit an agent tool call.
        selected = profile['model']
        response_backend = Backend(selected, config=config, route='/v1/responses')
        context, count = response_backend.preflight({'model': selected['modelId'], 'input': 'Reply ready.', 'max_output_tokens': 16})
        rows.append({'case': 'responses-count-only', 'context': context, 'prompt_tokens': count})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
print(json.dumps({'live_gateway': rows, 'model_restarted': False, 'original_settings_modified': False}))
