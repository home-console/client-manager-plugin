from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sdk.testing import PluginTestRuntime


def _import_impl():
    plugin_dir = Path(__file__).resolve().parents[1]
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
    import plugin as plugin_mod  # type: ignore

    return plugin_mod


def test_metadata_smoke() -> None:
    ClientManagerPlugin = _import_impl().ClientManagerPlugin

    plugin = ClientManagerPlugin(PluginTestRuntime())
    md = plugin.metadata
    assert md.name == "client_manager"
    assert "client.command.execute" in md.capabilities_provided


@pytest.mark.asyncio
async def test_on_load_registers_services_http_and_operations() -> None:
    ClientManagerPlugin = _import_impl().ClientManagerPlugin

    runtime = PluginTestRuntime()
    plugin = ClientManagerPlugin(runtime)
    await plugin.on_load()

    # A few representative service names
    assert "client_manager.list_clients" in runtime.registered_services
    assert "client_manager.websocket" in runtime.registered_services

    # HTTP endpoints registered via BasePlugin.register_http_endpoint helper
    assert len(runtime.registered_http_endpoints) > 0

    # Operation handlers registered via BasePlugin.register_operation_handler helper (best-effort)
    assert "client.command.execute" in runtime.registered_operation_handlers

