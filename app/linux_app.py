"""Separate Linux local control plane. Python stdlib UI host + reused gateway."""
import argparse
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading
from urllib.parse import urlsplit, parse_qs
import uuid
import webbrowser

import control_core as core
import control_runtime
import model_backends
import project_memory
import plugin_host
from session_gateway import SessionRun
from linux_clients import Harness, installed_strata_model
from linux_paths import state_root
import update_center
from posix_lifecycle import Processes

ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 2*1024*1024


def update_progress(config, identity):
    """Expose check counts only, never arbitrary contents from local state."""
    path = update_center.progress_path(config, identity)
    if path.is_symlink():
        raise ValueError('Update progress must be a local record.')
    if not path.exists():
        return {'completed': 0, 'total': 0, 'state': 'pending', 'unit': 'checks'}
    if path.stat().st_size > 4096:
        raise ValueError('Update progress is unavailable.')
    progress = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(progress, dict):
        raise ValueError('Update progress is unavailable.')
    completed, total = progress.get('completed'), progress.get('total')
    phase = progress.get('state')
    if (type(completed) is not int or type(total) is not int or not 1 <= total <= 10000 or
            not 0 <= completed <= total or phase not in ('checking', 'complete') or
            progress.get('unit') != 'checks' or (phase == 'complete' and completed != total)):
        raise ValueError('Update progress is unavailable.')
    return dict(completed=completed, total=total, state=phase, unit='checks')


