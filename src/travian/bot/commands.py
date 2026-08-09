"""The /raport slash command (task 11): admin-only manual report trigger.

Design decisions (documented for the plan):

- **Registration-based injection (circular-import resolution)**: the command
  receives ``run_report`` and the config getter as parameters of
  ``register_commands(tree, run_report, get_config)`` — main.py wires the
  real functions, tests inject fakes. The module graph is single-direction
  (main → commands): the getter's return is typed against the minimal
  ``AdminConfig`` protocol (satisfied structurally by ``MergedConfig``), so
  commands.py never imports main.py and no cycle exists in either direction
  — confirmed by basedpyright's ``reportImportCycles``.
- **Admin check** (``is_admin``): ``manage_guild`` guild permission OR
  membership in the configured admin role. ``interaction.guild is None``
  (DM) short-circuits to False — a DM user has no guild permissions and no
  roles. The user is narrowed via ``isinstance(discord.Member)``: plain
  ``discord.User`` objects (DM) fail the check.
- **Config freshness**: the admin role id comes from a FRESH merged config
  read per invocation (``get_config`` via ``asyncio.to_thread`` — sqlite must
  not block the bot loop), so dashboard changes apply immediately.
  A config-read failure escapes to discord.py's command error handling
  (genuine bug — not guarded).
- **Ephemeral acknowledgements**: the denial and the "Report sent"/error
  responses are ephemeral — the report embed in the channel is the visible
  artifact; the command's own chatter never pollutes the channel.
- **Strings**: the command name/description are Discord API surface rather
  than embed text, but all user-facing strings live in ``strings.py``
  (repo convention, task 8) — see the constants used here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Protocol

import discord
from discord import app_commands

from travian.strings import (
    COMMAND_RAPORT_DESCRIPTION,
    RAPORT_ERROR,
    RAPORT_NO_PERMISSION,
    RAPORT_SENT,
)

logger = logging.getLogger(__name__)


class ReportRunner(Protocol):
    """The ``run_report`` surface the command needs (injected by main.py)."""

    async def __call__(self, channel_id: int, require_today: bool = True) -> None: ...


class AdminConfig(Protocol):
    """The config surface the command reads — satisfied by main's ``MergedConfig``.

    Typing the getter against this minimal protocol instead of importing
    ``MergedConfig`` keeps the module graph single-direction (main → commands):
    no circular import exists in either direction. Read-only (``@property``)
    to match ``MergedConfig``'s frozen attributes.
    """

    @property
    def admin_role_id(self) -> int | None: ...


ConfigGetter = Callable[[], AdminConfig]


def is_admin(interaction: discord.Interaction, admin_role_id: int | None) -> bool:
    """True when the interaction's user may run /raport.

    Admin = ``manage_guild`` guild permission OR membership in the configured
    admin role. Non-guild contexts (DMs): no guild permissions and no roles →
    always False.
    """
    if interaction.guild is None:
        return False
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False
    if user.guild_permissions.manage_guild:
        return True
    if admin_role_id is not None:
        return any(role.id == admin_role_id for role in user.roles)
    return False


async def _raport(
    interaction: discord.Interaction,
    run_report: ReportRunner,
    get_config: ConfigGetter,
) -> None:
    """The /raport flow: admin check → defer → run_report → ephemeral ack."""
    cfg = await asyncio.to_thread(get_config)
    if not is_admin(interaction, cfg.admin_role_id):
        _ = await interaction.response.send_message(RAPORT_NO_PERMISSION, ephemeral=True)
        return
    _ = await interaction.response.defer()
    channel = interaction.channel
    if channel is None:
        # Unreachable for guild commands (the admin check already excludes
        # DMs) — the type allows None, so this branch errors out instead.
        _ = await interaction.followup.send(RAPORT_ERROR, ephemeral=True)
        return
    try:
        await run_report(channel.id, require_today=False)
    except Exception:
        # Defensive: run_report never raises by contract (logs internally) —
        # a bug must surface a visible error ack, never a false "Report sent".
        logger.exception("raport command failed")
        _ = await interaction.followup.send(RAPORT_ERROR, ephemeral=True)
        return
    _ = await interaction.followup.send(RAPORT_SENT, ephemeral=True)


def register_commands[ClientT: discord.Client](
    tree: app_commands.CommandTree[ClientT],
    run_report: ReportRunner,
    get_config: ConfigGetter,
) -> None:
    """Add the /raport command to ``tree`` (callback closes over the injected fns)."""

    async def callback(interaction: discord.Interaction) -> None:
        await _raport(interaction, run_report, get_config)

    tree.add_command(
        app_commands.Command(
            name="raport",
            description=COMMAND_RAPORT_DESCRIPTION,
            callback=callback,
        )
    )
