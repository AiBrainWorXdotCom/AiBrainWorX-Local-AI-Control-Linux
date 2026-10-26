"""Native Linux process ownership. Never kill by port, process name or PID alone."""
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

import control_core as core


def identity(pid):
    try:
        directory = Path('/proc')/str(pid)
        fields = (directory/'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z' or directory.stat().st_uid != os.getuid():
            return None
        executable = (directory/'exe').stat()
        return {'pid': int(pid), 'start': fields[19], 'group': int(fields[2]),
                'session': int(fields[3]), 'command': hashlib.sha256((directory/'cmdline').read_bytes()).hexdigest(),
                'executable': [executable.st_dev, executable.st_ino]}
    except (OSError, ValueError, IndexError):
        return None


def same(record):
    if not record:
        return False
    actual = identity(record['pid'])
    # Node may rewrite argv/process.title after launch. Its executable inode,
    # user, start time, session and group remain stable; another exec is refused.
    fields = ('pid', 'start', 'group', 'session', 'executable')
    return bool(actual and all(key in record and record[key] == actual[key] for key in fields))


class Processes:
    def __init__(self, config):
        self.config = config
        self.path = Path(config['StateRoot'])/'owned-processes.local.json'
        self.children = {}

    def records(self):
        return core.read_json(self.path) if self.path.exists() else {}

    def status(self, profile_id):
        record = self.records().get(profile_id)
        return {'state': 'running' if same(record) else 'stopped', 'owned': bool(same(record))}

    def start(self, profile, environment=None):
        launch = profile['model'].get('launch', {})
        executable = Path(launch.get('executable', ''))
        args = launch.get('args', [])
        cwd = Path(launch.get('cwd') or profile['workspaceRoot'])
        if (not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK)
                or executable.suffix.lower() in ('.exe', '.cmd', '.bat', '.ps1')):
            raise ValueError('Choose an installed native Linux executable with execute permission.')
        if not isinstance(args, list) or len(args) > 100 or any(not isinstance(a, str) or '\0' in a or len(a) > 8192 for a in args):
            raise ValueError('Engine arguments must be a bounded JSON array of strings.')
        if not cwd.is_absolute() or not cwd.is_dir():
            raise ValueError('Choose an existing Linux working directory.')
        # Engine builders bind to loopback; obvious public bind overrides are refused.
        if any(value in args for value in ('0.0.0.0', '::', '--host=0.0.0.0', '--host=::')):
            raise ValueError('Managed model servers must bind to loopback.')
        with core.state_lock(self.config):
            records = self.records()
            if same(records.get(profile['id'])):
                return {'state': 'running', 'detail': 'This model process is already owned by the launcher.'}
            from urllib.parse import urlsplit
            port = urlsplit(profile['model']['baseUrl']).port
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=.2):
                    raise ValueError('The model port is already occupied. No process was started or stopped.')
            except (ConnectionError, TimeoutError):
                pass
            logs = Path(self.config['StateRoot'])/'logs'
            logs.mkdir(parents=True, exist_ok=True)
            log = logs/(profile['id']+'.engine.log')
            if log.exists() and log.stat().st_size > 2*1024*1024:
                log.write_bytes(log.read_bytes()[-256*1024:])
            with log.open('ab') as handle:
                os.chmod(log, 0o600)
                process = subprocess.Popen([str(executable), *args], cwd=cwd, stdin=subprocess.DEVNULL,
                    stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
                    env=None if environment is None else {**os.environ, **environment})
            record = None
            for _ in range(20):
                if process.poll() is not None:
                    raise ValueError('The engine exited during startup. Inspect its local log.')
                record = identity(process.pid)
                if record:
                    break
                time.sleep(.01)
            if not record:
                process.terminate()
                raise ValueError('Could not establish process ownership.')
            records[profile['id']] = record
            core.write_json(self.path, records)
            self.children[profile['id']] = process
        return {'state': 'starting', 'detail': 'Native Linux process started. Waiting for the model API.'}

    def stop(self, profile_id):
        with core.state_lock(self.config):
            records = self.records()
            root = records.get(profile_id)
            if not same(root):
                return {'state': 'stopped', 'detail': 'No verified owned process is running. Other processes were left alone.'}
            captured = []
            for path in Path('/proc').iterdir():
                if path.name.isdigit():
                    item = identity(int(path.name))
                    if item and item['group'] == root['group'] and item['session'] == root['session'] and int(item['start']) >= int(root['start']):
                        captured.append(item)
            if not same(root):
                raise ValueError('Process ownership changed; no signal was sent.')
            os.killpg(root['group'], signal.SIGTERM)
            deadline = time.monotonic()+5
            while time.monotonic() < deadline and any(same(item) for item in captured):
                time.sleep(.05)
            for item in captured:
                if same(item):
                    os.kill(item['pid'], signal.SIGKILL)
            if profile_id in self.children:
                self.children.pop(profile_id).wait(timeout=3)
            survivors = [item for item in captured if same(item)]
            if survivors:
                raise ValueError('Stop incomplete. A captured engine child is still running; inspect Diagnostics.')
            records.pop(profile_id, None)
            core.write_json(self.path, records)
        return {'state': 'stopped', 'detail': 'Owned Linux process group exited; unrelated services were not touched.'}
