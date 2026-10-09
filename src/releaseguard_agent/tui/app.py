import asyncio
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Markdown, Static
from textual.worker import Worker, WorkerState

from releaseguard_agent.config.provider import (
    ProviderConfig,
    ProviderConfigError,
    get_active_provider,
    load_providers,
)
from releaseguard_agent.llm.anthropic_stream_client import AnthropicStreamClient
from releaseguard_agent.llm.client import LLMStreamError, StreamLLMClient
from releaseguard_agent.llm.conversation import ConversationManager
from releaseguard_agent.llm.events import (
    StreamEnd,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
)
from releaseguard_agent.llm.messages import ThinkingBlock
from releaseguard_agent.llm.openai_stream_client import OpenAIStreamClient
from releaseguard_agent.tui.banner import render_banner


class ReleaseGuardApp(App):
    """ReleaseGuard Agent Interactive Terminal UI."""

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    #banner-view {
        padding: 1;
        background: $surface;
        color: $accent;
        border-bottom: solid $primary;
        height: auto;
    }

    #chat-scroll {
        height: 1fr;
        padding: 1;
    }

    .user-msg {
        background: $primary-darken-2;
        color: $text;
        padding: 1;
        margin: 1 0;
        border-left: double $primary;
    }

    .assistant-msg {
        background: $surface-darken-1;
        color: $text;
        padding: 1;
        margin: 1 0;
        border-left: solid $success;
    }

    .thinking-box {
        background: $panel;
        color: $text-muted;
        padding: 1;
        margin: 1 0;
        border-left: dashed $warning;
    }

    .error-box {
        background: $error-darken-2;
        color: $text;
        padding: 1;
        margin: 1 0;
        border-left: solid $error;
    }

    #status-bar {
        background: $boost;
        color: $text-muted;
        padding: 0 1;
        height: 1;
    }

    #input-container {
        height: auto;
        padding: 1;
        background: $surface;
        border-top: solid $primary;
    }

    #user-input {
        width: 1fr;
    }

    #send-btn {
        width: 12;
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel_generation", "Cancel / Esc"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear_chat", "Clear Screen"),
    ]

    def __init__(
        self,
        provider: ProviderConfig | None = None,
        client: StreamLLMClient | None = None,
        tool_registry: Any | None = None,
        mcp_manager: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.provider = provider
        self.client = client
        self.tool_registry = tool_registry
        self.mcp_manager = mcp_manager
        self.conversation = ConversationManager()
        self.current_worker: Worker | None = None
        self._init_error: str | None = None

        if self.client is None:
            self._setup_client()

    def _setup_client(self) -> None:
        try:
            if self.provider is None:
                providers = load_providers()
                if not providers:
                    self._init_error = (
                        "No LLM providers configured in .releaseguard/config.yaml"
                    )
                    return
                self.provider = get_active_provider(providers)

            if self.provider.type == "anthropic":
                self.client = AnthropicStreamClient(self.provider)
            else:
                self.client = OpenAIStreamClient(self.provider)
        except ProviderConfigError as exc:
            self._init_error = str(exc)
        except Exception as exc:
            self._init_error = f"Failed to initialize LLM client: {exc}"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(render_banner(), id="banner-view")
        with VerticalScroll(id="chat-scroll"):
            if self._init_error:
                yield Static(
                    f"⚠️ Configuration Warning: {self._init_error}\n"
                    "You can still inspect the interface, or configure .releaseguard/config.yaml to chat.",
                    classes="error-box",
                )
        yield Static(self._format_status(), id="status-bar")
        with Horizontal(id="input-container"):
            yield Input(
                placeholder="Type message and press Enter (Esc to stop)...",
                id="user-input",
            )
            yield Button("Send", id="send-btn", variant="primary")
        yield Footer()

    def _format_status(self, state: str = "Ready") -> str:
        prov_name = self.provider.name if self.provider else "None"
        model_name = self.provider.model if self.provider else "None"
        tokens = self.conversation.estimate_tokens()
        return f" Provider: {prov_name} | Model: {model_name} | Tokens: ~{tokens} | State: {state}"

    def update_status(self, state: str = "Ready") -> None:
        status_bar = self.query_one("#status-bar", Static)
        status_bar.update(self._format_status(state))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            await self._submit_input()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self._submit_input()

    async def _submit_input(self) -> None:
        input_widget = self.query_one("#user-input", Input)
        text = input_widget.value.strip()
        if not text:
            return

        if self.current_worker and self.current_worker.is_running:
            return

        input_widget.value = ""
        input_widget.disabled = True

        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
        # Render user message
        user_box = Static(f"🧑 User:\n{text}", classes="user-msg")
        await chat_scroll.mount(user_box)
        chat_scroll.scroll_end(animate=False)

        self.conversation.add_user_message(text)
        self.update_status(state="Generating...")

        if not self.client:
            await chat_scroll.mount(
                Static(
                    f"❌ Cannot send message: {self._init_error or 'No active LLM client.'}",
                    classes="error-box",
                )
            )
            input_widget.disabled = False
            input_widget.focus()
            self.update_status(state="Error")
            return

        self.current_worker = self.run_worker(
            self._stream_response(),
            exclusive=True,
            name="llm_stream",
        )

    async def _stream_response(self) -> None:
        input_widget = self.query_one("#user-input", Input)
        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)

        thinking_widget: Static | None = None
        assistant_widget: Markdown | None = None
        accumulated_text = ""
        accumulated_thinking = ""
        thinking_blocks: list[ThinkingBlock] = []

        try:
            assert self.client is not None
            async for event in self.client.stream(self.conversation):
                if isinstance(event, ThinkingDelta):
                    accumulated_thinking += event.thinking
                    if thinking_widget is None:
                        thinking_widget = Static(
                            "🤔 Thinking:\n" + accumulated_thinking,
                            classes="thinking-box",
                        )
                        await chat_scroll.mount(thinking_widget)
                    else:
                        thinking_widget.update(f"🤔 Thinking:\n{accumulated_thinking}")
                    chat_scroll.scroll_end(animate=False)

                elif isinstance(event, ThinkingComplete):
                    if thinking_widget and accumulated_thinking:
                        thinking_blocks.append(
                            ThinkingBlock(thinking=accumulated_thinking)
                        )
                        thinking_widget.update(
                            f"💡 Thought Process Completed ({len(accumulated_thinking)} chars)"
                        )

                elif isinstance(event, TextDelta):
                    accumulated_text += event.text
                    if assistant_widget is None:
                        assistant_widget = Markdown(
                            accumulated_text, classes="assistant-msg"
                        )
                        await chat_scroll.mount(assistant_widget)
                    else:
                        await assistant_widget.update(accumulated_text)
                    chat_scroll.scroll_end(animate=False)

                elif isinstance(event, StreamEnd):
                    break

            # Append completed message to conversation
            if accumulated_text or thinking_blocks:
                self.conversation.add_assistant_message(
                    text=accumulated_text,
                    thinking=thinking_blocks if thinking_blocks else None,
                )

            self.update_status(state="Ready")

        except asyncio.CancelledError:
            self.update_status(state="Cancelled")
            await chat_scroll.mount(
                Static("⏹️ Generation cancelled by user.", classes="error-box")
            )
        except LLMStreamError as exc:
            self.update_status(state="Stream Error")
            await chat_scroll.mount(
                Static(f"❌ LLM Stream Error: {exc}", classes="error-box")
            )
        except Exception as exc:
            self.update_status(state="Unexpected Error")
            await chat_scroll.mount(Static(f"❌ Error: {exc}", classes="error-box"))
        finally:
            input_widget.disabled = False
            input_widget.focus()

    def action_cancel_generation(self) -> None:
        """Cancel current in-flight LLM generation cleanly."""
        if self.current_worker and self.current_worker.is_running:
            self.current_worker.cancel()

    def action_clear_chat(self) -> None:
        """Clear visible chat history."""
        chat_scroll = self.query_one("#chat-scroll", VerticalScroll)
        chat_scroll.remove_children()
        self.conversation.clear()
        self.update_status(state="Cleared")

    async def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.state in (
            WorkerState.CANCELLED,
            WorkerState.ERROR,
            WorkerState.SUCCESS,
        ):
            input_widget = self.query_one("#user-input", Input)
            input_widget.disabled = False


if __name__ == "__main__":
    app = ReleaseGuardApp()
    app.run()
