from __future__ import annotations

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from backend.permission import Decision


PermissionModalResult = Decision | tuple[str, Decision]


class PermissionModal(ModalScreen[PermissionModalResult]):
    CSS = """
    PermissionModal { align: center middle; }
    #perm-dialog { width: 100%; max-width: 54; height: 100%; max-height: 21; padding: 0 1; background: $surface; border: solid $primary; }
    #perm-title { height: 1; }
    #perm-notice { height: 1; color: $warning; }
    .perm-body { height: 1; color: $text-muted; }
    #perm-target-scroll { height: 1fr; min-height: 1; max-height: 6; }
    #perm-target { width: 1fr; height: auto; color: $warning; }
    #perm-buttons { width: 1fr; height: 3; align: right bottom; }
    #perm-buttons Button { width: 1fr; min-width: 0; }
    Horizontal { height: 3; }
    """

    def __init__(
        self,
        agent_name: str,
        tool_name: str,
        target: str | None,
        *,
        allow_always: bool = True,
        notice: str | None = None,
    ):
        super().__init__()
        self.agent_name = agent_name
        self.tool_name = tool_name
        self.target = target
        self.allow_always = allow_always
        self.notice = notice

    def compose(self):
        with Vertical(id="perm-dialog"):
            yield Static("[bold]Permission Request[/bold]", id="perm-title")
            if self.notice:
                yield Static(self.notice, id="perm-notice", markup=False)
            yield Static(f"Agent: {self.agent_name}", classes="perm-body", markup=False)
            yield Static(f"Tool: {self.tool_name}", classes="perm-body", markup=False)
            if self.target:
                with VerticalScroll(id="perm-target-scroll"):
                    yield Static(f"Target: {self.target}", id="perm-target", markup=False)
            with Horizontal(id="perm-buttons"):
                yield Button("Deny", variant="error", id="btn-deny")
                yield Button("Allow Once", variant="primary", id="btn-once")
                if self.allow_always:
                    yield Button("Always", variant="success", id="btn-always")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-deny":
            self.dismiss(Decision.DENY)
        elif event.button.id == "btn-once":
            self.dismiss(Decision.ALLOW)
        elif event.button.id == "btn-always" and self.allow_always:
            self.dismiss(("allow_always", Decision.ALLOW))
