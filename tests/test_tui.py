from __future__ import annotations

import asyncio

import pytest

from backend.tui.app import AptApp


async def test_tui_renders_core_widgets():
    app = AptApp()
    async with app.run_test() as pilot:
        await asyncio.sleep(1.5)
        from textual.widgets import Input, Static
        from textual.containers import VerticalScroll

        bar = app.query_one("#bar", Static)
        scroll = app.query_one("#scroll", VerticalScroll)
        inp = app.query_one("#inp", Input)
        assert bar is not None
        assert scroll is not None
        assert inp is not None


async def test_tui_loads_models_into_bar(ollama_available):
    if not ollama_available:
        pytest.skip("Ollama not running")
    from backend.provider import default_provider

    installed = {m.name for m in default_provider().list_models()}
    if not installed:
        pytest.skip("no models installed")
    app = AptApp()
    async with app.run_test() as pilot:
        await asyncio.sleep(2.0)
        # Loads whatever is actually installed — not a hard-coded model set.
        assert app.model_count == len(installed)
        assert app.model in installed
        assert app.sid is not None


async def test_tui_help_command_pushes_screen():
    app = AptApp()
    async with app.run_test() as pilot:
        await asyncio.sleep(1.0)
        inp = app.query_one("#inp")
        inp.focus()
        inp.value = "/help"
        await pilot.press("enter")
        await asyncio.sleep(0.4)
        assert any(t.__name__ == "HelpScreen" for t in map(type, app.screen_stack))
        await pilot.press("escape")


async def test_tui_agent_switch():
    app = AptApp()
    async with app.run_test() as pilot:
        await asyncio.sleep(1.0)
        inp = app.query_one("#inp")
        inp.focus()
        inp.value = "/agent plan"
        await pilot.press("enter")
        await asyncio.sleep(0.4)
        assert app.agent_name == "plan"
        assert app.agent is not None
        assert not app.agent.permissions.can_write()


async def test_tui_clear_command():
    app = AptApp()
    async with app.run_test() as pilot:
        await asyncio.sleep(1.0)
        inp = app.query_one("#inp")
        inp.focus()
        inp.value = "/clear"
        await pilot.press("enter")
        await asyncio.sleep(0.3)
        assert app.messages == []
