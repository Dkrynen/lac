from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from backend.permission import Decision


class PermissionModal(ModalScreen[Decision]):
    CSS = """
    #perm-dialog { width: 54; padding: 1 2; background: $surface; border: solid $primary; }
    #perm-title { margin-bottom: 1; }
    #perm-target { margin-bottom: 1; color: $warning; }
    #perm-body { margin-bottom: 1; color: $text-muted; }
    #perm-buttons { width: 1fr; align: right bottom; }
    Horizontal { height: auto; margin-top: 1; }
    """

    def __init__(self, agent_name: str, tool_name: str, target: str | None):
        super().__init__()
        self.agent_name = agent_name
        self.tool_name = tool_name
        self.target = target

    def compose(self):
        with Vertical(id="perm-dialog"):
            yield Static("[bold]Permission Request[/bold]", id="perm-title")
            yield Static(f"Agent: {self.agent_name}", id="perm-body")
            yield Static(f"Tool: {self.tool_name}", id="perm-body")
            if self.target:
                yield Static(f"Target: {self.target}", id="perm-target")
            with Horizontal(id="perm-buttons"):
                yield Button("Deny", variant="error", id="btn-deny")
                yield Button("Allow Once", variant="primary", id="btn-once")
                yield Button("Always Allow", variant="success", id="btn-always")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-deny":
            self.dismiss(Decision.DENY)
        elif event.button.id == "btn-once":
            self.dismiss(Decision.ALLOW)
        elif event.button.id == "btn-always":
            self.dismiss(("allow_always", Decision.ALLOW))
