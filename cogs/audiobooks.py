"""Astrabot cog: audiobook requests (web UI + Discord embeds).

Load it by adding ``"cogs.audiobooks"`` to ``startup_extensions`` in
``bot.py``. It follows the repo's async setup convention:

    async def setup(bot):
        await bot.add_cog(Audiobooks(bot))

Configuration (no secrets in code or in a committed file):
- If the bot's ``config.json`` has an ``"audiobooks"`` object, its keys
  are used. Thea can add that object later; this cog never reads or
  prints the rest of config.json, and it never touches the bot token.
- Environment variables override / fill in the same keys:
  AUDIOBOOKS_CHANNEL_ID, AUDIOBOOKS_WEB_HOST, AUDIOBOOKS_WEB_PORT,
  AUDIOBOOKS_DB_PATH, AUDIOBOOKS_ADMIN_IDS (comma-separated Discord
  user IDs), plus EMAIL_SMTP_* for email (see audiobooks_notify.py).
- Defaults: web on 127.0.0.1:8080, database audiobooks.db next to the
  bot, no admin IDs. With no admin IDs, the guild owner and the bot
  application's owner (via ``Bot.is_owner()``) may still click the
  buttons -- set AUDIOBOOKS_ADMIN_IDS in production to lock it down
  to explicit IDs.

Discord flow (persistent views, discord.py):
- New request -> embed with Fulfill Request + Reject Request.
- Fulfill -> status In Progress, Fulfill is replaced by
  Request Fulfilled, Reject stays available, requester is emailed.
- Request Fulfilled -> status Fulfilled, all buttons disabled,
  requester is emailed that the book has been added to the server.
- Reject -> a modal asks for an optional reason, then the row is
  hard-deleted, the embed is edited to Rejected with buttons
  disabled, and the requester is emailed the rejection message plus
  the reason, if one was given.
- Requester web cancel -> row is hard-deleted, its Discord message
  is deleted, and the requester is emailed that it was cancelled.
- Custom email -> the admin-only ``/email_user`` slash command (and
  the Email section of the admin dashboard) emails one requester a
  message typed by Thea, e.g. a special offer. The message is sent
  exactly as typed; it is not tied to a request row.
Buttons are routed by the Discord message ID stored on the request
row, so the same two persistent views work after a bot restart.
"""

from __future__ import annotations

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from .audiobooks_db import AudiobooksDB, book_label, normalize_email
from .audiobooks_notify import configure_email, send_email
from .audiobooks_web import AudiobooksWeb

log = logging.getLogger("audiobooks")

# Audiobook slash commands exist only in one guild: audiobooks.guild_id
# in config.json, or this one if the config does not set it. Falling
# back here, not to global, so a config gap can never publish them.
DEFAULT_GUILD_ID = 454792201633792001

STATUS_COLORS = {
    "Requested": discord.Color.blurple(),
    "In Progress": discord.Color.green(),
    "Fulfilled": discord.Color.green(),
    "Cancelled": discord.Color.light_grey(),
}


