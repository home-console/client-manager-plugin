"""Proxy package to expose existing `app` package as `client_manager.app`.
This avoids changing existing imports in tests and keeps local layout.
"""
from importlib import import_module as _import_module
import os

# Locate the real `app` package and set this package's __path__ to point
# there so submodule imports like `client_manager.app.main` resolve correctly.
_real_app = _import_module('app')
try:
    __path__ = list(_real_app.__path__)
except Exception:
    # fallback: locate relative path to 'app' directory in the repo
    base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    __path__ = [base]

# re-export common attributes from real `app`
for _name in dir(_real_app):
    if not _name.startswith('_'):
        globals()[_name] = getattr(_real_app, _name)

__all__ = [name for name in globals() if not name.startswith('_')]
