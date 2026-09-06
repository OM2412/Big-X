import os
import sys
from pathlib import Path
import types

_repo_root = Path(__file__).resolve().parent

for svc_dir in os.listdir(_repo_root):
    if "-" in svc_dir:
        mod_name = svc_dir.replace("-", "_")
        pkg_name = f"services.{mod_name}"
        if pkg_name not in sys.modules:
            pkg = types.ModuleType(pkg_name)
            pkg.__path__ = [str(_repo_root / svc_dir)]
            pkg.__package__ = pkg_name
            sys.modules[pkg_name] = pkg
            globals()[mod_name] = pkg
