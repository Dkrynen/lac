from __future__ import annotations

from pathlib import Path

from backend.plugin import PluginHostImpl, load_plugins
from backend.plugin.base import PluginManifest
from backend.plugin.manager import PluginManager


def test_discover_finds_builtin_tools():
    mgr = PluginManager(host=PluginHostImpl(), start_dir=Path.cwd())
    manifests = mgr.discover()
    names = [m.name for m in manifests]
    assert "tools" in names


def test_load_all_registers_tools():
    host = PluginHostImpl()
    mgr = load_plugins(host, start_dir=Path.cwd())
    assert "tools" in mgr.names()
    assert mgr.errors() == []
    assert "read_file" in host.tools
    assert "list_files" in host.tools
    assert "write_file" in host.tools


def test_builtin_tool_read_file_executes():
    host = PluginHostImpl()
    load_plugins(host, start_dir=Path.cwd())
    handler = host.tools["read_file"]["handler"]
    result = handler({"path": "backend/version.py"}, {"cwd": "."})
    assert "2.2.0" in result


def test_list_files_tool():
    host = PluginHostImpl()
    load_plugins(host, start_dir=Path.cwd())
    handler = host.tools["list_files"]["handler"]
    result = handler({"path": "backend"}, {"cwd": "."})
    assert "api.py" in result
    assert "cookbook" in result


def test_plugin_loads_without_host():
    mgr = load_plugins(None, start_dir=Path.cwd())
    assert "tools" in mgr.names()


def test_file_plugin_discovery(tmp_path):
    base = tmp_path / ".apt" / "plugin"
    base.mkdir(parents=True)
    (base / "echo.py").write_text(
        "def setup(host):\n"
        "    if host is not None:\n"
        "        host.register_tool('echo','echo back',{'type':'object'}, lambda a,c: a.get('msg',''))\n"
    )
    mgr = PluginManager(host=PluginHostImpl(), start_dir=tmp_path)
    mgr.discover()
    mgr.load_all()
    assert "echo" in mgr.names()
    assert mgr.errors() == []
