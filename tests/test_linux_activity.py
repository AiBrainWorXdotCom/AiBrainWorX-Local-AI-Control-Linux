"""Authenticated Activity/update APIs on an isolated stdlib HTTP host.

No native client, model server, inference request, or external process is used.
These transport checks run on Windows too; process ownership is mocked here.
"""
from contextlib import ExitStack
from http.client import HTTPConnection
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import control_core as core
import linux_app


class LinuxActivityApi(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='aibrainworx-activity-test-')
        self.addCleanup(self.temp.cleanup)
        self.config = {'StateRoot': self.temp.name, 'WorkspaceRoot': self.temp.name,
                       'PythonExecutable': sys.executable, 'ModelBackend': 'compatible',
                       'ModelId': 'fixture-model', 'ModelBaseUrl': 'http://127.0.0.1:9989/v1'}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.processes = self.stack.enter_context(patch.object(linux_app, 'Processes', autospec=True)).return_value
        self.processes.status.return_value = {'owned': False, 'state': 'stopped'}
        self.harness = self.stack.enter_context(patch.object(linux_app, 'Harness', autospec=True)).return_value
        self.backend_status = self.stack.enter_context(patch.object(linux_app.model_backends, 'status',
            side_effect=AssertionError('Activity must not probe the model backend.')))
        self.backend_connection = self.stack.enter_context(patch.object(linux_app, 'HTTPConnection',
            side_effect=AssertionError('Activity must not open a model connection.')))
        self.server = linux_app.make_app(self.config, 0, 9988)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        code, html = self.fetch('/')
        self.assertEqual(code, 200)
        self.token = re.search(rb'name="awx-token" content="([0-9a-f]+)"', html)[1].decode()

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.processes.start.assert_not_called()
        self.processes.stop.assert_not_called()
        self.harness.start.assert_not_called()
        self.harness.stop.assert_not_called()

    def fetch(self, route, method='GET', payload=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request(method, route,
                None if payload is None else json.dumps(payload), headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def request(self, route, payload=None, method=None, extra_headers=None):
        method = method or ('GET' if payload is None else 'POST')
        headers = {'X-Awx-Token': self.token}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        headers.update(extra_headers or {})
        code, raw = self.fetch(route, method, payload, headers)
        return code, json.loads(raw)

    def test_activity_and_progress_routes_require_token_and_same_origin(self):
        routes = [('/api/plugin-panels', {}), ('/api/plugin-remove', {}),
                  ('/api/plugin-restore', {}), ('/api/plugin-archives', None), ('/api/plugin-memory-policy', None), ('/api/plugin-memory-policy', {'level':'all'}),
                  ('/api/updates', {}), ('/api/update-progress?id=' + 'a' * 32, None),
                  ('/api/update-progress-clear', {'id': 'a' * 32})]
        for route, payload in routes:
            with self.subTest(route=route):
                method = 'GET' if payload is None else 'POST'
                headers = {} if payload is None else {'Content-Type': 'application/json'}
                self.assertEqual(self.fetch(route, method, payload, headers)[0], 403)
                self.assertEqual(self.request(route, payload,
                    extra_headers={'Origin': 'https://foreign.invalid'})[0], 403)
        self.backend_status.assert_not_called()
        self.backend_connection.assert_not_called()

    def test_memory_policy_api_requires_approval_and_controls_levels(self):
        import shutil
        folder = Path(self.config['StateRoot'])/'plugins/memory-policy'
        folder.mkdir(parents=True)
        shutil.copy(Path(__file__).resolve().parents[1]/'examples/plugins/memory-policy/plugin.json', folder/'plugin.json')
        self.assertEqual(self.request('/api/plugin-memory-policy')[1]['level'], 'off')
        self.assertEqual(self.request('/api/plugin-memory-policy', {'level':'all'})[0], 422)
        item = self.request('/api/plugins')[1]['items'][0]
        self.assertEqual(self.request('/api/plugin-approve', {'id':item['id'], 'fingerprint':item['fingerprint'], 'enabled':True, 'acceptLocalCodeRisk':True})[0], 200)
        self.assertEqual(self.request('/api/plugin-memory-policy', {'level':'verified'})[0], 200)
        self.assertEqual(self.request('/api/plugin-memory-policy')[1]['level'], 'verified')
        self.assertEqual(self.request('/api/plugin-memory-policy', {'level':'admin'})[0], 422)

    def test_light_panel_context_ignores_client_backend_metrics_and_gpu_flag(self):
        report = {'items': [{'id': 'fixture', 'name': 'Monitor', 'state': 'enabled',
                             'fingerprint': 'f' * 64, 'panel': {'metrics': [], 'services': [],
                                                            'processes': [], 'note': 'Fixture'}}]}
        with patch.object(linux_app.plugin_host, 'panels', return_value=report) as panels:
            code, body = self.request('/api/plugin-panels', {'backend': 'strata',
                'modelMetrics': {'private': 'attacker supplied'}, 'includeGpuProcesses': True})
        self.assertEqual((code, body), (200, report))
        args = panels.call_args.args
        self.assertEqual(args[0], self.config)
        self.assertEqual(args[1]['backend'], 'compatible')
        self.assertIsNone(args[1].get('modelMetrics'))
        self.assertIs(args[1]['inspectProcesses'], False)
        self.assertIs(args[1]['includeGpuProcesses'], False)
        self.backend_status.assert_not_called()
        self.backend_connection.assert_not_called()

    def test_activity_actions_require_a_bounded_json_object(self):
        for route in ('/api/plugin-panels', '/api/plugin-remove', '/api/plugin-restore',
                      '/api/updates', '/api/update-progress-clear'):
            with self.subTest(route=route):
                self.assertEqual(self.request(route, [1, 2, 3])[0], 422)
        code, _ = self.fetch('/api/plugin-panels', 'POST', {}, {'X-Awx-Token': self.token,
                                                              'Content-Type': 'text/plain'})
        self.assertEqual(code, 413)
        self.backend_status.assert_not_called()
        self.backend_connection.assert_not_called()

    def test_process_collection_requires_exact_explicit_boolean(self):
        cases = [({}, False, False), ({'inspectProcesses': 'true', 'includeGpuProcesses': True}, False, False),
                 ({'inspectProcesses': 1, 'includeGpuProcesses': True}, False, False),
                 ({'inspectProcesses': True, 'includeGpuProcesses': 'true'}, True, False),
                 ({'inspectProcesses': True, 'includeGpuProcesses': True}, True, True)]
        with patch.object(linux_app.plugin_host, 'panels', return_value={'items': []}) as panels:
            for payload, inspect, gpu in cases:
                with self.subTest(payload=payload):
                    self.assertEqual(self.request('/api/plugin-panels', payload)[0], 200)
                    context = panels.call_args.args[1]
                    self.assertIs(context['inspectProcesses'], inspect)
                    self.assertIs(context['includeGpuProcesses'], gpu)
        self.backend_status.assert_not_called()
        self.backend_connection.assert_not_called()

    def test_panels_reuse_only_metrics_from_preexisting_status_route_and_active_profile(self):
        metrics = {'hardware': {'cpu': 12.5, 'ram_used': 1024, 'ram_total': 4096},
                   'live': {'state': 'idle', 'tok_s': 42}}
        self.backend_status.side_effect = None
        self.backend_status.return_value = {'ready': True, 'detail': 'Fixture model ready.',
                                            'modelMetrics': metrics}
        with patch.object(linux_app.os, 'uname', return_value=SimpleNamespace(release='fixture'), create=True):
            self.assertEqual(self.request('/api/status')[0], 200)
        with patch.object(linux_app.plugin_host, 'panels', return_value={'items': []}) as panels:
            self.assertEqual(self.request('/api/plugin-panels', {})[0], 200)
            self.assertEqual(panels.call_args.args[1]['modelMetrics'], metrics)
            view = core.view_state(self.config)
            profile = json.loads(json.dumps(view['active']))
            profile['model']['modelId'] = 'different-model'
            with patch.object(core, 'port_open', return_value=False):
                core.save_profile(self.config, {'profile': profile, 'revision': view['revision']})
            self.assertEqual(self.request('/api/plugin-panels', {})[0], 200)
            self.assertIsNone(panels.call_args.args[1].get('modelMetrics'))
        self.assertEqual(self.backend_status.call_count, 1)
        self.backend_connection.assert_not_called()

    def test_archive_lifecycle_forwards_only_authenticated_explicit_actions(self):
        operations = [('/api/plugin-remove', 'remove', {'id': 'monitor', 'fingerprint': 'f' * 64},
                       {'note': 'Archived', 'archiveId': 'archive-fixture'}),
                      ('/api/plugin-restore', 'restore', {'archiveId': 'archive-fixture'},
                       {'note': 'Restored disabled', 'id': 'monitor'})]
        for route, name, payload, response in operations:
            with self.subTest(route=route), patch.object(linux_app.plugin_host, name,
                                                        return_value=response) as operation:
                self.assertEqual(self.request(route, payload), (200, response))
                operation.assert_called_once_with(self.config, payload)
        with patch.object(linux_app.plugin_host, 'archives', return_value={'items': []}) as archives:
            self.assertEqual(self.request('/api/plugin-archives'), (200, {'items': []}))
            archives.assert_called_once_with(self.config)

    def test_updates_pass_private_progress_writer_and_real_counts(self):
        identity = 'a' * 32
        samples = []
        report = {'components': [], 'summary': {}, 'detail': 'Fixture check complete.'}
        def check(*args, **kwargs):
            self.assertEqual(args[:3], (self.config, core.VERSION, False))
            kwargs['progress']({'completed': 1, 'total': 2, 'state': 'checking', 'unit': 'checks'})
            return report
        with patch.object(linux_app.update_center, 'progress_writer', return_value=samples.append) as writer, \
             patch.object(linux_app.update_center, 'check', side_effect=check):
            self.assertEqual(self.request('/api/updates', {'includePrereleases': False,
                                                          'progressId': identity}), (200, report))
            writer.assert_called_once_with(self.config, identity)
        self.assertEqual(samples, [{'completed': 1, 'total': 2, 'state': 'checking', 'unit': 'checks'}])

    def progress_file(self, identity, value):
        path = linux_app.update_center.progress_path(self.config, identity)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def test_progress_pending_and_valid_result_are_private_bounded_data(self):
        identity = 'b' * 32
        self.assertEqual(self.request('/api/update-progress?id=' + identity),
            (200, {'completed': 0, 'total': 0, 'state': 'pending', 'unit': 'checks'}))
        self.progress_file(identity, {'completed': 2, 'total': 8, 'state': 'checking',
                                      'unit': 'checks', 'privatePath': 'never expose this value'})
        code, body = self.request('/api/update-progress?id=' + identity)
        self.assertEqual(code, 200)
        self.assertEqual(body, {'completed': 2, 'total': 8, 'state': 'checking', 'unit': 'checks'})
        self.assertNotIn('never expose', json.dumps(body))

    def test_invalid_progress_ids_and_records_never_expose_private_content(self):
        for identity in ('../config.local.json', 'a' * 31, 'A' * 32, 'z' * 32):
            with self.subTest(identity=identity):
                self.assertEqual(self.request('/api/update-progress?id=' + identity)[0], 422)
                self.assertEqual(self.request('/api/update-progress-clear', {'id': identity})[0], 422)
        identity = 'c' * 32
        bad_values = [{'completed': -1, 'total': 2, 'state': 'checking', 'unit': 'checks'},
                      {'completed': 3, 'total': 2, 'state': 'checking', 'unit': 'checks'},
                      {'completed': True, 'total': 2, 'state': 'checking', 'unit': 'checks'},
                      {'completed': 1.5, 'total': 2, 'state': 'checking', 'unit': 'checks'},
                      {'completed': 1, 'total': 2 ** 100, 'state': 'checking', 'unit': 'checks'},
                      {'completed': 1, 'total': 2, 'state': 'complete', 'unit': 'checks'},
                      {'completed': 1, 'total': 2, 'state': 'checking', 'unit': 'bytes'},
                      {'completed': 1, 'total': 2, 'state': 'private-secret', 'unit': 'checks'}]
        for value in bad_values:
            with self.subTest(value=value):
                self.progress_file(identity, {**value, 'privatePath': 'never expose this value'})
                code, body = self.request('/api/update-progress?id=' + identity)
                self.assertEqual(code, 422)
                self.assertNotIn('never expose', json.dumps(body))
        path = self.progress_file(identity, {})
        for raw in ('{"privatePath":"never expose this value",', 'x' * 4097):
            with self.subTest(raw_length=len(raw)):
                path.write_text(raw, encoding='utf-8')
                code, body = self.request('/api/update-progress?id=' + identity)
                self.assertEqual(code, 422)
                self.assertNotIn('never expose', json.dumps(body))

    def test_progress_clear_removes_only_the_supplied_record(self):
        own = 'd' * 32
        other = 'e' * 32
        sample = {'completed': 2, 'total': 2, 'state': 'complete', 'unit': 'checks'}
        own_path = self.progress_file(own, sample)
        other_path = self.progress_file(other, sample)
        self.assertEqual(self.request('/api/update-progress-clear', {'id': own})[0], 200)
        self.assertFalse(own_path.exists())
        self.assertTrue(other_path.exists())


if __name__ == '__main__':
    unittest.main()
