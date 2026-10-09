import pytest

from releaseguard_agent.commands.models import Command, CommandContext
from releaseguard_agent.commands.registry import CommandRegistry


def dummy_handler(ctx: CommandContext) -> None:
    pass


def test_command_registration_and_finding() -> None:
    """T2 & AC2: Register command and lookup by primary name or alias."""
    reg = CommandRegistry()
    cmd = Command(
        name="status",
        description="Check status",
        aliases=("s", "stat"),
        handler=dummy_handler,
    )
    reg.register(cmd)

    # Lookup by primary name
    found = reg.find("status")
    assert found is not None
    assert found.name == "status"

    # Lookup with leading slash and uppercase
    assert reg.find("/STATUS") == cmd
    assert reg.find("/s") == cmd
    assert reg.find("stat") == cmd

    # Non-existent command
    assert reg.find("unknown") is None


def test_command_name_collision_raises_error() -> None:
    """AC2: Registering a command with duplicate primary name raises ValueError."""
    reg = CommandRegistry()
    cmd1 = Command(name="test", description="First test", handler=dummy_handler)
    cmd2 = Command(name="test", description="Second test", handler=dummy_handler)

    reg.register(cmd1)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(cmd2)


def test_alias_collision_raises_error() -> None:
    """AC2: Registering an alias conflicting with existing name or alias raises ValueError."""
    reg = CommandRegistry()
    cmd1 = Command(
        name="plan", description="Plan", aliases=("p",), handler=dummy_handler
    )
    cmd2 = Command(
        name="preview", description="Preview", aliases=("p",), handler=dummy_handler
    )

    reg.register(cmd1)
    with pytest.raises(ValueError, match="Alias collision"):
        reg.register(cmd2)

    # Collision with primary name
    cmd3 = Command(
        name="other", description="Other", aliases=("plan",), handler=dummy_handler
    )
    with pytest.raises(ValueError, match="Alias collision"):
        reg.register(cmd3)


def test_command_unregistration() -> None:
    """Check removing command and clearing aliases."""
    reg = CommandRegistry()
    cmd = Command(
        name="compact", description="Compact", aliases=("c",), handler=dummy_handler
    )
    reg.register(cmd)

    assert reg.find("c") is not None
    assert reg.unregister("compact") is True

    assert reg.find("compact") is None
    assert reg.find("c") is None
    assert reg.unregister("compact") is False


def test_command_autocomplete() -> None:
    """T2 & AC3: Tab autocomplete returns sorted matching slash commands."""
    reg = CommandRegistry()
    reg.register(
        Command(
            name="status", description="Status", aliases=("s",), handler=dummy_handler
        )
    )
    reg.register(Command(name="session", description="Session", handler=dummy_handler))
    reg.register(
        Command(
            name="compact", description="Compact", aliases=("c",), handler=dummy_handler
        )
    )
    reg.register(
        Command(
            name="hidden_cmd", description="Hidden", hidden=True, handler=dummy_handler
        )
    )

    # All public commands
    all_cmds = reg.complete("/")
    assert "/status" in all_cmds
    assert "/s" in all_cmds
    assert "/session" in all_cmds
    assert "/compact" in all_cmds
    assert "/hidden_cmd" not in all_cmds

    # Prefix match
    matches = reg.complete("/se")
    assert matches == ["/session"]

    matches_s = reg.complete("s")
    assert "/s" in matches_s
    assert "/session" in matches_s
    assert "/status" in matches_s