def make_app(config, port=8765, gateway_port=8766):
    token = secrets.token_hex(32)
    processes = Processes(config)
    harness = Harness(config)
    # HELP-ACTIVITY: panels reuse telemetry returned by the existing status route.
    # No Activity request probes a model API or starts a process inspection itself.
    telemetry = {'snapshot': None}
    def active():
        return core.get_profile(core.ensure_state(config))
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass
        def json(self, status, data):
            raw = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers(); self.wfile.write(raw)
            self.close_connection = True
        def allowed(self, api=False):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            hosts = (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
            if self.headers.get('Host') not in hosts:
                return False
            if self.headers.get('Origin') not in (None, origin, origin.replace('127.0.0.1', 'localhost')):
                return False
            return not api or secrets.compare_digest(self.headers.get('X-Awx-Token', ''), token)
        def do_OPTIONS(self):
            self.json(403, {'error': 'Cross-origin access is not enabled.'})
        def do_GET(self):
            path = urlsplit(self.path)
            if not self.allowed(path.path.startswith('/api/')):
                return self.json(403, {'error': 'Local app access required.'})
            try:
                if path.path == '/api/state':
                    return self.json(200, core.view_state(config))
                if path.path == '/api/status':
                    profile = active()
                    status = model_backends.status(config, profile)
                    owned = processes.status(profile['id'])
                    metrics = status.get('modelMetrics', status.get('metrics'))
                    telemetry['snapshot'] = {'profile': profile['id'], 'model': json.dumps(profile['model'], sort_keys=True),
                                             'metrics': metrics if isinstance(metrics, dict) else None}
                    return self.json(200, {**status, **owned, 'ready': status['ready'],
                        'detail': owned.get('detail', status['detail']) if not status['ready'] else status['detail'],
                        'state': 'ready' if status['ready'] else 'starting' if owned['owned'] else 'error' if owned['state'] == 'error' else 'offline',
                        'platform': 'WSL2' if 'microsoft' in os.uname().release.lower() else 'Linux'})
                if path.path == '/api/catalog':
                    return self.json(200, core.read_json(ROOT/'app/model-details.json'))
                if path.path == '/api/client-status':
                    return self.json(200, harness.status())
                if path.path == '/api/memory':
                    return self.json(200, {'items': project_memory.items(config, active())})
                if path.path == '/api/plugins':
                    return self.json(200, {'items': plugin_host.inventory(config)})
                if path.path == '/api/plugin-archives':
                    return self.json(200, plugin_host.archives(config))
                if path.path == '/api/update-progress':
                    identity = parse_qs(path.query).get('id', [''])[0]
                    return self.json(200, update_progress(config, identity))
                if path.path == '/api/transcript':
                    from conversation_sessions import Sessions
                    identity = parse_qs(path.query).get('id', [''])[0]
                    store = Sessions(config['StateRoot'])
                    return self.json(200, {'items': store.transcript(identity, project_memory.project_key(active()))})
                files = {'/': (ROOT/'web/index.html', 'text/html; charset=utf-8'),
                         '/app.js': (ROOT/'web/app.js', 'text/javascript; charset=utf-8'),
                         '/style.css': (ROOT/'web/style.css', 'text/css; charset=utf-8'),
                         '/logo.png': (ROOT/'media/aibrainworx-wordmark-green.png', 'image/png')}
                if path.path not in files:
                    return self.json(404, {'error': 'Not found'})
                file, content_type = files[path.path]
                raw = file.read_bytes().replace(b'__AWX_TOKEN__', token.encode()) if path.path == '/' else file.read_bytes()
                self.send_response(200); self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.send_header('X-Content-Type-Options', 'nosniff'); self.end_headers(); self.wfile.write(raw)
            except (ValueError, KeyError):
                self.json(422, {'error': 'That local record is unavailable.'})
            except OSError:
                self.json(502, {'error': 'That local record is temporarily unavailable.'})
        def do_POST(self):
            if not self.allowed(True):
                return self.json(403, {'error': 'Local app token required.'})
            connection = None
            local_session = None
            started = False
            try:
                size = int(self.headers.get('Content-Length', '-1'))
                if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding') or not 0 <= size <= MAX_BODY:
                    return self.json(413, {'error': 'A bounded JSON request is required.'})
                self.connection.settimeout(15)
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('A JSON object is required.')
                route = urlsplit(self.path).path
                if route == '/api/profile':
                    return self.json(200, core.save_profile(config, payload))
                if route == '/api/import-strata':
                    return self.json(200, {'model': installed_strata_model(config)})
                if route == '/api/select':
                    return self.json(200, core.select_profile(config, payload))
                if route == '/api/start':
                    return self.json(200, processes.start(active()))
                if route == '/api/stop':
                    return self.json(200, processes.stop(active()['id']))
                if route == '/api/client-start':
                    return self.json(200, harness.start())
                if route == '/api/client-stop':
                    return self.json(200, harness.stop())
                if route == '/api/client-open':
                    return self.json(200, {'url': harness.open_url()})
                if route == '/api/updates':
                    progress = update_center.progress_writer(config, payload['progressId']) if payload.get('progressId') else None
                    return self.json(200, update_center.check(config, core.VERSION, payload.get('includePrereleases', True), progress=progress))
                if route == '/api/update-progress-clear':
                    path = update_center.progress_path(config, payload.get('id'))
                    if path.is_symlink():
                        raise ValueError('Update progress must be a local record.')
                    path.unlink(missing_ok=True)
                    return self.json(200, {'note': 'This check progress record was cleared.'})
                if route == '/api/model-source':
                    return self.json(200, update_center.save_model_source(config, payload))
                if route == '/api/plugin-approve':
                    return self.json(200, plugin_host.approve(config, payload))
                if route == '/api/plugin-remove':
                    return self.json(200, plugin_host.remove(config, payload))
                if route == '/api/plugin-restore':
                    return self.json(200, plugin_host.restore(config, payload))
                if route == '/api/plugin-panels':
                    profile = active()
                    cached = telemetry['snapshot']
                    metrics = cached['metrics'] if cached and cached['profile'] == profile['id'] and cached['model'] == json.dumps(profile['model'], sort_keys=True) else None
                    inspect_processes = payload.get('inspectProcesses') is True
                    return self.json(200, plugin_host.panels(config, {
                        'backend': profile['model']['backend'], 'modelMetrics': metrics,
                        'inspectProcesses': inspect_processes,
                        'includeGpuProcesses': inspect_processes and payload.get('includeGpuProcesses') is True}))
                if route == '/api/memory':
                    return self.json(200, project_memory.save(config, active(), payload))
                if route == '/api/memory-archive':
                    return self.json(200, project_memory.archive(config, active(), payload))
                if route == '/api/chat':
                    profile = active()
                    message = payload.get('message')
                    if not isinstance(message, str) or not message.strip() or len(message) > 16000:
                        raise ValueError('Enter a message of up to 16,000 characters.')
                    customized = plugin_host.run_hook(config, 'chat.before_send', {'message': message})
                    if not isinstance(customized.get('message'), str) or len(customized['message']) > 16000:
                        raise ValueError('The plugin returned an invalid message.')
                    descriptor = {'id': payload['sessionId'], 'requestId': payload.get('requestId') or uuid.uuid4().hex}
                    body = {'model': profile['model']['modelId'], 'messages': [{'role': 'user', 'content': customized['message']}],
                            'max_tokens': profile['maxOutputTokens'], 'stream': True,
                            'aibrainworx_session': descriptor}
                    if profile['model']['backend'] in ('ollama', 'lmstudio') or (profile['model']['backend'] == 'strata' and not config.get('StrataRoot')):
                        # Preserve history for transports without a validated exact
                        # counter. No approximate compaction or hidden token claims.
                        local_session = SessionRun(config, profile, descriptor, customized['message'])
                        body['messages'] = local_session.store.working(local_session.identity, local_session.project)
                        body.pop('aibrainworx_session')
                    connection = HTTPConnection('127.0.0.1', gateway_port, timeout=600)
                    connection.request('POST', '/profiles/'+profile['id']+'/v1/chat/completions', json.dumps(body), {'Content-Type': 'application/json'})
                    reply = connection.getresponse()
                    if reply.status != 200:
                        detail = json.loads(reply.read(4096)).get('error', {})
                        raise ValueError(detail.get('message', 'The local model could not start the reply.') if isinstance(detail, dict) else 'Model unavailable.')
                    self.send_response(200); self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
                    self.send_header('Cache-Control', 'no-store'); self.send_header('Connection', 'close'); self.end_headers()
                    started = self.close_connection = True
                    from guarded_stream import events, frame
                    text, finish = '', None
                    for event in events(reply):
                        for choice in event.get('choices', []):
                            text += choice.get('delta', {}).get('content', '')
                            finish = choice.get('finish_reason') or finish
                        wire = frame(event)
                        if local_session:
                            local_session.record(wire)
                        self.wfile.write(wire); self.wfile.flush()
                    self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
                    plugin_host.run_hook(config, 'chat.after_reply', {'reply': text, 'finishReason': finish})
                    return
                return self.json(404, {'error': 'Not found'})
            except (ValueError, KeyError, TypeError) as exc:
                if not started:
                    self.json(422, {'error': str(exc) if isinstance(exc, ValueError) else 'Invalid local request.'})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except OSError:
                if not started:
                    self.json(502, {'error': 'The local backend is unavailable. Check its address and readiness.'})
            finally:
                if local_session:
                    local_session.close()
                if connection:
                    connection.close()
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    if sys.platform != 'linux':
        raise SystemExit('This separate launcher runs inside Linux/WSL. Use Launch.sh.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', default=str(state_root()))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--gateway-port', type=int, default=8766)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    state = Path(args.state).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    config_path = state/'linux-config.local.json'
    if config_path.exists():
        config = core.read_json(config_path)
    else:
        config = {'StateRoot': str(state), 'WorkspaceRoot': str(ROOT), 'PythonExecutable': sys.executable,
                  'HarnessEntry': '', 'StrataRoot': '', 'ModelBackend': 'compatible',
                  'ModelBaseUrl': 'http://127.0.0.1:8080/v1', 'ModelId': 'select-a-local-model'}
        core.write_json(config_path, config)
    config['GatewayPort'] = args.gateway_port
    config['AppPort'] = args.port
    config.setdefault('HarnessPort', 3081)
    config['_configPath'] = str(config_path)
    config['LauncherRepository'] = 'https://github.com/AiBrainWorXdotCom/AiBrainWorX-Local-AI-Control-Linux'
    core.ensure_state(config)
    gateway = control_runtime.make_server(config, args.gateway_port)
    thread = threading.Thread(target=gateway.serve_forever, daemon=True); thread.start()
    app = make_app(config, args.port, args.gateway_port)
    url = f'http://127.0.0.1:{app.server_port}'
    print('AiBrainWorX Linux '+core.VERSION+' — '+url, flush=True)
    print('Model weights and inference engines are separate. No model has been downloaded or started.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        app.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.server_close(); gateway.shutdown(); gateway.server_close(); thread.join(3)


if __name__ == '__main__':
    main()
