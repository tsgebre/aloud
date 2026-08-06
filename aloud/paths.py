"""Where voice models live. Stdlib only.

Resolution order:
1. $ALOUD_MODELS_DIR, if set;
2. the repo checkout's models/ directory, when running from source;
3. ~/.local/share/aloud/models (respecting $XDG_DATA_HOME) - the case for
   a pip/pipx-installed Aloud, where site-packages is no place for 60 MB
   voice files.
"""

import os
import pathlib

_REPO_MODELS_DIR = pathlib.Path(__file__).resolve().parent.parent / "models"


def models_dir() -> pathlib.Path:
    env = os.environ.get("ALOUD_MODELS_DIR")
    if env:
        return pathlib.Path(env)
    if _REPO_MODELS_DIR.is_dir():
        return _REPO_MODELS_DIR
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return pathlib.Path(data_home) / "aloud" / "models"
