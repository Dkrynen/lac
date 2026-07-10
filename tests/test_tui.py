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


async def test_permission_modal_mounts_and_hides_always_for_doom_loop():
    from textual.app import App
    from textual.widgets import Static

    from backend.tui.permission_modal import PermissionModal

    class ModalHost(App):
        def compose(self):
            yield Static("host")

    app = ModalHost()
    async with app.run_test(size=(80, 24)) as pilot:
        target = "echo [conceal]rm -rf /[/conceal] " + ("x" * 1000)
        modal = PermissionModal(
            "build",
            "run_bash",
            target,
            allow_always=False,
            notice="Repeated identical tool call detected; permanent approval is disabled.",
        )
        app.push_screen(modal)
        await pilot.pause()
        assert len(modal.query(".perm-body")) == 2
        assert len(modal.query("#btn-always")) == 0
        assert "permanent approval is disabled" in modal.query_one("#perm-notice", Static).render().plain
        rendered_target = modal.query_one("#perm-target", Static).render()
        assert target in rendered_target.plain
        dialog = modal.query_one("#perm-dialog")
        buttons = modal.query_one("#perm-buttons")
        assert dialog.region.x > 0 and dialog.region.y > 0
        assert buttons.region.bottom <= app.size.height


async def test_permission_ask_hides_always_when_target_is_missing(monkeypatch):
    from backend.permission import Decision

    captured = []
    app = AptApp()
    async with app.run_test() as pilot:
        async def fake_push_screen(screen, *, wait_for_dismiss=False):
            captured.append(screen)
            return Decision.DENY

        monkeypatch.setattr(app, "push_screen", fake_push_screen)
        result = await app._permission_ask("build", "list_files", None, "list")
        await pilot.pause()

    assert result.decision is Decision.DENY
    assert len(captured) == 1
    modal = captured[0]
    assert modal.allow_always is False
    assert modal.notice is not None
    assert "no target" in modal.notice.lower()


@pytest.mark.parametrize("size", [(80, 24), (60, 15), (40, 10)])
@pytest.mark.parametrize(
    ("allow_always", "notice"),
    [
        (True, None),
        (False, "Loop detected; Always is disabled."),
    ],
)
async def test_permission_modal_fits_supported_viewports_with_full_scrollable_target(
    size, allow_always, notice
):
    from textual.app import App
    from textual.containers import VerticalScroll
    from textual.widgets import Static

    from backend.tui.permission_modal import PermissionModal

    class ModalHost(App):
        def compose(self):
            yield Static("host")

    app = ModalHost()
    async with app.run_test(size=size) as pilot:
        target = "echo [conceal]rm -rf /[/conceal] " + ("x" * 1000)
        modal = PermissionModal(
            "build",
            "run_bash",
            target,
            allow_always=allow_always,
            notice=notice,
        )
        app.push_screen(modal)
        await pilot.pause()

        dialog = modal.query_one("#perm-dialog")
        buttons = modal.query_one("#perm-buttons")
        target_scroll = modal.query_one("#perm-target-scroll", VerticalScroll)
        rendered_target = modal.query_one("#perm-target", Static).render()

        assert dialog.region.x >= 0
        assert dialog.region.y >= 0
        assert dialog.region.right <= app.size.width
        assert dialog.region.bottom <= app.size.height
        assert buttons.region.right <= app.size.width
        assert buttons.region.bottom <= app.size.height
        assert target_scroll.region.bottom <= app.size.height
        assert target_scroll.max_scroll_y > 0
        assert target in rendered_target.plain
        assert not rendered_target.spans
        assert len(modal.query("#btn-always")) == int(allow_always)
