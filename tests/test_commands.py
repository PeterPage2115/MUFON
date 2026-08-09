"""Tests for the /raport, /wioski and /regiony slash commands: admin check,
flow, registration.

No real Discord is touched — ``register_commands`` gets a fake tree plus
injected fakes for ``run_report``, the section runners and the config
getter, and each command's callback is invoked directly with a
``FakeInteraction`` whose user is a ``Mock(spec=discord.Member)`` (spec'd
mocks pass ``isinstance`` — the admin check narrows on ``discord.Member``).
async tests run via ``asyncio.run`` (pytest-asyncio is not a dependency),
matching test_bot_main.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import Mock

import discord
from discord import app_commands

from travian.bot import commands
from travian.bot.main import MergedConfig
from travian.strings import (
    COMMAND_NO_PERMISSION,
    COMMAND_RAPORT_DESCRIPTION,
    COMMAND_REGIONS_DESCRIPTION,
    COMMAND_WIOSKI_DESCRIPTION,
    RAPORT_ERROR,
    RAPORT_NO_PERMISSION,
    RAPORT_SENT,
    REGIONS_NO_DATA,
    WIOSKI_NO_EVENTS,
)

CHANNEL_ID = 111111111111111111
ADMIN_ROLE_ID = 42


# --- fakes -------------------------------------------------------------------


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


def _member(*, manage_guild: bool = False, roles: list[FakeRole] | None = None) -> Mock:
    """A spec'd ``discord.Member`` fake — passes ``isinstance(Member)``."""
    user = Mock(spec=discord.Member)
    user.guild_permissions = discord.Permissions(manage_guild=manage_guild)
    user.roles = list(roles or [])
    return user


class FakeResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.sent: list[tuple[str | None, bool]] = []

    async def defer(self) -> None:
        self.deferred = True

    async def send_message(self, content: str | None = None, *, ephemeral: bool = False, **_: object) -> None:
        self.sent.append((content, ephemeral))


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[str | None, bool]] = []
        self.sent_embeds: list[list[discord.Embed]] = []

    async def send(
        self,
        content: str | None = None,
        *,
        ephemeral: bool = False,
        embeds: list[discord.Embed] | None = None,
        **_: object,
    ) -> None:
        self.sent.append((content, ephemeral))
        if embeds is not None:
            self.sent_embeds.append(embeds)


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class FakeInteraction:
    def __init__(self, *, guild: object | None, user: Mock, channel: FakeChannel | None = None) -> None:
        self.guild = guild
        self.user = user
        self.channel = channel
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class FakeRunReport:
    """Records calls; optionally raises or observes the interaction's defer state."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []
        self.deferred_when_called: bool | None = None
        self.deferred_source: FakeResponse | None = None
        self.error: Exception | None = None

    async def __call__(self, channel_id: int, require_today: bool = True) -> None:
        if self.error is not None:
            raise self.error
        if self.deferred_source is not None:
            self.deferred_when_called = self.deferred_source.deferred
        self.calls.append((channel_id, require_today))


class FakeSectionRunner:
    """The /wioski + /regiony runner fake: returns configurable embeds (default [])."""

    def __init__(self, embeds: list[discord.Embed] | None = None) -> None:
        self.calls: list[int] = []
        self.embeds: list[discord.Embed] = embeds or []

    async def __call__(self) -> list[discord.Embed]:
        self.calls.append(len(self.embeds))
        return self.embeds


class FakeTree:
    def __init__(self) -> None:
        self.commands: list[app_commands.Command[Any, ..., Any]] = []

    def add_command(self, command: app_commands.Command[Any, ..., Any], /, **_: object) -> None:
        self.commands.append(command)


def _config(*, admin_role_id: int | None = None) -> MergedConfig:
    return MergedConfig(admin_role_id=admin_role_id)


def _registered(
    run_report: FakeRunReport,
    get_config: Callable[[], MergedConfig],
    *,
    run_villages: FakeSectionRunner | None = None,
    run_regions: FakeSectionRunner | None = None,
) -> tuple[FakeTree, app_commands.Command[Any, ..., Any]]:
    tree = FakeTree()
    commands.register_commands(
        cast(app_commands.CommandTree[Any], tree),
        run_report,
        get_config,
        run_villages or FakeSectionRunner(),
        run_regions or FakeSectionRunner(),
    )
    assert len(tree.commands) == 3
    return tree, tree.commands[0]  # registration order: raport, wioski, regiony


def _command_by_name(
    tree: FakeTree, name: str
) -> app_commands.Command[Any, ..., Any]:
    return next(c for c in tree.commands if c.name == name)


def _is_admin(interaction: FakeInteraction, admin_role_id: int | None) -> bool:
    return commands.is_admin(cast(discord.Interaction, interaction), admin_role_id)


async def _invoke(command: app_commands.Command[Any, ..., Any], interaction: FakeInteraction) -> None:
    await command.callback(cast(discord.Interaction, interaction))


# --- is_admin unit tests ---------------------------------------------------------


class TestIsAdmin:
    def test_guild_member_with_manage_guild_is_admin(self) -> None:
        interaction = FakeInteraction(guild=object(), user=_member(manage_guild=True))
        assert _is_admin(interaction, None) is True

    def test_guild_member_with_admin_role_is_admin(self) -> None:
        interaction = FakeInteraction(guild=object(), user=_member(roles=[FakeRole(ADMIN_ROLE_ID)]))
        assert _is_admin(interaction, ADMIN_ROLE_ID) is True

    def test_member_without_manage_guild_or_role_is_not_admin(self) -> None:
        interaction = FakeInteraction(guild=object(), user=_member())
        assert _is_admin(interaction, None) is False

    def test_member_with_unrelated_role_is_not_admin(self) -> None:
        interaction = FakeInteraction(guild=object(), user=_member(roles=[FakeRole(7)]))
        assert _is_admin(interaction, ADMIN_ROLE_ID) is False

    def test_no_admin_role_configured_means_role_cannot_grant(self) -> None:
        interaction = FakeInteraction(guild=object(), user=_member(roles=[FakeRole(ADMIN_ROLE_ID)]))
        assert _is_admin(interaction, None) is False

    def test_dm_context_is_never_admin_even_with_manage_guild(self) -> None:
        interaction = FakeInteraction(guild=None, user=_member(manage_guild=True))
        assert _is_admin(interaction, ADMIN_ROLE_ID) is False

    def test_dm_user_without_member_attributes_is_not_admin(self) -> None:
        user = Mock(spec=discord.User)
        interaction = FakeInteraction(guild=None, user=user)
        assert _is_admin(interaction, None) is False


# --- /raport flow tests -----------------------------------------------------------


class TestRaportCommand:
    def test_manage_guild_admin_defers_then_sends_report(self) -> None:
        run_report = FakeRunReport()
        _, command = _registered(run_report, lambda: _config())
        interaction = FakeInteraction(guild=object(), user=_member(manage_guild=True), channel=FakeChannel(CHANNEL_ID))
        run_report.deferred_source = interaction.response

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert run_report.calls == [(CHANNEL_ID, False)]
        assert run_report.deferred_when_called is True  # defer ran BEFORE run_report
        assert interaction.response.deferred is True
        assert interaction.followup.sent == [(RAPORT_SENT, True)]
        assert interaction.response.sent == []

    def test_admin_role_holder_gets_report_sent(self) -> None:
        run_report = FakeRunReport()
        _, command = _registered(run_report, lambda: _config(admin_role_id=ADMIN_ROLE_ID))
        interaction = FakeInteraction(
            guild=object(), user=_member(roles=[FakeRole(ADMIN_ROLE_ID)]), channel=FakeChannel(CHANNEL_ID)
        )

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert run_report.calls == [(CHANNEL_ID, False)]
        assert interaction.followup.sent == [(RAPORT_SENT, True)]

    def test_non_admin_denied_ephemeral_and_run_report_not_called(self) -> None:
        run_report = FakeRunReport()
        _, command = _registered(run_report, lambda: _config())
        interaction = FakeInteraction(guild=object(), user=_member(), channel=FakeChannel(CHANNEL_ID))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert interaction.response.sent == [(RAPORT_NO_PERMISSION, True)]
        assert interaction.response.deferred is False
        assert run_report.calls == []

    def test_role_configured_but_user_lacks_role_denied(self) -> None:
        run_report = FakeRunReport()
        _, command = _registered(run_report, lambda: _config(admin_role_id=ADMIN_ROLE_ID))
        interaction = FakeInteraction(guild=object(), user=_member(roles=[FakeRole(7)]), channel=FakeChannel(CHANNEL_ID))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert interaction.response.sent == [(RAPORT_NO_PERMISSION, True)]
        assert run_report.calls == []

    def test_dm_interaction_denied(self) -> None:
        run_report = FakeRunReport()
        _, command = _registered(run_report, lambda: _config(admin_role_id=ADMIN_ROLE_ID))
        interaction = FakeInteraction(guild=None, user=_member(manage_guild=True))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert interaction.response.sent == [(RAPORT_NO_PERMISSION, True)]
        assert run_report.calls == []

    def test_run_report_exception_sends_error_ack(self) -> None:
        run_report = FakeRunReport()
        run_report.error = RuntimeError("boom")
        _, command = _registered(run_report, lambda: _config())
        interaction = FakeInteraction(guild=object(), user=_member(manage_guild=True), channel=FakeChannel(CHANNEL_ID))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert interaction.followup.sent == [(RAPORT_ERROR, True)]
        assert RAPORT_SENT not in [content for content, _ in interaction.followup.sent]

    def test_missing_channel_sends_error_without_run(self) -> None:
        run_report = FakeRunReport()
        _, command = _registered(run_report, lambda: _config())
        interaction = FakeInteraction(guild=object(), user=_member(manage_guild=True))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert interaction.followup.sent == [(RAPORT_ERROR, True)]
        assert run_report.calls == []

    def test_config_getter_called_freshly_per_invocation(self) -> None:
        run_report = FakeRunReport()
        calls: list[int] = []

        def get_config() -> MergedConfig:
            calls.append(1)
            return _config(admin_role_id=ADMIN_ROLE_ID)

        _, command = _registered(run_report, get_config)
        interaction = FakeInteraction(
            guild=object(), user=_member(roles=[FakeRole(ADMIN_ROLE_ID)]), channel=FakeChannel(CHANNEL_ID)
        )

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert calls == [1]
        assert run_report.calls == [(CHANNEL_ID, False)]


# --- /wioski + /regiony flow tests -------------------------------------------------


class TestWioskiCommand:
    def test_admin_gets_embeds_ephemeral(self) -> None:
        embeds = [discord.Embed(title="🏗️ New & Lost Villages")]
        runner = FakeSectionRunner(embeds)
        tree, _ = _registered(FakeRunReport(), lambda: _config(), run_villages=runner)
        command = _command_by_name(tree, "wioski")
        interaction = FakeInteraction(guild=object(), user=_member(manage_guild=True), channel=FakeChannel(CHANNEL_ID))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert runner.calls == [1]
        assert interaction.response.deferred is True
        assert interaction.followup.sent == [(None, True)]
        assert interaction.followup.sent_embeds == [embeds]

    def test_empty_runner_sends_no_events_string(self) -> None:
        runner = FakeSectionRunner()
        tree, _ = _registered(FakeRunReport(), lambda: _config(), run_villages=runner)
        command = _command_by_name(tree, "wioski")
        interaction = FakeInteraction(guild=object(), user=_member(manage_guild=True), channel=FakeChannel(CHANNEL_ID))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert interaction.followup.sent == [(WIOSKI_NO_EVENTS, True)]
        assert interaction.followup.sent_embeds == []

    def test_non_admin_denied_and_runner_not_called(self) -> None:
        runner = FakeSectionRunner([discord.Embed(title="🏗️ New & Lost Villages")])
        tree, _ = _registered(FakeRunReport(), lambda: _config(), run_villages=runner)
        command = _command_by_name(tree, "wioski")
        interaction = FakeInteraction(guild=object(), user=_member(), channel=FakeChannel(CHANNEL_ID))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert interaction.response.sent == [(COMMAND_NO_PERMISSION, True)]
        assert interaction.response.deferred is False
        assert runner.calls == []

    def test_dm_interaction_denied(self) -> None:
        runner = FakeSectionRunner()
        tree, _ = _registered(FakeRunReport(), lambda: _config(), run_villages=runner)
        command = _command_by_name(tree, "wioski")
        interaction = FakeInteraction(guild=None, user=_member(manage_guild=True))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert interaction.response.sent == [(COMMAND_NO_PERMISSION, True)]
        assert runner.calls == []


class TestRegionyCommand:
    def test_admin_gets_embeds_ephemeral(self) -> None:
        embeds = [discord.Embed(title="🗺️ Regions")]
        runner = FakeSectionRunner(embeds)
        tree, _ = _registered(FakeRunReport(), lambda: _config(), run_regions=runner)
        command = _command_by_name(tree, "regiony")
        interaction = FakeInteraction(guild=object(), user=_member(manage_guild=True), channel=FakeChannel(CHANNEL_ID))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert runner.calls == [1]
        assert interaction.response.deferred is True
        assert interaction.followup.sent == [(None, True)]
        assert interaction.followup.sent_embeds == [embeds]

    def test_empty_runner_sends_no_data_string(self) -> None:
        runner = FakeSectionRunner()
        tree, _ = _registered(FakeRunReport(), lambda: _config(), run_regions=runner)
        command = _command_by_name(tree, "regiony")
        interaction = FakeInteraction(guild=object(), user=_member(manage_guild=True), channel=FakeChannel(CHANNEL_ID))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert interaction.followup.sent == [(REGIONS_NO_DATA, True)]
        assert interaction.followup.sent_embeds == []

    def test_non_admin_denied_and_runner_not_called(self) -> None:
        runner = FakeSectionRunner([discord.Embed(title="🗺️ Regions")])
        tree, _ = _registered(FakeRunReport(), lambda: _config(), run_regions=runner)
        command = _command_by_name(tree, "regiony")
        interaction = FakeInteraction(guild=object(), user=_member(), channel=FakeChannel(CHANNEL_ID))

        async def scenario() -> None:
            await _invoke(command, interaction)

        asyncio.run(scenario())
        assert interaction.response.sent == [(COMMAND_NO_PERMISSION, True)]
        assert runner.calls == []


# --- registration ---------------------------------------------------------------


class TestRegisterCommands:
    def test_registers_three_commands_with_descriptions(self) -> None:
        tree, _ = _registered(FakeRunReport(), lambda: _config())
        by_name = {command.name: command for command in tree.commands}
        assert set(by_name) == {"raport", "wioski", "regiony"}
        assert by_name["raport"].description == COMMAND_RAPORT_DESCRIPTION
        assert by_name["wioski"].description == COMMAND_WIOSKI_DESCRIPTION
        assert by_name["regiony"].description == COMMAND_REGIONS_DESCRIPTION
