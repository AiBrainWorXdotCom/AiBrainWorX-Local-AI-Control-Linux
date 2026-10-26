"""Explicit read-only preflight probe. Requires the owner's local config path."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from backend_budget import Backend
from context_policy import fit_request
import model_backends

parser = argparse.ArgumentParser()
parser.add_argument('--config', required=True)
args = parser.parse_args()
config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
selected = model_backends.default_model(config)
backend = Backend(selected, config=config)
rows = []
for label, extra in [('plain', {}), ('tool-schema', {'tools': [{'type': 'function', 'function': {
        'name': 'read_status', 'description': 'Read a status value.',
        'parameters': {'type': 'object', 'properties': {}}}}]})]:
    payload = {'model': selected['modelId'], 'messages': [
        {'role': 'system', 'content': 'Return short answers.'},
        {'role': 'user', 'content': 'Reply ready.'}], 'reasoning_effort': 'none', 'max_tokens': 4096, **extra}
    started = time.monotonic()
    context, count = backend.preflight(payload)
    _, budget = fit_request(payload, prompt_tokens=count, server_context=context)
    rows.append({'case': label, **budget.view(), 'preflight_seconds': round(time.monotonic()-started, 3)})
print(json.dumps({'preflight': rows, 'generation_requests': 0, 'engine_changes': False}))