def _audiobooks_config(bot) -> dict:
    cfg: dict = {}
    try:
        raw = getattr(bot, "config", None)
        if isinstance(raw, dict):
            cfg.update(raw.get("audiobooks") or {})
    except Exception:
        pass
    # The repo's bot loads config.py's ``config`` dict; also accept a
    # module-level config attribute for tests / other loaders.
    if not cfg:
        try:
            from config import config as module_config  # type: ignore

            if isinstance(module_config, dict):
                cfg.update(module_config.get("audiobooks") or {})
        except Exception:
            pass
    if os.environ.get("AUDIOBOOKS_CHANNEL_ID"):
        try:
            cfg["request_channel_id"] = int(os.environ["AUDIOBOOKS_CHANNEL_ID"])
        except ValueError:
            pass
    if os.environ.get("AUDIOBOOKS_WEB_HOST"):
        cfg["web_host"] = os.environ["AUDIOBOOKS_WEB_HOST"]
    if os.environ.get("AUDIOBOOKS_WEB_PORT"):
        try:
            cfg["web_port"] = int(os.environ["AUDIOBOOKS_WEB_PORT"])
        except ValueError:
            pass
    if os.environ.get("AUDIOBOOKS_DB_PATH"):
        cfg["db_path"] = os.environ["AUDIOBOOKS_DB_PATH"]
    if os.environ.get("AUDIOBOOKS_ADMIN_IDS"):
        ids = []
        for part in os.environ["AUDIOBOOKS_ADMIN_IDS"].split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        cfg["admin_user_ids"] = ids
    if os.environ.get("AUDIOBOOKS_ADMIN_EMAIL"):
        cfg["admin_email"] = os.environ["AUDIOBOOKS_ADMIN_EMAIL"]
    email_cfg = dict(cfg.get("email") or {})
    _env_map = {
        "EMAIL_SMTP_HOST": "smtp_host",
        "EMAIL_SMTP_PORT": "smtp_port",
        "EMAIL_SMTP_USER": "smtp_user",
        "EMAIL_SMTP_PASSWORD": "smtp_password",
        "EMAIL_FROM_ADDRESS": "from_address",
    }
    for env_key, cfg_key in _env_map.items():
        if os.environ.get(env_key):
            email_cfg[cfg_key] = os.environ[env_key]
    if email_cfg:
        cfg["email"] = email_cfg
    return cfg


def build_embed(row: dict) -> discord.Embed:
    embed = discord.Embed(
        title=book_label(row),
        color=STATUS_COLORS.get(row["status"], discord.Color.blurple()),
    )
    embed.add_field(name="Requester", value=row.get("requesterName") or "Unknown", inline=True)
    embed.add_field(name="Status", value=row["status"], inline=True)
    embed.add_field(name="Request ID", value=str(row["id"]), inline=True)
    embed.add_field(name="Link", value=row["linkToBook"], inline=False)
    embed.set_footer(text="Audiobook request")
    return embed


