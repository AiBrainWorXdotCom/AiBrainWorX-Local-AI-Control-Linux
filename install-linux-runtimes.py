"""Explicit Linux runtime installation from official sources. Never downloads weights."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'app'))
from linux_paths import state_root, runtime_root
RUNTIME = runtime_root()
STATE_ROOT = state_root()
STRATA_RELEASE = '0.2.6'
HARNESS_VERSION = '0.2.1-rc.2'


def fetch(url, maximum=2*1024*1024):
    req = urllib.request.Request(url, headers={'User-Agent': 'AiBrainWorX-Linux-Setup', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read(maximum+1)
    if len(data) > maximum:
        raise ValueError('Unexpectedly large setup metadata.')
    return data


def download(url, path, expected, maximum):
    if path.exists():
        with path.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() == expected:
                return
    cached = ROOT/'.runtime/downloads'/path.name
    if cached != path and cached.exists():
        with cached.open('rb') as handle:
            if hashlib.file_digest(handle, 'sha256').hexdigest() == expected:
                shutil.copyfile(cached, path)
                return
    temporary = path.with_suffix(path.suffix+'.partial')
    digest, size = hashlib.sha256(), 0
    print('Downloading runtime archive (not model weights): '+path.name, flush=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'AiBrainWorX-Linux-Setup'})
    try:
        with urllib.request.urlopen(req, timeout=60) as response, temporary.open('wb') as output:
            while block := response.read(1024*1024):
                size += len(block)
                if size > maximum:
                    raise ValueError('Runtime archive exceeded its declared bound.')
                digest.update(block); output.write(block)
                if size % (64*1024*1024) == 0:
                    print(str(size//(1024*1024))+' MiB received', flush=True)
        if digest.hexdigest() != expected:
            raise ValueError('Runtime checksum mismatch. Nothing was extracted.')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def unpack(archive, target):
    staging = target.with_name(target.name+'.extracting')
    if staging.exists():
        raise ValueError('An earlier extraction requires inspection.')
    staging.mkdir()
    with tarfile.open(archive) as tar:
        # Python's data filter rejects absolute/escaping paths and unsafe links.
        tar.extractall(staging, filter='data')
    children = list(staging.iterdir())
    if len(children) == 1 and children[0].is_dir():
        children[0].rename(target)
        staging.rmdir()
    else:
        staging.rename(target)


def run(argv, **kwargs):
    subprocess.run([str(v) for v in argv], check=True, **kwargs)


def posix_path(value):
    if isinstance(value, str) and re.match(r'^[A-Za-z]:[\\/]', value):
        return '/mnt/'+value[0].lower()+value[2:].replace('\\', '/')
    return value


def map_engine_args(arguments):
    if not isinstance(arguments, list) or any(not isinstance(value, str) for value in arguments):
        raise ValueError('Strata engine arguments must be a string array.')
    # --resident-experts is upstream's LOW-RAM mode, not "load everything".
    # Removing it selects the much larger default expert arena on Linux.
    # Preserve the owner's memory flags; only translate filesystem paths.
    return [posix_path(value) for value in arguments]


def install(reuse_config=None):
    if sys.platform != 'linux':
        raise ValueError('Run this installer inside Linux/WSL.')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    downloads = RUNTIME/'downloads'; downloads.mkdir(exist_ok=True)
    checksums = fetch('https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt').decode()
    match = re.search(r'^([0-9a-f]{64})\s+(node-v24\.[0-9]+\.[0-9]+-linux-x64.tar.xz)$', checksums, re.M)
    if not match:
        raise ValueError('Official Node 24 Linux checksum entry was not found.')
    node_home = RUNTIME/'node'
    if not node_home.exists():
        archive = downloads/match[2]
        download('https://nodejs.org/dist/latest-v24.x/'+match[2], archive, match[1], 100*1024*1024)
        unpack(archive, node_home)
    env = {**os.environ, 'PATH': str(node_home/'bin')+os.pathsep+os.environ.get('PATH', '')}
    harness_home = RUNTIME/'harness'
    harness_entry = harness_home/'node_modules/@deepseek-ai/dsh/lib/bin.js'
    if not harness_entry.exists():
        run([node_home/'bin/npm', 'install', '--prefix', harness_home, '--ignore-scripts',
             '--no-audit', '--no-fund', '--save-exact', '@deepseek-ai/dsh@'+HARNESS_VERSION], env=env)
    source = RUNTIME/'strata-source'
    if not source.exists():
        run(['git', 'clone', '--depth', '1', '--branch', 'v'+STRATA_RELEASE,
             'https://github.com/Niko1221/Strata.git', source])
    python = source/'.venv/bin/python'
    pip_ready = python.exists() and subprocess.run([str(python), '-m', 'pip', '--version'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not pip_ready:
        run([sys.executable, '-m', 'venv', source/'.venv'])
    run([python, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-input', '-r', source/'requirements.txt'])
    feed = json.loads(fetch('https://github.com/Niko1221/Strata/releases/download/v'+STRATA_RELEASE+'/strata-feed.json'))
    packages = feed['engine']['packages']
    package = next(p for p in packages if p['platform'] == 'linux-x86_64' and p['backend'] == 'cuda' and p['variant'] == 'cuda13')
    engine_home = RUNTIME/'strata-engine'
    if not engine_home.exists():
        name = package['url']
        if '/' in name or not name.endswith('.tar.gz'):
            raise ValueError('Unexpected engine archive name.')
        archive = downloads/name
        download('https://github.com/Niko1221/Strata/releases/download/v'+STRATA_RELEASE+'/'+name,
                 archive, package['sha256'], package['size']+1024*1024)
        unpack(archive, engine_home)
    candidates = [p for p in engine_home.rglob('strata') if p.is_file()]
    if len(candidates) != 1:
        raise ValueError('Could not identify the native Strata executable.')
    engine = candidates[0]
    engine.chmod(engine.stat().st_mode | 0o100)
    libdirs = sorted({str(p.parent) for p in engine_home.rglob('libcublas.so*')})
    state_dir = STATE_ROOT; state_dir.mkdir(parents=True, exist_ok=True); state_dir.chmod(0o700)
    config_path = state_dir/'linux-config.local.json'
    config = json.loads(config_path.read_text()) if config_path.exists() else {
        'StateRoot': str(state_dir), 'WorkspaceRoot': str(ROOT), 'PythonExecutable': sys.executable,
        'ModelBackend': 'compatible', 'ModelBaseUrl': 'http://127.0.0.1:8080/v1', 'ModelId': 'select-a-local-model'}
    config.update(NodeExecutable=str(node_home/'bin/node'), HarnessEntry=str(harness_entry),
                  StrataRoot=str(source), StrataPython=str(python), GatewayPort=8766, HarnessPort=3081)
    mapped = None
    if reuse_config:
        original = json.loads(Path(reuse_config).read_text(encoding='utf-8-sig'))
        mapped = {key: value for key, value in original.items() if key not in ('exe', 'args', 'cwd', 'lib_dirs', 'log')}
        # Keep prepared lookup/weight data on its existing volume and retain
        # upstream's low-RAM/mmap choices rather than changing allocation modes.
        mapped['args'] = map_engine_args(original['args'])
        mapped.update(exe=str(engine), cwd=str(source), tokenizer=posix_path(original['tokenizer']),
                      lib_dirs=libdirs, log=str(state_dir/'strata-engine.log'),
                      open_browser=False, host='127.0.0.1', port=8081)
        target = state_dir/('strata-coder-linux-'+hashlib.sha256(str(RUNTIME).encode()).hexdigest()[:10]+'.local.json')
        if target.exists():
            mapped = json.loads(target.read_text())  # Keep local edits on repeat setup.
        else:
            target.write_text(json.dumps(mapped, indent=2))
        config['StrataConfig'] = str(target)
    config_path.write_text(json.dumps(config, indent=2))
    # Validate runnable clients, never start a model or a Harness web session here.
    run([node_home/'bin/node', '--version'])
    run([node_home/'bin/node', harness_entry, '--version'], env=env)
    run([python, source/'serve/server.py', '--help'], env={**env, 'PYTHONPATH': str(source)+os.pathsep+str(source/'tools')}, stdout=subprocess.DEVNULL)
    print(json.dumps({'installed': ['Node 24', 'DeepSeek Harness '+HARNESS_VERSION, 'Strata '+STRATA_RELEASE,
                                  'NVIDIA Linux engine '+package['version']],
                      'model_weights_downloaded': False, 'model_started': False,
                      'existing_model_config_mapped': mapped is not None}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Explicitly download/install private runtimes from official sources')
    parser.add_argument('--reuse-strata-config', help='Read an existing Strata config and map its existing weight paths to WSL')
    parser.add_argument('--runtime-root', type=Path, help='Optional native Linux runtime folder; recommended over /mnt for WSL Node startup')
    parser.add_argument('--state', type=Path, help='Override the private per-user Linux state directory')
    args = parser.parse_args()
    if args.runtime_root:
        RUNTIME = args.runtime_root.expanduser().resolve()
        RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    if args.state:
        STATE_ROOT = args.state.expanduser().resolve()
    if args.install:
        install(args.reuse_strata_config)
    else:
        print('Plan only: Node 24 + pinned Harness + Strata '+STRATA_RELEASE+' + Linux CUDA13 engine, inside the private Linux runtime cache.')
        print('Use --install explicitly. No model weights will be downloaded; no model is started.')
