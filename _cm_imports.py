from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def _load_package(package_name: str, package_dir: Path) -> ModuleType:
    """
    Load a package from an explicit directory WITHOUT sys.path mutation.

    The loaded package becomes importable as `package_name.*` via sys.modules.
    """
    init_py = package_dir / "__init__.py"
    if not init_py.exists():
        raise FileNotFoundError(f"Missing package __init__.py: {init_py}")

    existing = sys.modules.get(package_name)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(
        package_name,
        str(init_py),
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to create spec for package {package_name} from {init_py}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    spec.loader.exec_module(module)
    return module


def _load_module(module_name: str, module_path: Path) -> ModuleType:
    """Load a single module from a file path WITHOUT sys.path mutation."""
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    spec = importlib.util.spec_from_file_location(module_name, str(module_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to create spec for module {module_name} from {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def ensure_cm_app_loaded() -> ModuleType:
    """
    Ensure the plugin-local `app/` folder is loaded under a unique namespace.

    After calling this, imports like `client_manager_plugin_app.config` work,
    and cannot collide with the repository root `app/`.
    """
    plugindir = Path(__file__).resolve().parent
    app_dir = plugindir / "app"
    return _load_package("client_manager_plugin_app", app_dir)


def import_plugin_services() -> ModuleType:
    """Import `plugin_services.py` from this plugin directory deterministically."""
    plugindir = Path(__file__).resolve().parent
    return _load_module("client_manager_plugin_internal.plugin_services", plugindir / "plugin_services.py")


def import_plugin_events() -> ModuleType:
    """Import `plugin_events.py` from this plugin directory deterministically."""
    plugindir = Path(__file__).resolve().parent
    return _load_module("client_manager_plugin_internal.plugin_events", plugindir / "plugin_events.py")