class _RequestView(discord.ui.View):
    """Buttons shown while a request is still Requested."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Fulfill Request", style=discord.ButtonStyle.primary,
                       custom_id="audiobook:fulfill")
    async def fulfill(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_button(interaction, "fulfill")

    @discord.ui.button(label="Reject Request", style=discord.ButtonStyle.danger,
                       custom_id="audiobook:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_button(interaction, "reject")


class _InProgressView(discord.ui.View):
    """Buttons shown after Fulfill: Request Fulfilled appears here."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Request Fulfilled", style=discord.ButtonStyle.success,
                       custom_id="audiobook:fulfilled")
    async def fulfilled(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_button(interaction, "fulfilled")

    @discord.ui.button(label="Reject Request", style=discord.ButtonStyle.danger,
                       custom_id="audiobook:reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_button(interaction, "reject")


def view_for_status(cog, status: str, disabled: bool = False) -> discord.ui.View:
    if status == "In Progress":
        view = _InProgressView(cog)
    elif status == "Requested":
        view = _RequestView(cog)
    else:
        # Terminal states: show the requested-state buttons disabled so
        # the record is readable, but nothing can be clicked again.
        view = _RequestView(cog)
    if disabled:
        for item in view.children:
            item.disabled = True
    return view


class _RejectReasonModal(discord.ui.Modal, title="Reject request"):
    """Modal shown when an admin clicks Reject Request.

    The reason is optional: leaving it blank keeps the original
    rejection email wording unchanged. A reason that is given is
    appended to that email (and shown on the Rejected embed).
    """

    reason = discord.ui.TextInput(
        label="Reason for rejection",
        placeholder="Optional — e.g. no audiobook edition found",
        required=False,
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, cog, row: dict):
        super().__init__(title="Reject request")
        self.cog = cog
        self.row_id = row["id"]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # The admin check runs again here: the modal is a new
        # interaction, and the request may have changed since the
        # button was clicked.
        if not await self.cog.is_admin(interaction):
            await interaction.response.send_message(
                "You are not allowed to do that.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        row = self.cog.db.get_request(self.row_id)
        if row is None:
            await interaction.followup.send(
                "That request is no longer available.", ephemeral=True)
            return
        if row["status"] == "Fulfilled":
            await interaction.followup.send(
                "That request is already fulfilled and cannot be rejected.",
                ephemeral=True)
            return
        reason = (self.reason.value or "").strip()
        await self.cog.execute_reject(interaction, row, reason)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.warning("Reject-reason modal failed: %s", error)
        try:
            await interaction.followup.send(
                "Something went wrong with that rejection. Please try again.",
                ephemeral=True)
        except Exception:
            pass


class Audiobooks(commands.Cog):
    def __init__(self, bot, *, db=None, notifier=None, config_override=None):
        self.bot = bot
        self.config = config_override if config_override is not None else _audiobooks_config(bot)
        configure_email(self.config.get("email") or {})
        self.db = db if db is not None else AudiobooksDB(self.config.get("db_path", "audiobooks.db"))
        self.notifier = notifier if notifier is not None else send_email
        self.web = AudiobooksWeb(self)
        self._web_runner = None
        self._web_site = None

    # -- config helpers ------------------------------------------------
    @property
    def channel_id(self) -> int | None:
        value = self.config.get("request_channel_id")
        return int(value) if value else None

    @property
    def admin_email(self) -> str:
        """The one email address allowed into the admin web dashboard.

        Default is Thea's address, as she specified; overridable via
        config ``audiobooks.admin_email`` or env
        ``AUDIOBOOKS_ADMIN_EMAIL``. This is separate from Discord
        admin IDs: dashboard access is by logged-in email only.
        """
        value = self.config.get("admin_email") or "theruvial237@gmail.com"
        return str(value).strip().lower()

    def is_dashboard_admin(self, email: str | None) -> bool:
        return bool(email) and email.strip().lower() == self.admin_email

    # -- Admin-dashboard actions (shared with the web UI) ------------
    async def admin_fulfill(self, request_id: int):
        """Fulfill via the dashboard. Returns (row, sent, error)."""
        row = self.db.get_request(request_id)
        if row is None:
            return None, False, "not_found"
        if row["status"] == "Fulfilled":
            return row, False, "already_closed"
        if row["status"] == "In Progress":
            return row, False, "already_in_progress"
        updated = self.db.set_request_status(request_id, "In Progress")
        sent = await self.notifier(
            updated["requester"],
            "Your audiobook request is being worked on",
            f"Hi {updated.get('requesterName') or 'there'}, Thea has begun "
            f"working on your request for {book_label(updated)}.",
        )
        await self.update_embed_for_request(updated)
        return updated, sent, None

    async def admin_fulfilled(self, request_id: int):
        """Mark fulfilled via the dashboard. Returns (row, sent, error)."""
        row = self.db.get_request(request_id)
        if row is None:
            return None, False, "not_found"
        if row["status"] == "Fulfilled":
            return row, False, "already_closed"
        if row["status"] != "In Progress":
            return row, False, "must_fulfill_first"
        updated = self.db.set_request_status(request_id, "Fulfilled")
        sent = await self.notifier(
            updated["requester"],
            "Your audiobook request has been fulfilled",
            f"The requested book {book_label(updated)} has been added to the server",
        )
        await self.delete_embed_for_request(updated)
        return updated, sent, None

    async def admin_reject(self, request_id: int, reason: str):
        """Reject via the dashboard. Returns (sent, error).

        Same email/embed result as the Discord Reject modal: row
        deleted, embed edited to Rejected (with the reason, if given),
        email sent with the original wording plus the reason.
        """
        row = self.db.get_request(request_id)
        if row is None:
            return False, "not_found"
        if row["status"] == "Fulfilled":
            return False, "already_closed"
        email = row["requester"]
        book = book_label(row)
        self.db.delete_request(request_id)
        embed = (
            discord.Embed(title=book, color=discord.Color.red(), description="Rejected")
            .add_field(name="Requester", value=row.get("requesterName") or "Unknown")
            .add_field(name="Status", value="Rejected")
            .add_field(name="Request ID", value=str(request_id))
        )
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        if row.get("discordMessageId") and row.get("discordChannelId"):
            try:
                channel = self.bot.get_channel(int(row["discordChannelId"]))
                if channel is None:
                    channel = await self.bot.fetch_channel(int(row["discordChannelId"]))
                message = await channel.fetch_message(int(row["discordMessageId"]))
                await message.edit(
                    embed=embed,
                    view=view_for_status(self, "Requested", disabled=True),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not edit rejected embed %s: %s", request_id, exc)
        body = f"Request Rejected for {book} Speak to Thea for more information"
        if reason:
            body += f"\n\nReason: {reason}"
        sent = await self.notifier(email, f"Request Rejected for {book}", body)
        return sent, None

    async def admin_delete_request(self, request_id: int) -> bool:
        """Hard-delete a request and its Discord message (dashboard)."""
        row = self.db.get_request(request_id)
        if row is None:
            return False
        await self.delete_embed_for_request(row)
        self.db.delete_request(request_id)
        return True

    # -- Custom email to one requester (slash + dashboard) ---------
    def resolve_requester(self, value: str):
        """Resolve a typed name or email to a user row.

        Returns ``(user, error)`` with exactly one None. A value
        containing ``@`` is treated as an email address; anything
        else is matched case-insensitively against first names.
        A name shared by several users is ambiguous on purpose --
        the caller must pick one by email, not guess which one.
        """
        raw = (value or "").strip()
        if not raw:
            return None, "empty_requester"
        if "@" in raw:
            email = normalize_email(raw)
            if email is None:
                return None, "invalid_email"
            user = self.db.get_user(email)
            return (user, None) if user else (None, "not_found")
        matches = [u for u in self.db.list_users()
                   if u["firstName"].strip().lower() == raw.lower()]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            emails = ", ".join(u["email"] for u in matches)
            return None, f"ambiguous: several users are named {raw} ({emails}) -- use an email address"
        return None, "not_found"

    async def send_custom_email(self, requester: str, message: str,
                                subject: str | None = None):
        """Email one requester a custom message (offers, news, etc.).

        Returns ``(user, sent, error)``. The message body is sent
        exactly as typed -- no greeting or footer is added, so Thea
        controls the wording. A blank subject becomes
        "A message from Thea". This is not tied to a request row.
        """
        body = (message or "").strip()
        if not body:
            return None, False, "empty_message"
        user, error = self.resolve_requester(requester)
        if error:
            return None, False, error
        subject = (subject or "").strip() or "A message from Thea"
        sent = await self.notifier(user["email"], subject, body)
        return user, sent, None

    @app_commands.command(name="email_user",
                          description="Email an audiobook user a custom message (admin only)")
    @app_commands.describe(
        requester="Requester's name (pick from the list) or email address",
        message="Message to email them",
        subject="Optional email subject (default: A message from Thea)",
    )
    async def email_user(self, interaction: discord.Interaction,
                         requester: str, message: str,
                         subject: str | None = None) -> None:
        if not await self.is_admin(interaction):
            await interaction.response.send_message(
                "You are not allowed to do that.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        user, sent, error = await self.send_custom_email(
            requester, message, subject)
        if error == "not_found":
            await interaction.followup.send(
                f"No user found for {requester!r}.", ephemeral=True)
        elif error == "empty_message":
            await interaction.followup.send(
                "The message was blank -- nothing was sent.", ephemeral=True)
        elif error:
            await interaction.followup.send(error, ephemeral=True)
        elif sent:
            await interaction.followup.send(
                f"Email sent to {user['firstName']} ({user['email']}).",
                ephemeral=True)
        else:
            await interaction.followup.send(
                f"Email to {user['firstName']} ({user['email']}) could not "
                "be sent -- check the email configuration.",
                ephemeral=True)

    @email_user.autocomplete("requester")
    async def email_user_autocomplete(self, interaction: discord.Interaction,
                                      current: str):
        # Admins only, so a non-admin typing the command cannot
        # enumerate users' names and emails from the suggestions.
        if not await self.is_admin(interaction):
            return []
        query = (current or "").strip().lower()
        choices = []
        for user in self.db.list_users():
            if query and query not in user["firstName"].lower() \
                    and query not in user["email"].lower():
                continue
            choices.append(app_commands.Choice(
                name=f"{user['firstName']} ({user['email']})",
                value=user["email"]))
            if len(choices) >= 25:
                break
        return choices

    async def is_admin(self, interaction) -> bool:
        """Who may click Fulfill / Reject / Request Fulfilled.

        Bug fix 2026-10-29: the previous version read
        ``bot.owner_id`` directly. Astrabot's ``bot.py`` never passes
        ``owner_id`` to ``commands.Bot``, so that attribute is always
        ``None`` and -- with an empty ``admin_user_ids`` list -- every
        click, including Thea's, was denied with "not allowed".
        discord.py only populates the application owner when
        ``Bot.is_owner()`` fetches application info, so use that.

        Allowed, in order:
        1. A user ID in ``admin_user_ids`` from config.
        2. The owner of the guild the button was clicked in.
        3. The bot application's owner, via ``bot.is_owner()``.
        """
        user_id = int(interaction.user.id)
        admins = self.config.get("admin_user_ids") or []
        if user_id in [int(x) for x in admins]:
            return True
        guild = getattr(interaction, "guild", None)
        guild_owner_id = getattr(guild, "owner_id", None) if guild else None
        if guild_owner_id is not None and int(guild_owner_id) == user_id:
            return True
        is_owner = getattr(self.bot, "is_owner", None)
        if is_owner is not None:
            try:
                if await is_owner(interaction.user):
                    return True
            except Exception:
                pass
        return False

    async def resync_channel(self) -> None:
        """Clear the requests channel and re-post every DB request.

        Runs on every bot start (see ``cog_load``). The channel is
        treated as dedicated to audiobook requests: every message in
        it is deleted first, then one embed per database row is posted
        oldest-first, with buttons matching its current status:

        - ``Requested``   -> Fulfill Request + Reject Request, enabled
        - ``In Progress`` -> Request Fulfilled + Reject Request, enabled
        - ``Fulfilled``   -> not re-posted: fulfilling deletes the message
        - ``Cancelled``   -> Fulfill Request + Reject Request, disabled

        Each new message ID is stored back on its row, so button clicks
        on the re-posted embeds route to the right request. If the
        channel is not configured or cannot be reached, this logs and
        returns -- it must never stop the bot or the web UI starting.
        """
        channel = await self._get_channel()
        if channel is None:
            return
        # Clear: delete one by one (bulk purge fails on messages older
        # than 14 days, and this channel is expected to be small).
        try:
            async for message in channel.history(limit=None):
                try:
                    await message.delete()
                except Exception as exc:  # noqa: BLE001
                    log.warning("Could not delete channel message %s: %s",
                                getattr(message, "id", "?"), exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not clear audiobook channel: %s", exc)
            return
        rows = self.db.list_requests()
        # list_requests is newest-first; post oldest-first so the
        # channel reads chronologically. Cancelled rows are never
        # re-posted: a cancelled request is deleted (see the website
        # cancel flow), and any legacy Cancelled rows are simply gone
        # from the channel after a restart, since it is cleared first.
        count = 0
        for row in reversed(rows):
            status = row["status"]
            if status in ("Cancelled", "Fulfilled"):
                continue
            if status == "In Progress":
                view = view_for_status(self, "In Progress")
            else:
                view = view_for_status(self, "Requested")
            try:
                message = await channel.send(embed=build_embed(row), view=view)
                self.db.set_discord_message(row["id"], message.id, channel.id)
                count += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not re-post request %s: %s", row["id"], exc)
        log.info("Audiobook channel resynced: %s request(s) re-posted.", count)

    async def cog_load(self) -> None:
        # Persistent views: register before any interaction is dispatched.
        try:
            self.bot.add_view(_RequestView(self))
            self.bot.add_view(_InProgressView(self))
        except Exception as exc:  # noqa: BLE001 -- view registration must not kill the cog
            log.warning("Could not register persistent audiobook views: %s", exc)
        # Rebuild the requests channel from the database on every start.
        try:
            await self.resync_channel()
        except Exception as exc:  # noqa: BLE001 -- resync must not kill the cog
            log.warning("Audiobook channel resync failed: %s", exc)
        # Web server on the bot's event loop (aiohttp is already a
        # discord.py dependency, so this adds no new runtime package).
        try:
            from aiohttp import web as aio_web

            app = self.web.build_app()
            self._web_runner = aio_web.AppRunner(app)
            await self._web_runner.setup()
            host = self.config.get("web_host", "127.0.0.1")
            port = int(self.config.get("web_port", 8080))
            self._web_site = aio_web.TCPSite(self._web_runner, host, port)
            await self._web_site.start()
            log.info("Audiobook requests web UI listening on %s:%s", host, port)
        except Exception as exc:  # noqa: BLE001
            log.warning("Audiobook web UI could not start: %s", exc)

    async def cog_unload(self) -> None:
        # Awaited by discord.py (via maybe_coroutine). Awaiting the
        # web cleanup matters for cog reloads -- e.g. the self-update
        # watcher: if the old runner were only scheduled for cleanup,
        # the reloaded cog could try to bind the same port before
        # the old server had released it, and the web UI would fail
        # to start. Awaiting here frees the port before reload loads.
        if self._web_runner is not None:
            try:
                await self._web_runner.cleanup()
            except Exception:
                pass
            self._web_runner = None
            self._web_site = None

    # -- Discord embed handling ---------------------------------------
    async def _get_channel(self):
        if not self.channel_id:
            log.warning("audiobooks.request_channel_id is not configured; embed not sent.")
            return None
        channel = self.bot.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(self.channel_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not fetch audiobook channel %s: %s", self.channel_id, exc)
                return None
        return channel

    async def send_request_embed(self, row: dict) -> None:
        """Send the embed for a newly created row and store its IDs."""
        full = self.db.get_request(row["id"]) or row
        channel = await self._get_channel()
        if channel is None:
            return
        message = await channel.send(embed=build_embed(full),
                                     view=view_for_status(self, "Requested"))
        self.db.set_discord_message(row["id"], message.id, channel.id)

    async def update_embed_for_request(self, row: dict, disabled: bool = False) -> None:
        """Edit the original embed to match a row's current status."""
        if not row.get("discordMessageId") or not row.get("discordChannelId"):
            return
        try:
            channel = self.bot.get_channel(int(row["discordChannelId"]))
            if channel is None:
                channel = await self.bot.fetch_channel(int(row["discordChannelId"]))
            message = await channel.fetch_message(int(row["discordMessageId"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not fetch embed for request %s: %s", row["id"], exc)
            return
        status = row["status"]
        # An embed edit must never block the email that follows it:
        # if this edit throws, the caller would never reach the
        # notifier, which is exactly the "status changed, no email"
        # symptom. Log and continue instead.
        try:
            if status == "Cancelled":
                # Reuse the requested view disabled: no further action.
                await message.edit(embed=build_embed(row),
                                   view=view_for_status(self, "Requested", disabled=True))
            elif disabled:
                await message.edit(embed=build_embed(row),
                                   view=view_for_status(self, status, disabled=True))
            else:
                await message.edit(embed=build_embed(row),
                                   view=view_for_status(self, status))
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not edit embed for request %s: %s", row["id"], exc)

    async def delete_embed_for_request(self, row: dict) -> None:
        """Delete the Discord message for a request, if it still exists.

        Used by the website cancel flow and by fulfilling a request. A failure to delete (message
        already gone, missing permission) is logged, not raised: the
        cancellation itself must not be rolled back because of it.
        """
        if not row.get("discordMessageId") or not row.get("discordChannelId"):
            return
        try:
            channel = self.bot.get_channel(int(row["discordChannelId"]))
            if channel is None:
                channel = await self.bot.fetch_channel(int(row["discordChannelId"]))
            message = await channel.fetch_message(int(row["discordMessageId"]))
            await message.delete()
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not delete embed for request %s: %s", row["id"], exc)

    async def _disable_embed(self, interaction: discord.Interaction, row: dict) -> None:
        try:
            await interaction.message.edit(
                embed=build_embed(row),
                view=view_for_status(self, row["status"], disabled=True),
            )
        except Exception:
            pass

    async def execute_reject(self, interaction: discord.Interaction,
                             row: dict, reason: str) -> None:
        """Carry out a rejection after the reason modal was submitted.

        ``interaction`` is already deferred by the modal submit handler,
        so results go to ``followup``. Captures the email address and
        book name BEFORE deleting the row, deletes the row, edits the
        embed to Rejected (with the reason, if one was given), and
        emails the requester. The original rejection wording is kept
        verbatim as the first part of the email; a reason is appended,
        never substituted for it.
        """
        email = row["requester"]
        book = book_label(row)
        request_id = row["id"]
        self.db.delete_request(request_id)
        embed = (
            discord.Embed(
                title=book,
                color=discord.Color.red(),
                description="Rejected",
            )
            .add_field(name="Requester", value=row.get("requesterName") or "Unknown")
            .add_field(name="Status", value="Rejected")
            .add_field(name="Request ID", value=str(request_id))
        )
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        edited = False
        message = getattr(interaction, "message", None)
        try:
            if message is not None:
                await message.edit(
                    embed=embed,
                    view=view_for_status(self, "Requested", disabled=True),
                )
                edited = True
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not edit rejected embed %s: %s", request_id, exc)
        if not edited and row.get("discordMessageId") and row.get("discordChannelId"):
            try:
                channel = self.bot.get_channel(int(row["discordChannelId"]))
                if channel is None:
                    channel = await self.bot.fetch_channel(int(row["discordChannelId"]))
                message = await channel.fetch_message(int(row["discordMessageId"]))
                await message.edit(
                    embed=embed,
                    view=view_for_status(self, "Requested", disabled=True),
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not edit rejected embed %s: %s", request_id, exc)
        body = f"Request Rejected for {book} Speak to Thea for more information"
        if reason:
            body += f"\n\nReason: {reason}"
        sent = await self.notifier(
            email,
            f"Request Rejected for {book}",
            body,
        )
        await interaction.followup.send(
            "Rejected." + ("" if sent else " (The email could not be sent -- please follow up manually.)"),
            ephemeral=True,
        )

    # -- Button actions -------------------------------------------------
    async def handle_button(self, interaction: discord.Interaction, action: str) -> None:
        """Shared handler for Fulfill / Reject / Request Fulfilled.

        Action is the button's action name, not a user-supplied value.
        The request is found by the message ID of the embed that was
        clicked, which is how persistent views survive restarts.
        """
        if not await self.is_admin(interaction):
            await interaction.response.send_message(
                "You are not allowed to do that.", ephemeral=True)
            log.info("Non-admin %s clicked audiobook %s", interaction.user.id, action)
            return
        if action == "reject":
            # A modal must be the interaction's initial response, so
            # Reject does NOT defer first -- it shows the reason modal.
            # The actual rejection happens in the modal's on_submit.
            row = None
            try:
                row = self.db.get_request_by_message(interaction.message.id)
            except Exception as exc:  # noqa: BLE001
                log.warning("Audiobook button lookup failed: %s", exc)
            if row is None:
                await interaction.response.send_message(
                    "That request is no longer available.", ephemeral=True)
                return
            log.info("Admin %s clicked reject on request %s (%s)",
                     interaction.user.id, row["id"], row["status"])
            if row["status"] == "Fulfilled":
                await interaction.response.send_message(
                    "That request is already fulfilled and cannot be rejected.",
                    ephemeral=True)
                return
            await interaction.response.send_modal(_RejectReasonModal(self, row))
            return
        await interaction.response.defer(ephemeral=True)
        row = None
        try:
            row = self.db.get_request_by_message(interaction.message.id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Audiobook button lookup failed: %s", exc)
        if row is None:
            await interaction.followup.send(
                "That request is no longer available.", ephemeral=True)
            return
        log.info("Admin %s clicked %s on request %s (%s)",
                 interaction.user.id, action, row["id"], row["status"])

        if action == "fulfill":
            if row["status"] in ("Cancelled", "Fulfilled"):
                await interaction.followup.send("That request is no longer available.", ephemeral=True)
                await self._disable_embed(interaction, row)
                return
            if row["status"] == "In Progress":
                await interaction.followup.send("That request is already in progress.", ephemeral=True)
                return
            updated = self.db.set_request_status(row["id"], "In Progress")
            # Email first, embed second: the email is the part the
            # requester actually sees, and must not depend on the edit.
            sent = await self.notifier(
                row["requester"],
                "Your audiobook request is being worked on",
                f"Hi {row.get('requesterName') or 'there'}, Thea has begun "
                f"working on your request for {book_label(row)}.",
            )
            await self.update_embed_for_request(updated)
            await interaction.followup.send(
                "Marked In Progress." + ("" if sent else " (The email could not be sent -- please follow up manually.)"),
                ephemeral=True,
            )
            return

        if action == "fulfilled":
            if row["status"] == "Fulfilled":
                await interaction.followup.send("That request is already fulfilled.", ephemeral=True)
                await self._disable_embed(interaction, row)
                return
            if row["status"] != "In Progress":
                await interaction.followup.send(
                    "Fulfill Request must be clicked first.", ephemeral=True)
                return
            updated = self.db.set_request_status(row["id"], "Fulfilled")
            # Email first, embed second: see the In Progress comment
            # above — an embed edit must never be able to swallow this.
            sent = await self.notifier(
                row["requester"],
                "Your audiobook request has been fulfilled",
                f"The requested book {book_label(row)} has been added to the server",
            )
            await self.delete_embed_for_request(updated)
            await interaction.followup.send(
                "Marked Fulfilled." + ("" if sent else " (The email could not be sent -- please follow up manually.)"),
                ephemeral=True,
            )
            return

        await interaction.followup.send("Unknown action.", ephemeral=True)


async def setup(bot):
    cog = Audiobooks(bot)
    guild_id = int(cog.config.get("guild_id") or DEFAULT_GUILD_ID)
    # Set on each command rather than add_cog(guild=...): reloads remove
    # a cog without naming a guild, which would leave its commands behind.
    for command in cog.get_app_commands():
        app_commands.guilds(guild_id)(command)
    await bot.add_cog(cog)
    # Here rather than cog_load, which runs before commands are added.
    # setup() also runs on every reload, so reloads stay in sync.
    try:
        await bot.tree.sync(guild=discord.Object(id=guild_id))
    except Exception as exc:  # noqa: BLE001 -- a failed sync must not stop the site
        log.warning("Could not sync audiobook commands: %s", exc)
