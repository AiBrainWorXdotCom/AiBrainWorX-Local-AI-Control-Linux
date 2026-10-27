"""Independent Linux Harness home and verified owned-process controls."""
import os
import re
from pathlib import Path
from http.client import HTTPConnection
from urllib.parse import urlsplit

import control_core as core
import model_backends
from posix_lifecycle import Processes


def installed_strata_model(config):
    """Populate an editable connection; never save a profile or start an engine."""
    python = Path(config.get('StrataPython') or '/not-configured')
    server = Path(config.get('StrataRoot') or '/not-configured')/'serve/server.py'
    if not python.is_file() or not os.access(python, os.X_OK) or not server.is_file():
        raise ValueError('Install the native Linux Strata runtime first, then restart the launcher.')
    model = model_backends.strata_model(config.get('StrataConfig') or '')
    model['launch'] = {'executable': str(python), 'cwd': str(server.parents[1]),
        'args': [str(server), '--engine', 'strata', '--config', model['configPath'],
                 '--host', '127.0.0.1', '--port', '8081', '--offline']}
    return model


class Harness:
    def __init__(self, config):
        self.config = config
        self.processes = Processes(config)
        self.port = config.get('HarnessPort', 3081)

    def status(self):
        owned = self.processes.status('client-harness')['owned']
        ready = False
        conn = HTTPConnection('127.0.0.1', self.port, timeout=.4)
        try:
            conn.request('GET', '/')
            reply = conn.getresponse()
            ready = reply.status == 200 and b'<html' in reply.read(8192).lower()
            if reply.status == 401 and owned:
                try:
                    self.open_url()
                    ready = True  # Harness is healthy and intentionally requires sign-in.
                except ValueError:
                    pass
        except OSError:
            pass
        finally:
            conn.close()
        installed = all(Path(self.config.get(key) or '/not-configured').is_file()
                        for key in ('NodeExecutable', 'HarnessEntry'))
        return {'ready': ready, 'owned': owned, 'installed': installed,
                'url': f'http://127.0.0.1:{self.port}',
                'state': 'ready' if ready else 'starting' if owned else 'offline',
                'detail': 'Linux Harness is ready.' if ready else 'Loading the separate Linux Harness profile…' if owned
                          else 'Stopped. Start Harness when needed.' if installed else 'Install the native Linux Node and Harness runtimes first.'}

    def start(self):
        if self.processes.status('client-harness')['owned']:
            return {'state': 'running', 'detail': 'This Linux Harness is already owned by the launcher.'}
        if not self.status()['installed']:
            raise ValueError('Native Node and Harness are not configured. Run the optional runtime installer first.')
        state = Path(self.config['StateRoot'])
        state.mkdir(parents=True, exist_ok=True)
        state.chmod(0o700)
        if state.stat().st_mode & 0o077:
            raise ValueError('Harness needs a private Linux state folder. Use the default Linux data location or --state on a native Linux filesystem; Windows-mounted folders may not enforce Linux permissions.')
        prepared = core.prepare_clients(self.config)
        log = state/'logs/client-harness.engine.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_bytes(b'')  # Do not reuse an earlier process's sign-in URL.
        log.chmod(0o600)
        node = Path(self.config['NodeExecutable'])
        profile = {'id': 'client-harness', 'workspaceRoot': prepared['WorkspaceRoot'], 'model': {
            'baseUrl': f'http://127.0.0.1:{self.port}/v1',
            'launch': {'executable': str(node), 'cwd': prepared['WorkspaceRoot'], 'args': [
                self.config['HarnessEntry'], 'web', '--no-open', '--host', '127.0.0.1', '--port', str(self.port)]}}}
        return self.processes.start(profile, {'DSH_HOME': prepared['HarnessHome'],
            'AIBRAINWORX_API_KEY': 'local-no-auth',
            'PATH': str(node.parent)+os.pathsep+os.environ.get('PATH', '')})

    def stop(self):
        return self.processes.stop('client-harness')

    def open_url(self):
        if not self.processes.status('client-harness')['owned']:
            raise ValueError('No verified owned Linux Harness is available.')
        log = Path(self.config['StateRoot'])/'logs/client-harness.engine.log'
        try:
            with log.open('rb') as handle:
                handle.seek(max(0, log.stat().st_size-32768))
                text = handle.read(32768).decode('utf-8', errors='replace')
        except OSError:
            raise ValueError('Harness has not announced its local sign-in link yet.') from None
        matches = re.findall(r'(?m)^dsh web: (http://[^\s]{1,2000})\s*$', text)
        if not matches:
            raise ValueError('Harness has not announced its local sign-in link yet.')
        url = matches[-1]
        parsed = urlsplit(url)
        if parsed.hostname != '127.0.0.1' or parsed.port != self.port or parsed.username or parsed.password:
            raise ValueError('Harness announced an unexpected URL; it was not opened.')
        return url
