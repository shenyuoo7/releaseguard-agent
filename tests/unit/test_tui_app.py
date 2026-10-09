import pytest
from textual.widgets import Input

from releaseguard_agent.llm.fake_stream_client import FakeStreamClient
from releaseguard_agent.tui.app import ReleaseGuardApp
from releaseguard_agent.tui.banner import render_banner


def test_render_banner() -> None:
    banner_str = render_banner(version="1.0.0", cwd="E:/test_project")
    assert "ReleaseGuard Agent v1.0.0" in banner_str
    assert "E:/test_project" in banner_str
    assert "RELEASEGUARD" in banner_str.upper()


@pytest.mark.anyio
async def test_tui_app_mount_and_headless_composition() -> None:
    client = FakeStreamClient(text_chunks=["Answer text"])
    app = ReleaseGuardApp(client=client)

    async with app.run_test() as pilot:
        # Check elements mounted
        assert pilot.app.query_one("#banner-view") is not None
        assert pilot.app.query_one("#status-bar") is not None
        assert pilot.app.query_one("#user-input") is not None
        assert pilot.app.query_one("#send-btn") is not None


@pytest.mark.anyio
async def test_tui_app_send_message_and_stream() -> None:
    fake_client = FakeStreamClient(
        thinking_chunks=["Deep reasoning about quicksort"],
        text_chunks=["Here is the quicksort code:"],
    )
    app = ReleaseGuardApp(client=fake_client)

    async with app.run_test() as pilot:
        input_widget = pilot.app.query_one("#user-input", Input)
        input_widget.value = "Write quicksort"
        await pilot.click("#send-btn")

        if pilot.app.current_worker:
            await pilot.app.current_worker.wait()
        await pilot.pause(0.1)

        # Check messages in conversation
        messages = app.conversation.get_messages()
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "Write quicksort"
        assert messages[1].role == "assistant"
        assert "quicksort" in messages[1].content
        assert len(messages[1].thinking_blocks) == 1
        assert (
            messages[1].thinking_blocks[0].thinking == "Deep reasoning about quicksort"
        )


@pytest.mark.anyio
async def test_tui_app_clear_action() -> None:
    app = ReleaseGuardApp(client=FakeStreamClient(text_chunks=["Hi"]))
    async with app.run_test():
        app.conversation.add_user_message("Old question")
        assert len(app.conversation.get_messages()) == 1

        app.action_clear_chat()
        assert len(app.conversation.get_messages()) == 0


@pytest.mark.anyio
async def test_tui_app_cancel_action() -> None:
    fake_client = FakeStreamClient(
        text_chunks=["Chunk 1", "Chunk 2", "Chunk 3"],
        delay_s=0.5,
    )
    app = ReleaseGuardApp(client=fake_client)
    async with app.run_test() as pilot:
        input_widget = pilot.app.query_one("#user-input", Input)
        input_widget.value = "Slow stream"
        await pilot.click("#send-btn")
        await pilot.pause(0.1)

        pilot.app.action_cancel_generation()
        await pilot.pause(0.2)

        assert pilot.app.current_worker is not None
        assert (
            pilot.app.current_worker.is_cancelled
            or not pilot.app.current_worker.is_running
        )
