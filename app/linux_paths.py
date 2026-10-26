"""Linux per-user data paths. No machine-specific paths in distributed config."""
import os
from pathlib import Path


def state_root():
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')/'aibrainworx-local-ai-control'


def runtime_root():
    return Path(os.environ.get('XDG_CACHE_HOME') or Path.home()/'.cache')/'aibrainworx/linux-runtimes'
