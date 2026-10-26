from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))


@unittest.skipUnless(os.name == 'posix', 'Linux-specific acceptance checks')
class LinuxHost(unittest.TestCase):
    def setUp(self):
        from linux_app import make_app
        self.temp = tempfile.TemporaryDirectory()
        self.config = {'StateRoot': self.temp.name, 'WorkspaceRoot': self.temp.name,
                       'PythonExecutable': sys.executable, 'ModelBackend': 'compatible',
                       'ModelId': 'test-model', 'ModelBaseUrl': 'http://127.0.0.1:9989/v1'}
        self.server = make_app(self.config, 0, 9988)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)
        self.temp.cleanup()

    def fetch(self, route, method='GET', payload=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, route, None if payload is None else json.dumps(payload), headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_frontend_loads_and_provides_private_app_token(self):
        status, html = self.fetch('/')
        self.assertEqual(status, 200)
        self.assertIn(b'Linux Control', html)
        token = re.search(rb'name="awx-token" content="([0-9a-f]+)"', html)[1].decode()
        status, state = self.fetch('/api/state', headers={'X-Awx-Token': token})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(state)['active']['model']['modelId'], 'test-model')

    def test_api_rejects_foreign_origin_and_missing_token(self):
        self.assertEqual(self.fetch('/api/state')[0], 403)
        self.assertEqual(self.fetch('/', headers={'Origin': 'https://evil.invalid'})[0], 403)

    def test_assets_and_path_traversal(self):
        self.assertEqual(self.fetch('/app.js')[0], 200)
        self.assertEqual(self.fetch('/style.css')[0], 200)
        self.assertEqual(self.fetch('/../../LICENSE')[0], 404)

    def test_linux_project_identity_preserves_case(self):
        import project_memory
        from pathlib import Path
        upper = Path(self.temp.name)/'Project'
        lower = Path(self.temp.name)/'project'
        upper.mkdir(); lower.mkdir()
        self.assertNotEqual(project_memory.project_key({'workspaceRoot': str(upper)}),
                            project_memory.project_key({'workspaceRoot': str(lower)}))

    def test_client_profile_uses_separate_linux_gateway_and_home(self):
        import control_core as core
        self.config['GatewayPort'] = 8766
        self.config['HarnessPort'] = 3081
        with patch.object(core, 'port_open', return_value=False) as check:
            prepared = core.prepare_clients(self.config)
        patch_text = (Path(prepared['HarnessHome'])/'profiles/web/cordis.patch.yml').read_text()
        self.assertIn('127.0.0.1:8766/profiles/default/v1', patch_text)
        self.assertIn(self.temp.name, prepared['HarnessHome'])

    def test_harness_authentication_is_ready_without_exposing_its_signin_link(self):
        import linux_clients
        root=Path(self.temp.name)
        log=root/'logs/client-harness.engine.log';log.parent.mkdir()
        log.write_text('dsh web: http://127.0.0.1:3081/?test-only=fake\n')
        harness=linux_clients.Harness(self.config)
        class Response:
            status=401
        class Connection:
            def __init__(self,*args,**kwargs):pass
            def request(self,*args,**kwargs):pass
            def getresponse(self):return Response()
            def close(self):pass
        with patch.object(harness.processes,'status',return_value={'owned':True}), patch.object(linux_clients,'HTTPConnection',Connection):
            result=harness.status()
            self.assertTrue(result['ready'])
            self.assertNotIn('test-only',json.dumps(result))
            self.assertIn('test-only',harness.open_url())

    def test_harness_rejects_signin_links_for_another_origin_or_unowned_process(self):
        import linux_clients
        log=Path(self.temp.name)/'logs/client-harness.engine.log';log.parent.mkdir()
        log.write_text('dsh web: http://example.invalid:3081/?test-only=fake\n')
        harness=linux_clients.Harness(self.config)
        with patch.object(harness.processes,'status',return_value={'owned':True}):
            with self.assertRaises(ValueError):harness.open_url()
        with patch.object(harness.processes,'status',return_value={'owned':False}):
            with self.assertRaises(ValueError):harness.open_url()

    def test_other_backends_retain_history_without_claiming_compaction(self):
        import io
        import uuid
        from conversation_sessions import Sessions
        from session_gateway import SessionRun
        import control_core as core
        import project_memory
        profile = core.get_profile(core.ensure_state(self.config))
        profile['model']['backend'] = 'ollama'
        core.save_profile(self.config, {'profile': profile, 'revision': 0})
        sid = uuid.uuid4().hex
        run = SessionRun(self.config, profile, {'id': sid, 'requestId': uuid.uuid4().hex}, 'First question')
        run.text, run.reason = 'Saved first reply', 'stop'; run.close()
        sent = []
        class BackendConnection:
            def request(self, method, url, body, headers):
                sent.append(json.loads(body))
            def getresponse(self):
                response = io.BytesIO(b'data: {"choices":[{"delta":{"content":"Next reply"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
                response.status = 200
                return response
            def close(self): pass
        token = re.search(rb'name="awx-token" content="([0-9a-f]+)"', self.fetch('/')[1])[1].decode()
        with patch('linux_app.HTTPConnection', return_value=BackendConnection()):
            status, wire = self.fetch('/api/chat', 'POST', {'message': 'Next question', 'sessionId': sid},
                {'Content-Type': 'application/json', 'X-Awx-Token': token})
        self.assertEqual(status, 200)
        self.assertNotIn('aibrainworx_session', sent[0])
        self.assertIn('Saved first reply', json.dumps(sent[0]['messages']))
        rows = Sessions(self.temp.name).transcript(sid, project_memory.project_key(profile))
        self.assertEqual(rows[-1]['content'], 'Next reply')


@unittest.skipUnless(os.name == 'posix', 'Linux-specific process checks')
class Ownership(unittest.TestCase):
    def setUp(self):
        from posix_lifecycle import Processes
        self.temp = tempfile.TemporaryDirectory()
        self.config = {'StateRoot': self.temp.name}
        self.manager = Processes(self.config)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        self.profile = {'id': 'fixture', 'workspaceRoot': self.temp.name, 'model': {
            'baseUrl': f'http://127.0.0.1:{port}/v1',
            'launch': {'executable': sys.executable, 'args': ['-u', '-c', 'import time; time.sleep(60)']}}}

    def tearDown(self):
        self.manager.stop('fixture')
        self.temp.cleanup()

    def test_native_start_and_owned_sigterm(self):
        self.assertEqual(self.manager.start(self.profile)['state'], 'starting')
        self.assertTrue(self.manager.status('fixture')['owned'])
        self.assertEqual(self.manager.stop('fixture')['state'], 'stopped')
        self.assertFalse(self.manager.status('fixture')['owned'])

    def test_pid_reuse_record_never_authorizes_kill(self):
        from posix_lifecycle import identity
        import control_core
        record = identity(os.getpid())
        record['start'] = '0'
        control_core.write_json(self.manager.path, {'fixture': record})
        self.assertEqual(self.manager.stop('fixture')['state'], 'stopped')
        self.assertIsNotNone(identity(os.getpid()))

    def test_process_title_changes_keep_ownership_but_exec_changes_do_not(self):
        from posix_lifecycle import identity, same
        record = identity(os.getpid())
        record['command'] = 'changed process title'
        self.assertTrue(same(record))
        record['executable'] = [0, 0]
        self.assertFalse(same(record))

    def test_windows_executable_is_not_a_native_linux_engine(self):
        self.profile['model']['launch']['executable'] = '/mnt/c/Windows/System32/cmd.exe'
        with self.assertRaises(ValueError):
            self.manager.start(self.profile)


if __name__ == '__main__':
    unittest.main()
