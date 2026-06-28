import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import re
import random
import time
import sqlite3
import json
import functools
from collections import defaultdict
from typing import Optional
from discord.ui import Select, Button, View
from discord import ButtonStyle
from io import BytesIO
from datetime import datetime


# ============================================================
# CONFIG — change these IDs to match your server
# ============================================================

BOOSTER_ROLE_ID = 1520740405547761755          
ADMIN_ROLE_IDS  = [1517236355275428040, 1517235116114579727]

GIVEAWAY_CHANNEL_NAME   = "🎁︱𝒩𝓊𝓂𝒷𝑒𝓇-𝒢𝒾𝓋𝑒𝒶𝓌𝒶𝓎"
LEAKS_CHANNEL_NAME      = "𝙇𝙀𝘼𝙆𝙎-𝙍𝙀𝙌𝙐𝙀𝙎𝙏"
TICKET_CATEGORY_ID      = 1348042174159392768
ANNOUNCEMENT_CHANNEL_ID = 1386095247997665521
SUBMISSION_CHANNEL_ID   = 1519432733313466638
RATING_CHANNEL_ID       = 1519432943599091762
FEEDBACK_CHANNEL_ID     = 1519433070934233158
GIVEAWAY_CHANNEL_ID     = 1363495611995001013
MIDDLEMAN_ROLE_ID       = 1348072637972090880
BLACKLIST_ROLE_ID       = 1344056030153146448

MESSAGES_PER_SHECKLE    = 10
BOOSTER_PACKS_THRESHOLD = 5   # Number of boosts needed to access ALL booster leaks


# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.default()
intents.messages       = True
intents.guilds         = True
intents.members        = True
intents.message_content = True

bot  = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


# ============================================================
# HELPERS
# ============================================================

def auto_defer(ephemeral=True):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            interaction = args[0]
            try:
                await interaction.response.defer(ephemeral=ephemeral)
            except discord.errors.InteractionAlreadyResponded:
                pass
            return await func(*args, **kwargs)
        return wrapper
    return decorator


def has_admin_role(member: discord.Member) -> bool:
    return any(role.id in ADMIN_ROLE_IDS for role in member.roles)


def is_booster(member: discord.Member) -> bool:
    """Returns True if the member has the booster role."""
    return any(role.id == BOOSTER_ROLE_ID for role in member.roles)


def booster_boost_count(guild: discord.Guild, member: discord.Member) -> int:
    """
    Returns how many times this member has boosted.
    Discord exposes this via member.premium_since — if they're actively boosting
    we count them as 1. For real multi-boost counting you'd need a premium tier
    tracking system; here we use the role as a proxy (1 boost = role present).
    Expand this function if you add a custom multi-boost tracker.
    """
    return 1 if is_booster(member) else 0


async def safe_send_ephemeral(interaction: discord.Interaction, message: str):
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)
    except discord.NotFound:
        pass


# ============================================================
# DATABASE
# ============================================================

DB_PATH = "leaks.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leaks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL UNIQUE,
                link       TEXT    NOT NULL,
                payhip_url TEXT    NOT NULL,
                type       TEXT    NOT NULL DEFAULT 'normal',
                added_by   INTEGER NOT NULL,
                added_at   REAL    NOT NULL
            )
        """)
        # Migration: add 'type' column if it doesn't exist yet (for existing DBs)
        try:
            conn.execute("ALTER TABLE leaks ADD COLUMN type TEXT NOT NULL DEFAULT 'normal'")
        except sqlite3.OperationalError:
            pass  # Column already exists
        conn.commit()


init_db()


# ---- DB helpers ----

def add_leak(name: str, link: str, payhip_url: str, leak_type: str, user_id: int) -> bool:
    leak_type = leak_type.lower().strip()
    if leak_type not in ("normal", "booster"):
        leak_type = "normal"
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO leaks (name, link, payhip_url, type, added_by, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name.strip(), link.strip(), payhip_url.strip(), leak_type, user_id, time.time())
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def delete_leak(name: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM leaks WHERE LOWER(name) = ?", (name.lower().strip(),))
        conn.commit()
        return cur.rowcount > 0


def search_leaks(query: str, leak_type: Optional[str] = None):
    query = query.lower().strip()
    with get_db() as conn:
        if leak_type:
            rows = conn.execute(
                "SELECT * FROM leaks WHERE type = ?", (leak_type,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM leaks").fetchall()
    return [r for r in rows if query in r["name"].lower()]


def get_all_leaks(leak_type: Optional[str] = None):
    with get_db() as conn:
        if leak_type:
            return conn.execute(
                "SELECT * FROM leaks WHERE type = ? ORDER BY name ASC", (leak_type,)
            ).fetchall()
        return conn.execute("SELECT * FROM leaks ORDER BY name ASC").fetchall()


def update_leak_type(name: str, new_type: str) -> bool:
    new_type = new_type.lower().strip()
    if new_type not in ("normal", "booster"):
        return False
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE leaks SET type = ? WHERE LOWER(name) = ?",
            (new_type, name.lower().strip())
        )
        conn.commit()
        return cur.rowcount > 0


# ============================================================
# IN-MEMORY STATE
# ============================================================

giveaway_logs    = []
active_giveaways = {}
user_message_counts = defaultdict(int)
user_sheckles       = defaultdict(int)
trade_offers        = defaultdict(list)
trade_logs_list     = []
rating_submissions  = {}
pending_imports     = {}

# Garden (disabled but kept)
user_inventory      = defaultdict(lambda: {"growing": [], "grown": []})
user_achievements   = defaultdict(list)
user_fertilizers    = defaultdict(lambda: defaultdict(int))
user_active_boosts  = defaultdict(dict)
current_plant_event = None

current_season = {"name": "Spring", "boosted_seeds": ["Carrot", "Strawberry"], "multiplier": 1.0}
SEASONS = [
    {"name": "Spring", "boosted_seeds": ["Carrot", "Strawberry"]},
    {"name": "Summer", "boosted_seeds": ["Ember Lily", "Bamboo"]},
    {"name": "Fall",   "boosted_seeds": ["Potato", "Sugar Apple"]},
    {"name": "Winter", "boosted_seeds": ["Beanstalk"]},
]

seeds = {
    "Carrot":      (2,  250),
    "Strawberry":  (10,  50),
    "Potato":      (5,    0),
    "Bamboo":      (20, 300),
    "Ember Lily":  (55, 550),
    "Sugar Apple": (80, 800),
    "Beanstalk":   (70, 750),
}
SEED_RARITIES = {
    "Carrot": "Uncommon", "Strawberry": "Common", "Potato": "Common",
    "Bamboo": "Rare", "Ember Lily": "Mythical",
    "Sugar Apple": "Legendary", "Beanstalk": "Legendary",
}
mutations = {
    "global": {
        "Giant":    {"multiplier": 1.5, "rarity": 0.05,  "description": "Yields 50% more sheckles"},
        "Golden":   {"multiplier": 2.0, "rarity": 0.01,  "description": "Doubles the sheckle value"},
        "Diseased": {"multiplier": 0.5, "rarity": 0.1,   "description": "Reduces value by half"},
    },
    "specific": {
        "Carrot": {
            "Perfect":       {"multiplier": 10.0, "rarity": 0.001, "description": "The ultimate carrot", "priority": True},
            "Bunny's Favorite": {"multiplier": 3.0, "rarity": 0.02, "description": "Loved by rabbits"},
        },
        "Ember Lily": {"Inferno": {"multiplier": 3.5, "rarity": 0.03, "description": "Burns with eternal flame"}},
        "Beanstalk":  {"Skyreach": {"multiplier": 4.0, "rarity": 0.015, "description": "Reaches the clouds"}},
    },
}
limited_seeds      = {}
fertilizers = {
    "Growth Boost":   {"cost": 50,  "description": "Makes plants grow 25% faster for 1 hour", "effect": {"type": "growth",   "multiplier": 0.75, "duration": 3600}},
    "Mutation Boost": {"cost": 100, "description": "Doubles mutation chances for 1 hour",      "effect": {"type": "mutation", "multiplier": 2.0,  "duration": 3600}},
}
achievement_definitions = {
    "First Seed":        {"condition": lambda uid: len(user_inventory[uid]["grown"]) > 0,                                     "description": "Grow your first plant"},
    "Mutation Master":   {"condition": lambda uid: any(s.mutation for s in user_inventory[uid]["grown"]),                     "description": "Grow a mutated plant"},
    "Legendary Gardener":{"condition": lambda uid: any(s.name in ["Sugar Apple","Beanstalk"] for s in user_inventory[uid]["grown"]), "description": "Grow a legendary plant"},
}


# ============================================================
# GARDEN HELPERS
# ============================================================

class GrowingSeed:
    def __init__(self, name, grow_duration, mutation=None, limited=False, allowed_mutations=None):
        self.name        = name
        self.finish_time = time.time() + grow_duration
        self.limited     = limited
        self.mutation    = mutation or self._determine_mutation(name, allowed_mutations)

    def _determine_mutation(self, plant_name, allowed_mutations=None):
        if allowed_mutations is not None:
            for mut in allowed_mutations:
                pool = mutations["global"] if mut in mutations["global"] else mutations["specific"].get(plant_name, {})
                if mut in pool and random.random() < pool[mut]["rarity"]:
                    return mut
            return None
        if plant_name in mutations["specific"]:
            for mut, data in mutations["specific"][plant_name].items():
                if data.get("priority") and random.random() < data["rarity"]:
                    return mut
            for mut, data in mutations["specific"][plant_name].items():
                if mut != "Perfect" and random.random() < data["rarity"]:
                    return mut
        for mut, data in mutations["global"].items():
            if random.random() < data["rarity"]:
                return mut
        return None


def calculate_grow_time(base_seed, user_id):
    grow_time = 300
    if base_seed in current_season["boosted_seeds"]:
        grow_time *= 0.8
    if current_plant_event:
        if current_plant_event["effect"] == "delay":
            grow_time += current_plant_event["delay"]
        elif current_plant_event["effect"] == "speed":
            grow_time *= current_plant_event["multiplier"]
    boost = user_active_boosts.get(user_id, {}).get("growth_boost")
    if boost and time.time() < boost["expires"]:
        grow_time *= boost["multiplier"]
    return max(30, grow_time)


def update_growing_seeds(user_id):
    now     = time.time()
    growing = user_inventory[user_id]["growing"]
    done    = [s for s in growing if s.finish_time <= now]
    for s in done:
        user_inventory[user_id]["grown"].append(s)
    user_inventory[user_id]["growing"] = [s for s in growing if s.finish_time > now]


def check_achievements(user_id):
    new = []
    for name, data in achievement_definitions.items():
        if name not in user_achievements[user_id] and data["condition"](user_id):
            user_achievements[user_id].append(name)
            new.append(name)
    return new


def pretty_seed(seed_obj):
    name = seed_obj.name
    if seed_obj.mutation:
        name += f" ({seed_obj.mutation})"
    if getattr(seed_obj, "limited", False):
        name += " 🌟(Limited)"
    return name


def normalize_seed_name(raw: str):
    raw = raw.strip()
    m   = re.match(r'^(.*?)(?:\((.*?)\))?$', raw)
    base = ' '.join(w.capitalize() for w in m.group(1).split()).strip()
    mut  = m.group(2)
    if mut:
        mut = ' '.join(w.capitalize() for w in mut.split()).strip()
    return base, mut, f"{base} ({mut})" if mut else base


def find_matching_seed(seed_list, desired_input):
    base, mut, _ = normalize_seed_name(desired_input)
    for s in seed_list:
        if s.name != base:
            continue
        if mut is None or (s.mutation and s.mutation.lower() == mut.lower()):
            return s
    return None


# ============================================================
# TICKET / MIDDLEMAN SYSTEM
# ============================================================

class CloseTicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="❌ Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not any(role.id == MIDDLEMAN_ROLE_ID for role in interaction.user.roles):
            return await interaction.response.send_message("❌ Only middlemen can close tickets.", ephemeral=True)
        await interaction.response.send_message("✅ Closing ticket...", ephemeral=True)
        await interaction.channel.delete(reason="Ticket closed by middleman")


class MiddlemanModal(discord.ui.Modal, title="Apply for Middleman"):
    confirm = discord.ui.TextInput(
        label="PINGING A MIDDLEMAN WILL RESULT IN BLACKLIST!",
        placeholder="In order to make a ticket say 'Yes I understand.'",
        min_length=2, max_length=32
    )
    trader_info = discord.ui.TextInput(
        label="UserID and Username of the other trader",
        placeholder="someone & 1234567890"
    )
    private_server = discord.ui.TextInput(label="Can both traders join Private servers?", placeholder="YES/NO")
    ready_status   = discord.ui.TextInput(label="Are BOTH traders ready?", placeholder="YES/NO")
    trade_details  = discord.ui.TextInput(
        label="What are you GIVING and RECEIVING?",
        placeholder="GIVING some random plant. RECEIVING some random plant.",
        style=discord.TextStyle.paragraph
    )

    def __init__(self, bot_instance, interaction):
        super().__init__()
        self.bot_instance  = bot_instance
        self.original_interaction = interaction

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip().lower() != "yes i understand.":
            blacklist_role = interaction.guild.get_role(BLACKLIST_ROLE_ID)
            if blacklist_role:
                await interaction.user.add_roles(blacklist_role, reason="Failed to confirm MM rules")
            return await interaction.response.send_message("🚫 You failed to confirm properly and were blacklisted.", ephemeral=True)

        guild = interaction.guild
        ticket_category = guild.get_channel(TICKET_CATEGORY_ID)
        if not ticket_category or not isinstance(ticket_category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ Ticket category not found.", ephemeral=True)

        middleman_role = guild.get_role(MIDDLEMAN_ROLE_ID)
        if not middleman_role:
            return await interaction.response.send_message("❌ Middleman role not found.", ephemeral=True)

        ticket_number = len([c for c in ticket_category.channels if isinstance(c, discord.TextChannel) and c.name.startswith("ticket-")]) + 1
        overwrites = {
            guild.default_role:  discord.PermissionOverwrite(read_messages=False),
            interaction.user:    discord.PermissionOverwrite(read_messages=True, send_messages=True),
            middleman_role:      discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_permissions=True),
            guild.me:            discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True),
        }

        try:
            ticket_channel = await ticket_category.create_text_channel(
                name=f"ticket-{ticket_number}", overwrites=overwrites, reason="Middleman ticket"
            )
        except Exception as e:
            print(f"Error creating ticket channel: {e}")
            return await interaction.response.send_message("❌ Failed to create ticket channel.", ephemeral=True)

        embed = discord.Embed(title="📨 Middleman Ticket Application", color=discord.Color.green())
        embed.add_field(name="Trader Info",           value=self.trader_info.value,    inline=False)
        embed.add_field(name="Private Server Access", value=self.private_server.value, inline=False)
        embed.add_field(name="Ready Status",          value=self.ready_status.value,   inline=False)
        embed.add_field(name="Trade Details",         value=self.trade_details.value,  inline=False)
        embed.set_footer(text=f"Ticket opened by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)

        try:
            await ticket_channel.send(content=middleman_role.mention, embed=embed, view=CloseTicketView(self.bot_instance))
            await interaction.response.send_message(f"✅ Ticket created: {ticket_channel.mention}", ephemeral=True)
        except Exception as e:
            print(f"Error sending ticket message: {e}")
            await interaction.response.send_message("❌ Ticket created but failed to send details.", ephemeral=True)


# ============================================================
# TRADING SYSTEM
# ============================================================

def remove_trade_offer(sender_id, recipient_id, sender_seed_name, recipient_seed_name):
    trade_offers[recipient_id] = [
        o for o in trade_offers[recipient_id]
        if not (
            o["sender_id"] == sender_id
            and o["sender_seed_name"] == sender_seed_name
            and o["recipient_seed_name"] == recipient_seed_name
        )
    ]


class TradeView(View):
    def __init__(self, sender, recipient, sender_seed, recipient_seed, original_message=None, viewer=None, trade_messages=None):
        super().__init__(timeout=300)
        self.sender          = sender
        self.recipient       = recipient
        self.sender_seed     = sender_seed
        self.recipient_seed  = recipient_seed
        self.original_message = original_message
        self.viewer          = viewer
        self.trade_messages  = trade_messages or []

        if viewer and viewer.id != sender.id:
            for item in list(self.children):
                if isinstance(item, discord.ui.Button) and item.label == "Cancel Trade":
                    self.remove_item(item)

    async def _disable_all(self, interaction: discord.Interaction, embed: discord.Embed):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.edit_original_response(embed=embed, view=self)
        except Exception:
            try:
                await interaction.followup.send("✅ Trade action completed!", ephemeral=True)
            except Exception:
                pass

        current_id = interaction.message.id if hasattr(interaction, "message") else None
        for msg_id in self.trade_messages:
            if msg_id == current_id:
                continue
            try:
                ch  = interaction.channel
                msg = await ch.fetch_message(msg_id)
                dv  = TradeView(self.sender, self.recipient, self.sender_seed, self.recipient_seed, viewer=self.viewer)
                for item in dv.children:
                    if isinstance(item, discord.ui.Button):
                        item.disabled = True
                await msg.edit(embed=embed, view=dv)
            except Exception:
                pass

    @discord.ui.button(label="Accept Trade", style=ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.recipient.id:
            return await safe_send_ephemeral(interaction, "❌ This trade isn't for you!")

        update_growing_seeds(self.sender.id)
        update_growing_seeds(self.recipient.id)

        sender_seed    = next((s for s in user_inventory[self.sender.id]["grown"]    if s.name == self.sender_seed.name    and s.mutation == self.sender_seed.mutation),    None)
        recipient_seed = next((s for s in user_inventory[self.recipient.id]["grown"] if s.name == self.recipient_seed.name and s.mutation == self.recipient_seed.mutation), None)

        if not sender_seed or not recipient_seed:
            remove_trade_offer(self.sender.id, self.recipient.id, self.sender_seed.name, self.recipient_seed.name)
            return await safe_send_ephemeral(interaction, "❌ One or both seeds no longer available.")

        user_inventory[self.sender.id]["grown"].remove(sender_seed)
        user_inventory[self.recipient.id]["grown"].remove(recipient_seed)
        user_inventory[self.sender.id]["grown"].append(recipient_seed)
        user_inventory[self.recipient.id]["grown"].append(sender_seed)

        trade_logs_list.append({"from": self.sender.id, "to": self.recipient.id, "gave": sender_seed.name, "got": recipient_seed.name, "time": time.time()})
        remove_trade_offer(self.sender.id, self.recipient.id, self.sender_seed.name, self.recipient_seed.name)

        embed = discord.Embed(
            title="✅ Trade Completed",
            description=f"{self.sender.mention} gave {pretty_seed(sender_seed)}\n{self.recipient.mention} gave {pretty_seed(recipient_seed)}",
            color=discord.Color.green()
        )
        await self._disable_all(interaction, embed)

        for user, received, gave in [(self.sender, recipient_seed, sender_seed), (self.recipient, sender_seed, recipient_seed)]:
            try:
                await user.send(f"✅ Trade completed!\nYou received: {pretty_seed(received)}\nYou gave: {pretty_seed(gave)}")
            except Exception:
                pass

    @discord.ui.button(label="Decline Trade", style=ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.recipient.id:
            return await safe_send_ephemeral(interaction, "❌ This trade isn't for you!")

        remove_trade_offer(self.sender.id, self.recipient.id, self.sender_seed.name, self.recipient_seed.name)
        embed = discord.Embed(title="❌ Trade Declined", description=f"{self.recipient.mention} declined the trade from {self.sender.mention}", color=discord.Color.red())
        await self._disable_all(interaction, embed)
        try:
            await self.sender.send(f"❌ {self.recipient.mention} declined your trade offer.")
        except Exception:
            pass

    @discord.ui.button(label="Cancel Trade", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.sender.id:
            return await safe_send_ephemeral(interaction, "❌ Only the sender can cancel this trade.")

        remove_trade_offer(self.sender.id, self.recipient.id, self.sender_seed.name, self.recipient_seed.name)
        embed = discord.Embed(title="🚫 Trade Cancelled", description=f"{self.sender.mention} cancelled the trade offer.", color=discord.Color.red())
        await self._disable_all(interaction, embed)
        try:
            await self.recipient.send(f"🚫 {self.sender.mention} cancelled their trade offer.")
        except Exception:
            pass


# ============================================================
# GIVEAWAY SYSTEM
# ============================================================

class Giveaway:
    def __init__(self, hoster, prize, winners, number_range, target, duration, channel):
        self.hoster           = hoster
        self.prize            = prize
        self.winners_required = winners
        self.low, self.high   = number_range
        self.target           = target
        self.duration         = duration
        self.duration_minutes = duration
        self.channel          = channel
        self.winners          = set()
        self.participants     = set()
        self.guessed_users    = {}
        self.user_guesses     = {}
        self.end_time         = time.time() + (duration * 60) if duration > 0 else None
        self.task             = None
        self.view             = None

    def check_guess(self, user, guess):
        if self.end_time and time.time() > self.end_time:
            return None
        if user.id == self.hoster.id:
            return None
        self.participants.add(user)
        self.guessed_users.setdefault(user.id, []).append(guess)
        return guess == self.target


def create_giveaway_embed(giveaway: Giveaway) -> discord.Embed:
    embed = discord.Embed(title="🎉 NUMBER GUESS GIVEAWAY 🎉", description=f"Hosted by {giveaway.hoster.mention}", color=discord.Color.gold())
    embed.add_field(name="🏆 Prize",         value=giveaway.prize,                inline=False)
    embed.add_field(name="🔢 Number Range",  value=f"{giveaway.low}-{giveaway.high}", inline=True)
    embed.add_field(name="🎯 Winners Needed",value=str(giveaway.winners_required), inline=True)
    if giveaway.duration_minutes > 0 and giveaway.end_time:
        remaining = max(0, giveaway.end_time - time.time())
        mins, secs = divmod(int(remaining), 60)
        embed.add_field(name="⏳ Time Remaining", value=f"{mins}m {secs}s", inline=True)
    else:
        embed.add_field(name="⏳ Duration", value="No time limit", inline=True)
    embed.add_field(name="👥 Participants",  value=f"{len(giveaway.participants)} joined", inline=True)
    embed.add_field(name="🏆 Winners Found", value=f"{len(giveaway.winners)}/{giveaway.winners_required}", inline=True)
    embed.set_footer(text="Click 'Join Giveaway' to participate!", icon_url=giveaway.hoster.display_avatar.url)
    return embed


class GuessModal(discord.ui.Modal, title="Enter Your Guess"):
    guess = discord.ui.TextInput(label="Your guess", placeholder="Enter a number between X and Y")

    def __init__(self, giveaway: Giveaway):
        super().__init__()
        self.giveaway = giveaway

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id == self.giveaway.hoster.id:
            return await interaction.response.send_message("❌ You can't participate in your own giveaway!", ephemeral=True)
        if self.giveaway.end_time and time.time() > self.giveaway.end_time:
            return await interaction.response.send_message("❌ This giveaway has already ended!", ephemeral=True)
        try:
            guess = int(self.guess.value)
            if guess < self.giveaway.low or guess > self.giveaway.high:
                return await interaction.response.send_message(f"❌ Guess must be between {self.giveaway.low}-{self.giveaway.high}!", ephemeral=True)

            self.giveaway.user_guesses.setdefault(interaction.user.id, []).append(guess)

            if self.giveaway.check_guess(interaction.user, guess):
                self.giveaway.winners.add(interaction.user)
                await interaction.response.send_message(f"🎉 You guessed the correct number `{guess}`!", ephemeral=True)
                if len(self.giveaway.winners) >= self.giveaway.winners_required:
                    await end_giveaway(self.giveaway)
            else:
                await interaction.response.send_message("❌ That's not the correct number. Try again!", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid number!", ephemeral=True)


class ParticipantsView(discord.ui.View):
    def __init__(self, giveaway, participants, current_page, total_pages, original_view=None):
        super().__init__(timeout=60)
        self.giveaway      = giveaway
        self.participants  = participants
        self.current_page  = current_page
        self.total_pages   = total_pages
        self.original_view = original_view
        self.prev_button.disabled = current_page == 0
        self.next_button.disabled = current_page >= total_pages - 1

    def _make_embed(self):
        start = self.current_page * 10
        chunk = self.participants[start:start + 10]
        embed = discord.Embed(title="👥 Giveaway Participants", description="\n".join(f"{i+1}. {p.mention}" for i, p in enumerate(chunk, start=start)), color=discord.Color.blue())
        embed.set_footer(text=f"Page {self.current_page+1} of {self.total_pages}")
        return embed

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1
        await interaction.response.edit_message(embed=self._make_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1
        await interaction.response.edit_message(embed=self._make_embed(), view=self)


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway: Giveaway):
        super().__init__(timeout=None)
        self.giveaway = giveaway
        self.message  = None

    def disable_expired_buttons(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "join_giveaway":
                item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.message is None:
            self.message = interaction.message
        return True

    @discord.ui.button(label="Join Giveaway", style=discord.ButtonStyle.green, custom_id="join_giveaway")
    async def join_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.giveaway.end_time and time.time() > self.giveaway.end_time:
            return await interaction.response.send_message("❌ This giveaway has already ended!", ephemeral=True)
        await interaction.response.send_modal(GuessModal(self.giveaway))

    @discord.ui.button(label="Participants", style=discord.ButtonStyle.blurple, custom_id="view_participants")
    async def view_participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        participants = list(self.giveaway.participants)
        if not participants:
            return await interaction.response.send_message("No participants yet!", ephemeral=True)
        total_pages = max(1, (len(participants) + 9) // 10)
        view  = ParticipantsView(self.giveaway, participants, 0, total_pages, self)
        start = participants[:10]
        embed = discord.Embed(title="👥 Giveaway Participants", description="\n".join(f"{i+1}. {p.mention}" for i, p in enumerate(start)), color=discord.Color.blue())
        embed.set_footer(text=f"Page 1 of {total_pages}")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="My Guesses", style=discord.ButtonStyle.secondary, custom_id="my_guesses")
    async def my_guesses(self, interaction: discord.Interaction, button: discord.ui.Button):
        guesses = self.giveaway.user_guesses.get(interaction.user.id, [])
        if not guesses:
            return await interaction.response.send_message("You haven't made any guesses yet!", ephemeral=True)
        await interaction.response.send_message(f"📋 Your guesses: `{', '.join(map(str, guesses))}`", ephemeral=True)


async def end_giveaway(giveaway: Giveaway):
    if giveaway.task:
        giveaway.task.cancel()

    if giveaway.view:
        giveaway.view.disable_expired_buttons()
        try:
            if giveaway.view.message:
                await giveaway.view.message.edit(view=giveaway.view)
        except Exception:
            pass

    for winner in giveaway.winners:
        try:
            await winner.send(f"🎉 You won the giveaway in {giveaway.channel.mention}!\n**Prize:** {giveaway.prize}\nContact {giveaway.hoster.mention} to claim your reward!")
        except discord.Forbidden:
            try:
                await giveaway.channel.send(f"{winner.mention} won but has DMs closed! Please contact {giveaway.hoster.mention}.")
            except Exception:
                pass

    winners_text = ", ".join(w.mention for w in giveaway.winners) if giveaway.winners else "No winners"
    embed = discord.Embed(
        title="🎉 Giveaway Ended",
        description=f"**Prize:** {giveaway.prize}\n**Target Number:** ||{giveaway.target}||\n**Winners:** {winners_text}\n**Total Participants:** {len(giveaway.guessed_users)}",
        color=discord.Color.green()
    )

    try:
        await giveaway.channel.send(embed=embed)
        await giveaway.channel.set_permissions(giveaway.channel.guild.default_role, send_messages=False)
        await giveaway.channel.edit(slowmode_delay=0)
    except Exception:
        pass

    active_giveaways.pop(giveaway.channel.id, None)
    giveaway_logs.append({"channel_id": giveaway.channel.id, "hoster_id": giveaway.hoster.id, "prize": giveaway.prize, "winners": [w.id for w in giveaway.winners], "end_time": time.time()})


async def schedule_giveaway_end(giveaway: Giveaway):
    await asyncio.sleep(giveaway.duration * 60)
    if giveaway.channel.id in active_giveaways:
        await end_giveaway(giveaway)


# ============================================================
# LEAKS UI — pagination helpers
# ============================================================

PAGE_SIZE        = 15
SEARCH_PAGE_SIZE = 10


def build_list_embed(rows, page: int, leak_type: Optional[str] = None) -> discord.Embed:
    total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    chunk = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    type_label = f" ({leak_type.capitalize()})" if leak_type else ""
    embed = discord.Embed(
        title=f"📦 All Available Leaks{type_label}",
        description="\n".join(f"• **{r['name']}**" for r in chunk),
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Page {page + 1} of {total_pages} | {len(rows)} total leaks | Use /leaks <name> to get links")
    return embed


def build_search_embed(results, query: str, page: int) -> discord.Embed:
    total_pages = max(1, (len(results) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
    chunk = results[page * SEARCH_PAGE_SIZE:(page + 1) * SEARCH_PAGE_SIZE]
    embed = discord.Embed(title=f"🔍 Results for \"{query}\"", description="Multiple matches found. Be more specific to see Payhip links.", color=discord.Color.blurple())
    for row in chunk:
        type_badge = "🌟 Booster" if row["type"] == "booster" else "📦 Normal"
        embed.add_field(name=f"{row['name']} [{type_badge}]", value=f"[Download Link]({row['link']})", inline=False)
    embed.set_footer(text=f"Page {page + 1} of {total_pages} | {len(results)} results")
    return embed


class LeaksListView(discord.ui.View):
    def __init__(self, rows, page: int = 0, leak_type: Optional[str] = None):
        super().__init__(timeout=None)
        self.rows        = rows
        self.page        = page
        self.leak_type   = leak_type
        self.total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.gray)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=build_list_embed(self.rows, self.page, self.leak_type), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.gray)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=build_list_embed(self.rows, self.page, self.leak_type), view=self)


class LeaksSearchView(discord.ui.View):
    def __init__(self, results, query: str, page: int = 0):
        super().__init__(timeout=None)
        self.results     = results
        self.query       = query
        self.page        = page
        self.total_pages = max(1, (len(results) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.gray)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=build_search_embed(self.results, self.query, self.page), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.gray)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=build_search_embed(self.results, self.query, self.page), view=self)


class ImportModeView(discord.ui.View):
    def __init__(self, admin_id: int, channel_id: int):
        super().__init__(timeout=60)
        self.admin_id   = admin_id
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.admin_id:
            await interaction.response.send_message("❌ This isn't your import.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Merge", style=discord.ButtonStyle.green)
    async def merge(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending_imports[self.admin_id] = {"mode": "merge", "channel_id": self.channel_id}
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="✅ Mode set to **Merge**. Now send the `.json` file in this channel.", view=self)

    @discord.ui.button(label="Replace", style=discord.ButtonStyle.red)
    async def replace(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending_imports[self.admin_id] = {"mode": "replace", "channel_id": self.channel_id}
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="✅ Mode set to **Replace** (⚠️ this will wipe the current database). Now send the `.json` file in this channel.", view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ============================================================
# RATING / FEEDBACK SYSTEM
# ============================================================

def calculate_average(votes: dict) -> float:
    if not votes:
        return 0.0
    return sum(v["score"] for v in votes.values()) / len(votes)


def build_rating_embed(submission: dict) -> discord.Embed:
    votes     = submission["votes"]
    avg       = calculate_average(votes)
    vote_count = len(votes)
    stars     = "⭐" * round(avg) if avg > 0 else "No ratings yet"

    booster_badge = " 🌟 **[SERVER BOOSTER]**" if submission.get("is_booster") else ""
    embed = discord.Embed(title=f"🎬 {submission['display_name']}'s Edit{booster_badge}", color=discord.Color.gold())
    embed.add_field(name="🔗 Video Link", value=f"[Click to watch]({submission['link']})", inline=False)
    embed.add_field(name="⭐ Rating", value=f"{stars}\n**{avg:.1f}/10** from **{vote_count}** vote{'s' if vote_count != 1 else ''}", inline=True)

    comments = [f"**{v['rater_name']}** ({v['score']}/10): {v['comment']}" for v in votes.values() if v.get("comment")]
    if comments:
        embed.add_field(name="💬 Recent Comments", value="\n".join(comments[-3:]), inline=False)

    embed.set_footer(text="Click ⭐ Rate to vote • Click 💬 Feedback for detailed feedback")
    return embed


class RateModal(discord.ui.Modal, title="Rate This Edit"):
    score   = discord.ui.TextInput(label="Score (1-10)", placeholder="Enter a number from 1 to 10", min_length=1, max_length=2)
    comment = discord.ui.TextInput(label="Comment (optional)", placeholder="What did you think?", required=False, style=discord.TextStyle.paragraph, max_length=300)

    def __init__(self, submission_id: int):
        super().__init__()
        self.submission_id = submission_id

    async def on_submit(self, interaction: discord.Interaction):
        submission = rating_submissions.get(self.submission_id)
        if not submission:
            return await interaction.response.send_message("❌ This submission no longer exists.", ephemeral=True)

        try:
            score = int(self.score.value.strip())
            if not 1 <= score <= 10:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ Score must be a number between 1 and 10.", ephemeral=True)

        if interaction.user.id == submission["submitter_id"]:
            return await interaction.response.send_message("❌ You can't rate your own edit!", ephemeral=True)

        submission["votes"][interaction.user.id] = {
            "score":      score,
            "comment":    self.comment.value.strip() if self.comment.value else "",
            "rater_name": interaction.user.display_name,
        }

        try:
            channel = interaction.guild.get_channel(submission["rating_channel_id"])
            if channel:
                msg  = await channel.fetch_message(submission["rating_message_id"])
                view = RatingView(self.submission_id)
                await msg.edit(embed=build_rating_embed(submission), view=view)
        except Exception as e:
            print(f"Error updating rating embed: {e}")

        await interaction.response.send_message(f"✅ Rated **{submission['display_name']}'s** edit **{score}/10**!", ephemeral=True)


class FeedbackModal(discord.ui.Modal, title="Give Feedback"):
    feedback = discord.ui.TextInput(label="Your Feedback", placeholder="Be constructive! What worked, what could be improved?", style=discord.TextStyle.paragraph, min_length=10, max_length=1000)

    def __init__(self, submission_id: int):
        super().__init__()
        self.submission_id = submission_id

    async def on_submit(self, interaction: discord.Interaction):
        submission = rating_submissions.get(self.submission_id)
        if not submission:
            return await interaction.response.send_message("❌ This submission no longer exists.", ephemeral=True)

        feedback_channel = interaction.guild.get_channel(FEEDBACK_CHANNEL_ID)
        if not feedback_channel:
            return await interaction.response.send_message("❌ Feedback channel not found.", ephemeral=True)

        avg   = calculate_average(submission["votes"])
        embed = discord.Embed(title=f"💬 Feedback for {submission['display_name']}'s Edit", description=self.feedback.value, color=discord.Color.blurple())
        embed.add_field(name="🔗 Video",   value=f"[Watch here]({submission['link']})", inline=True)
        embed.add_field(name="📝 From",    value=interaction.user.mention,               inline=True)
        if avg > 0:
            embed.add_field(name="⭐ Current Rating", value=f"{avg:.1f}/10", inline=True)
        embed.set_footer(text=f"Submitted by {submission['display_name']}")

        submitter = interaction.guild.get_member(submission["submitter_id"])
        ping      = submitter.mention if submitter else f"<@{submission['submitter_id']}>"
        await feedback_channel.send(content=f"{ping} you received new feedback!", embed=embed)
        await interaction.response.send_message("✅ Feedback submitted!", ephemeral=True)


class RatingView(discord.ui.View):
    def __init__(self, submission_id: int):
        super().__init__(timeout=None)
        self.submission_id = submission_id

    @discord.ui.button(label="⭐ Rate", style=discord.ButtonStyle.green, custom_id="rate_button")
    async def rate(self, interaction: discord.Interaction, button: discord.ui.Button):
        submission = rating_submissions.get(self.submission_id)
        if not submission:
            return await interaction.response.send_message("❌ This submission no longer exists.", ephemeral=True)
        if interaction.user.id == submission["submitter_id"]:
            return await interaction.response.send_message("❌ You can't rate your own edit!", ephemeral=True)
        await interaction.response.send_modal(RateModal(self.submission_id))

    @discord.ui.button(label="💬 Feedback", style=discord.ButtonStyle.blurple, custom_id="feedback_button")
    async def feedback(self, interaction: discord.Interaction, button: discord.ui.Button):
        submission = rating_submissions.get(self.submission_id)
        if not submission:
            return await interaction.response.send_message("❌ This submission no longer exists.", ephemeral=True)
        await interaction.response.send_modal(FeedbackModal(self.submission_id))


class SubmitEditModal(discord.ui.Modal, title="Submit Your Edit"):
    display_name = discord.ui.TextInput(label="Display Name", placeholder="How should you appear? (e.g. kyrnx)", max_length=50)
    link         = discord.ui.TextInput(label="Video Link",   placeholder="https://www.tiktok.com/... or YouTube, Drive, etc.", max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        rating_channel = interaction.guild.get_channel(RATING_CHANNEL_ID)
        if not rating_channel:
            return await interaction.response.send_message("❌ Rating channel not found.", ephemeral=True)

        submitter_is_booster = is_booster(interaction.user)

        submission = {
            "submitter_id":     interaction.user.id,
            "display_name":     self.display_name.value.strip(),
            "link":             self.link.value.strip(),
            "votes":            {},
            "rating_message_id": None,
            "rating_channel_id": RATING_CHANNEL_ID,
            "is_booster":       submitter_is_booster,
        }

        temp_id = interaction.id
        rating_submissions[temp_id] = submission

        embed = build_rating_embed(submission)
        view  = RatingView(temp_id)
        msg   = await rating_channel.send(embed=embed, view=view)

        submission["rating_message_id"] = msg.id
        rating_submissions[msg.id] = submission
        del rating_submissions[temp_id]

        view2 = RatingView(msg.id)
        await msg.edit(view=view2)

        await interaction.response.send_message(f"✅ Your edit has been submitted to {rating_channel.mention}!", ephemeral=True)

        # If the submitter is a booster, ping admins to review
        if submitter_is_booster:
            admin_pings = " ".join(f"<@&{rid}>" for rid in ADMIN_ROLE_IDS)
            await rating_channel.send(
                f"🌟 **Booster submission!** {admin_pings} — please review {interaction.user.mention}'s edit when you get a chance!",
                delete_after=300
            )


class SubmitButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎬 Submit Your Edit", style=discord.ButtonStyle.green, custom_id="submit_edit_button")
    async def submit_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SubmitEditModal())


# ============================================================
# BOT EVENTS
# ============================================================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    bot.add_view(CloseTicketView(bot))
    bot.add_view(SubmitButtonView())
    for giveaway in active_giveaways.values():
        if giveaway.view and giveaway.view.message:
            bot.add_view(giveaway.view, message_id=giveaway.view.message.id)
    cleanup_expired.start()
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Failed to sync: {e}")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # ── JSON import handler ──────────────────────────────────────────────────
    if message.author.id in pending_imports and message.attachments:
        pending = pending_imports[message.author.id]
        if message.channel.id == pending["channel_id"]:
            attachment = next((a for a in message.attachments if a.filename.endswith(".json")), None)
            if attachment:
                del pending_imports[message.author.id]
                try:
                    raw  = await attachment.read()
                    data = json.loads(raw.decode("utf-8"))

                    if not isinstance(data, list):
                        await message.reply("❌ Invalid format — expected a JSON array.")
                        return

                    mode  = pending["mode"]
                    added = skipped = 0

                    with get_db() as conn:
                        if mode == "replace":
                            conn.execute("DELETE FROM leaks")
                            conn.commit()

                        for entry in data:
                            try:
                                # Support both old format (no 'type') and new format (with 'type')
                                leak_type = entry.get("type", "normal").lower().strip()
                                if leak_type not in ("normal", "booster"):
                                    leak_type = "normal"

                                conn.execute(
                                    "INSERT INTO leaks (name, link, payhip_url, type, added_by, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                                    (
                                        entry["name"].strip(),
                                        entry["link"].strip(),
                                        entry["payhip_url"].strip(),
                                        leak_type,
                                        entry.get("added_by", message.author.id),
                                        entry.get("added_at", time.time()),
                                    )
                                )
                                added += 1
                            except (sqlite3.IntegrityError, KeyError):
                                skipped += 1
                        conn.commit()

                    embed = discord.Embed(title="✅ Import Complete", color=discord.Color.green())
                    embed.add_field(name="Mode",    value=mode.capitalize(), inline=True)
                    embed.add_field(name="Added",   value=str(added),        inline=True)
                    embed.add_field(name="Skipped", value=str(skipped),      inline=True)
                    if skipped > 0:
                        embed.set_footer(text="Skipped entries already exist or had missing fields.")
                    await message.reply(embed=embed)
                    return

                except (json.JSONDecodeError, UnicodeDecodeError):
                    await message.reply("❌ Couldn't parse the file — make sure it's a valid JSON file.")
                    return

    # ── Sheckle counting ─────────────────────────────────────────────────────
    user_message_counts[message.author.id] += 1
    if user_message_counts[message.author.id] % MESSAGES_PER_SHECKLE == 0:
        user_sheckles[message.author.id] += 1

    # ── Giveaway message handling ─────────────────────────────────────────────
    current_giveaway = active_giveaways.get(message.channel.id)
    if current_giveaway:
        if current_giveaway.end_time and time.time() > current_giveaway.end_time:
            try:
                await message.delete()
            except Exception:
                pass
            return

        if message.content.strip().isdigit():
            try:
                guess = int(message.content.strip())
                if guess < current_giveaway.low or guess > current_giveaway.high:
                    try:
                        await message.reply(f"❌ Guess must be between {current_giveaway.low}-{current_giveaway.high}!", delete_after=5)
                    except Exception:
                        pass
                    return

                if current_giveaway.check_guess(message.author, guess):
                    current_giveaway.winners.add(message.author)
                    try:
                        await message.author.send(f"🎉 You guessed the correct number `{guess}`!\nPlease contact {current_giveaway.hoster.mention} to claim your prize.")
                    except Exception:
                        await message.channel.send(f"🎉 {message.author.mention} guessed correctly but has DMs closed. Please contact the host!")
                    if len(current_giveaway.winners) >= current_giveaway.winners_required:
                        await end_giveaway(current_giveaway)

                try:
                    await message.add_reaction("🔢")
                except Exception:
                    pass
            except ValueError:
                pass
        else:
            try:
                await message.delete()
            except Exception:
                pass
            return

    await bot.process_commands(message)


# ============================================================
# GIVEAWAY COMMANDS
# ============================================================

@tree.command(name="giveaway", description="Start a number guessing giveaway")
@app_commands.describe(
    prize="Prize for the giveaway",
    author="User hosting the giveaway",
    number_range="Range for guessing (e.g. 1-100)",
    duration="How long the giveaway lasts (e.g. 1m, 30s, 2h)",
    winners="Number of winners needed (default: 1)",
    target="Optional target user",
)
@app_commands.checks.has_any_role(*ADMIN_ROLE_IDS)
async def giveaway_cmd(
    interaction: discord.Interaction,
    prize: str,
    author: discord.User,
    number_range: str,
    duration: str,
    winners: Optional[int] = 1,
    target: Optional[discord.User] = None,
):
    if interaction.channel.id != GIVEAWAY_CHANNEL_ID:
        return await interaction.response.send_message("This command can only be used in the giveaway channel.", ephemeral=True)
    if winners < 1:
        return await interaction.response.send_message("❌ Number of winners must be at least 1.", ephemeral=True)

    try:
        start, end = map(int, number_range.split("-"))
        if start >= end:
            raise ValueError
    except Exception:
        return await interaction.response.send_message("❌ Invalid number range. Use a format like `1-100`.", ephemeral=True)

    time_units = {"s": 1, "m": 60, "h": 3600}
    try:
        tu             = duration[-1]
        tv             = int(duration[:-1])
        duration_secs  = tv * time_units[tu]
        duration_mins  = duration_secs // 60
    except Exception:
        return await interaction.response.send_message("❌ Invalid duration. Use `30s`, `1m`, `2h` etc.", ephemeral=True)

    target_number = random.randint(start, end)
    ga = Giveaway(hoster=author, prize=prize, winners=winners, number_range=(start, end), target=target_number, duration=duration_mins, channel=interaction.channel)
    active_giveaways[interaction.channel.id] = ga

    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.channel.edit(slowmode_delay=2)

    embed = discord.Embed(title="🎁 NUMBER GUESS GIVEAWAY", description=f"**Host:** {author.mention}\n**Range:** {number_range}\n**Prize:** {prize}\n**Winners Needed:** {winners}\n**Duration:** {duration}\n\nClick the button below to enter your guess!", color=discord.Color.gold())
    if target:
        embed.add_field(name="🎯 Target Player", value=target.mention)

    view = GiveawayView(ga)
    await interaction.response.send_message(embed=embed, view=view)
    msg        = await interaction.original_response()
    view.message = msg
    ga.view    = view

    if duration_mins > 0:
        ga.task = asyncio.create_task(schedule_giveaway_end(ga))


@tree.command(name="stop_giveaway", description="Forcefully end the current giveaway in this channel")
@auto_defer(ephemeral=True)
async def stop_giveaway(interaction: discord.Interaction):
    ga = active_giveaways.get(interaction.channel.id)
    if not ga:
        return await interaction.followup.send("❌ No active giveaway in this channel!", ephemeral=True)
    if interaction.user.id != ga.hoster.id and not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Only the giveaway host or admins can stop this!", ephemeral=True)

    if ga.task:
        ga.task.cancel()

    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
    await interaction.channel.edit(slowmode_delay=0)
    active_giveaways.pop(interaction.channel.id, None)

    embed = discord.Embed(title="🎉 Giveaway Ended by Admin", color=discord.Color.orange())
    embed.add_field(name="Giveaway Details", value=f"**Host:** {ga.hoster.mention}\n**Range:** {ga.low}-{ga.high}\n**Prize:** {ga.prize}\n**Target Number:** ||{ga.target}||", inline=False)
    if ga.winners:
        embed.add_field(name=f"🏆 Winner{'s' if len(ga.winners) > 1 else ''}", value=", ".join(w.mention for w in ga.winners), inline=False)
    else:
        embed.add_field(name="❌ No Winners", value="No one guessed the correct number!", inline=False)

    await interaction.followup.send(embed=embed)


# ============================================================
# TRADING COMMANDS
# ============================================================

@tree.command(name="trade_offer", description="Offer a trade to another user")
@auto_defer(ephemeral=False)
@app_commands.describe(user="User to trade with", yourseed="Seed you're offering", theirseed="Seed you want")
async def trade_offer_cmd(interaction: discord.Interaction, user: discord.Member, yourseed: str, theirseed: str):
    update_growing_seeds(interaction.user.id)
    update_growing_seeds(user.id)

    sender_seed    = find_matching_seed(user_inventory[interaction.user.id]["grown"], yourseed)
    recipient_seed = find_matching_seed(user_inventory[user.id]["grown"], theirseed)

    if not sender_seed:
        return await interaction.followup.send("❌ You don't have that grown seed to offer.", ephemeral=True)
    if not recipient_seed:
        return await interaction.followup.send(f"❌ {user.mention} doesn't have that seed or it's still growing.", ephemeral=True)

    embed = discord.Embed(
        title="🔔 Trade Offer",
        description=f"{interaction.user.mention} wants to trade with {user.mention}!\n\n**{interaction.user.display_name} offers:** {pretty_seed(sender_seed)}\n**{user.display_name} would give:** {pretty_seed(recipient_seed)}",
        color=discord.Color.blue()
    )
    embed.set_footer(text="This trade offer will expire in 5 minutes")

    view = TradeView(interaction.user, user, sender_seed, recipient_seed)
    msg  = await interaction.channel.send(f"{user.mention}, you received a trade offer from {interaction.user.mention}!", embed=embed, view=view)
    view.original_message = msg

    trade_offers[user.id].append({
        "sender_id":          interaction.user.id,
        "sender_seed_name":   sender_seed.name,
        "sender_seed_mut":    sender_seed.mutation,
        "recipient_seed_name": recipient_seed.name,
        "recipient_seed_mut": recipient_seed.mutation,
        "timestamp":          time.time(),
        "original_message_id": msg.id,
        "trade_messages":     [msg.id],
    })

    try:
        await user.send(f"You received a trade offer from {interaction.user.mention}!\nThey're offering: {pretty_seed(sender_seed)}\nThey want: {pretty_seed(recipient_seed)}\nCheck {interaction.channel.mention} to respond!")
    except Exception:
        pass


@tree.command(name="trade_offers", description="View your pending trade offers")
@auto_defer(ephemeral=True)
async def view_trade_offers(interaction: discord.Interaction):
    user_id = interaction.user.id
    if not isinstance(trade_offers.get(user_id), list):
        trade_offers[user_id] = []

    now = time.time()
    trade_offers[user_id] = [o for o in trade_offers[user_id] if isinstance(o, dict) and now - o.get("timestamp", 0) <= 300]

    offers = trade_offers[user_id]
    if not offers:
        return await interaction.followup.send("📭 You have no pending trade offers.", ephemeral=True)

    shown = 0
    for offer in offers:
        try:
            sender         = await bot.fetch_user(offer["sender_id"])
            sender_seed    = next((s for s in user_inventory[offer["sender_id"]]["grown"]    if s.name == offer["sender_seed_name"]    and s.mutation == offer["sender_seed_mut"]),    None)
            recipient_seed = next((s for s in user_inventory[user_id]["grown"] if s.name == offer["recipient_seed_name"] and s.mutation == offer["recipient_seed_mut"]), None)
            if not sender_seed or not recipient_seed:
                continue

            embed = discord.Embed(title=f"🔁 Trade Offer from {sender.display_name}", description=f"**They offer:** {pretty_seed(sender_seed)}\n**They want:** {pretty_seed(recipient_seed)}\nSent <t:{int(offer['timestamp'])}:R>", color=discord.Color.blurple())

            original_message = None
            if "original_message_id" in offer:
                try:
                    original_message = await interaction.channel.fetch_message(offer["original_message_id"])
                except Exception:
                    pass

            view     = TradeView(sender=sender, recipient=interaction.user, sender_seed=sender_seed, recipient_seed=recipient_seed, original_message=original_message, viewer=interaction.user, trade_messages=offer.get("trade_messages", []))
            trade_msg = await interaction.channel.send(embed=embed, view=view)
            offer.setdefault("trade_messages", []).append(trade_msg.id)
            shown += 1
        except Exception as e:
            print(f"Error showing trade offer: {e}")

    if shown == 0:
        await interaction.followup.send("❌ No valid trade offers could be shown.", ephemeral=True)
    else:
        await interaction.followup.send(f"📬 Shown {shown} trade offer(s).", ephemeral=True)


@tree.command(name="trade_logs", description="View recent trade logs (admin only)")
@auto_defer(ephemeral=True)
async def trade_logs_cmd(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)
    if not trade_logs_list:
        return await interaction.followup.send("📭 No trade logs available.", ephemeral=True)

    embed = discord.Embed(title="📜 Recent Trade Logs", color=discord.Color.gold())
    for log in trade_logs_list[-10:][::-1]:
        from_user = await bot.fetch_user(log["from"])
        to_user   = await bot.fetch_user(log["to"])
        ts        = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(log["time"]))
        embed.add_field(name=f"{from_user.name} ➝ {to_user.name} @ {ts}", value=f"{from_user.name} gave {log['gave']}, got {log['got']}", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ============================================================
# MIDDLEMAN COMMAND
# ============================================================

@tree.command(name="apply_middleman", description="Apply for a middleman trade")
async def apply_middleman(interaction: discord.Interaction):
    blacklist_role = interaction.guild.get_role(BLACKLIST_ROLE_ID)
    if blacklist_role and blacklist_role in interaction.user.roles:
        return await interaction.response.send_message("🚫 You are blacklisted from using the middleman system.", ephemeral=True)
    await interaction.response.send_modal(MiddlemanModal(bot, interaction))


# ============================================================
# ADMIN — SEED & SHECKLE COMMANDS
# ============================================================

@tree.command(name="give_seed", description="Give a seed to a user (admin only)")
@auto_defer(ephemeral=True)
@app_commands.describe(user="User to give seed to", seed="Seed name")
async def give_seed(interaction: discord.Interaction, user: discord.Member, seed: str):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Not allowed", ephemeral=True)
    base, mut, _ = normalize_seed_name(seed)
    if base not in seeds and base not in limited_seeds:
        return await interaction.followup.send("❌ Invalid seed name.", ephemeral=True)
    grow_time    = calculate_grow_time(base, interaction.user.id)
    seed_obj     = GrowingSeed(base, grow_time, limited=base in limited_seeds)
    user_inventory[user.id]["growing"].append(seed_obj)
    await interaction.followup.send(f"✅ Gave {pretty_seed(seed_obj)} to {user.mention}")


@tree.command(name="give_sheckles", description="Give sheckles to a user (admin only)")
@auto_defer(ephemeral=True)
@app_commands.describe(user="User to give sheckles to", amount="Amount of sheckles")
async def give_sheckles(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Not allowed", ephemeral=True)
    user_sheckles[user.id] += amount
    await interaction.followup.send(f"✅ Gave {amount} sheckles to {user.mention}")


@tree.command(name="growinstant", description="Instantly grow a user's plant (admin only)")
@auto_defer(ephemeral=True)
@app_commands.describe(user="User whose plant to instantly grow", plant="Plant name")
async def growinstant(interaction: discord.Interaction, user: discord.Member, plant: str):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Not allowed", ephemeral=True)
    base, mut, _ = normalize_seed_name(plant)
    update_growing_seeds(user.id)
    match = next((s for s in user_inventory[user.id]["growing"] if s.name == base and (mut is None or s.mutation == mut)), None)
    if not match:
        return await interaction.followup.send(f"❌ No matching growing seed found for {user.mention}.", ephemeral=True)
    grown = GrowingSeed(match.name, 0, mutation=match.mutation, limited=getattr(match, "limited", False))
    user_inventory[user.id]["growing"].remove(match)
    user_inventory[user.id]["grown"].append(grown)
    await interaction.followup.send(f"🌱 Instantly grew {pretty_seed(grown)} for {user.mention}.", ephemeral=True)


# ============================================================
# LEAKS COMMANDS
# ============================================================

@tree.command(name="leakscreate", description="Add a new leak entry (admin only)")
@auto_defer(ephemeral=False)
@app_commands.describe(
    leak="Name of the leak",
    link="Download/access link",
    payhip="Payhip product link",
    type="Type: 'normal' or 'booster' (default: normal)",
)
async def leaks_create(interaction: discord.Interaction, leak: str, link: str, payhip: str, type: str = "normal"):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)

    type = type.lower().strip()
    if type not in ("normal", "booster"):
        return await interaction.followup.send("❌ Type must be `normal` or `booster`.", ephemeral=True)

    success = add_leak(leak, link, payhip, type, interaction.user.id)
    if not success:
        return await interaction.followup.send(f"❌ A leak named **{leak}** already exists. Use `/leaksdelete` first if you want to replace it.", ephemeral=True)

    type_badge = "🌟 Booster" if type == "booster" else "📦 Normal"
    embed = discord.Embed(title="✅ Leak Added", color=discord.Color.green())
    embed.add_field(name="Name",          value=leak,         inline=False)
    embed.add_field(name="Type",          value=type_badge,   inline=False)
    embed.add_field(name="Download Link", value=link,         inline=False)
    embed.add_field(name="Payhip Link",   value=payhip,       inline=False)
    embed.set_footer(text=f"Added by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


@tree.command(name="addtype", description="Set the type of an existing leak (admin only)")
@auto_defer(ephemeral=True)
@app_commands.describe(
    leak="Exact name of the leak to update",
    type="New type: 'normal' or 'booster'",
)
async def add_type(interaction: discord.Interaction, leak: str, type: str):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)

    type = type.lower().strip()
    if type not in ("normal", "booster"):
        return await interaction.followup.send("❌ Type must be `normal` or `booster`.", ephemeral=True)

    success = update_leak_type(leak, type)
    if not success:
        return await interaction.followup.send(f"❌ No leak found with the name **{leak}**.", ephemeral=True)

    type_badge = "🌟 Booster" if type == "booster" else "📦 Normal"
    embed = discord.Embed(title="✅ Leak Type Updated", color=discord.Color.yellow())
    embed.add_field(name="Leak", value=leak,       inline=True)
    embed.add_field(name="Type", value=type_badge, inline=True)
    embed.set_footer(text=f"Updated by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


@tree.command(name="leaks", description="Search for a game asset leak by name")
@auto_defer(ephemeral=False)
@app_commands.describe(
    leak="Name or partial name to search for",
    type="Filter by type: 'normal' or 'booster' (optional)",
)
async def leaks_search(interaction: discord.Interaction, leak: str, type: Optional[str] = None):
    if interaction.channel.name != LEAKS_CHANNEL_NAME:
        return await interaction.followup.send(f"❌ This command can only be used in #{LEAKS_CHANNEL_NAME}.", ephemeral=True)

    member = interaction.guild.get_member(interaction.user.id) or interaction.user

    # Determine what type filter to use
    if type:
        type = type.lower().strip()
        if type not in ("normal", "booster"):
            return await interaction.followup.send("❌ Type must be `normal` or `booster`.", ephemeral=True)

        # Booster packs require the booster role
        if type == "booster" and not is_booster(member) and not has_admin_role(member):
            return await interaction.followup.send(
                "🌟 Booster packs are exclusive to **Server Boosters**!\n"
                "Boost the server to unlock access to booster packs.",
                ephemeral=True
            )

        results = search_leaks(leak, leak_type=type)
    else:
        # No type filter — if user is not a booster, only show normal leaks
        if is_booster(member) or has_admin_role(member):
            results = search_leaks(leak)
        else:
            results = search_leaks(leak, leak_type="normal")

    if not results:
        return await interaction.followup.send(f"❌ No leaks found matching **{leak}**.")

    # Single result — full embed with payhip
    if len(results) == 1:
        row        = results[0]
        is_booster_pack = row["type"] == "booster"

        # Double-check access for booster pack
        if is_booster_pack and not is_booster(member) and not has_admin_role(member):
            return await interaction.followup.send("🌟 This is a booster-exclusive pack. Boost the server to access it!", ephemeral=True)

        type_badge = "🌟 Booster Pack" if is_booster_pack else "📦 Normal Pack"
        embed = discord.Embed(title=f"📦 {row['name']}", color=discord.Color.blurple())
        embed.add_field(name="📋 Type",        value=type_badge,                            inline=True)
        embed.add_field(name="⬇️ Download",    value=f"[Click here]({row['link']})",        inline=True)
        embed.add_field(name="🛒 Buy on Payhip", value=f"[Click here]({row['payhip_url']})", inline=True)
        embed.set_footer(text=f"Added <t:{int(row['added_at'])}:R> by user ID {row['added_by']}")
        return await interaction.followup.send(embed=embed)

    # Multiple results — paginated
    view = LeaksSearchView(results, leak)
    await interaction.followup.send(embed=build_search_embed(results, leak, 0), view=view)


@tree.command(name="leakslist", description="List all available leaks")
@auto_defer(ephemeral=False)
@app_commands.describe(type="Filter by type: 'normal' or 'booster' (optional)")
async def leaks_list(interaction: discord.Interaction, type: Optional[str] = None):
    if interaction.channel.name != LEAKS_CHANNEL_NAME:
        return await interaction.followup.send(f"❌ This command can only be used in #{LEAKS_CHANNEL_NAME}.", ephemeral=True)

    member = interaction.guild.get_member(interaction.user.id) or interaction.user
    user_is_booster = is_booster(member)
    user_is_admin   = has_admin_role(member)

    if type:
        type = type.lower().strip()
        if type not in ("normal", "booster"):
            return await interaction.followup.send("❌ Type must be `normal` or `booster`.", ephemeral=True)

        if type == "booster" and not user_is_booster and not user_is_admin:
            return await interaction.followup.send(
                "🌟 Booster packs are exclusive to **Server Boosters**!\nBoost the server to unlock access.",
                ephemeral=True
            )
        rows = get_all_leaks(leak_type=type)
    else:
        if user_is_booster or user_is_admin:
            rows = get_all_leaks()
        else:
            rows = get_all_leaks(leak_type="normal")

    if not rows:
        return await interaction.followup.send("📭 No leaks in the database yet.")

    # If user is a booster, show them how many booster packs exist and threshold info
    booster_count = len(get_all_leaks(leak_type="booster"))
    if user_is_booster and not user_is_admin and booster_count > 0 and not type:
        note_embed = discord.Embed(
            title="🌟 Booster Perks",
            description=(
                f"There are currently **{booster_count}** booster-exclusive packs available!\n"
                f"As a server booster, you have access to **all** of them.\n"
                f"*(Use `/leakslist type:booster` to see only booster packs)*"
            ),
            color=discord.Color.gold()
        )
        await interaction.channel.send(embed=note_embed, delete_after=30)

    view = LeaksListView(rows, leak_type=type)
    await interaction.followup.send(embed=build_list_embed(rows, 0, type), view=view)


@tree.command(name="leaksdelete", description="Delete a leak entry (admin only)")
@auto_defer(ephemeral=False)
@app_commands.describe(leak="Exact name of the leak to delete")
async def leaks_delete(interaction: discord.Interaction, leak: str):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)

    success = delete_leak(leak)
    if not success:
        return await interaction.followup.send(f"❌ No leak found with the name **{leak}**.")

    embed = discord.Embed(title="🗑️ Leak Deleted", description=f"**{leak}** has been removed from the database.", color=discord.Color.red())
    await interaction.followup.send(embed=embed)


@tree.command(name="leaksexport", description="Export the entire leaks database as a JSON file (admin only)")
@auto_defer(ephemeral=True)
async def leaks_export(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)

    rows = get_all_leaks()
    if not rows:
        return await interaction.followup.send("📭 No leaks in the database to export.", ephemeral=True)

    data = [{"name": r["name"], "link": r["link"], "payhip_url": r["payhip_url"], "type": r["type"], "added_by": r["added_by"], "added_at": r["added_at"]} for r in rows]
    json_bytes = json.dumps(data, indent=2).encode("utf-8")
    file = discord.File(fp=BytesIO(json_bytes), filename="leaks_export.json")
    await interaction.followup.send(content=f"✅ Exported **{len(data)}** leaks.", file=file, ephemeral=True)


@tree.command(name="leaksimport", description="Import leaks from a JSON file (admin only)")
@auto_defer(ephemeral=True)
async def leaks_import(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)

    view = ImportModeView(interaction.user.id, interaction.channel_id)
    await interaction.followup.send(
        "📂 Choose import mode:\n"
        "**Merge** — keep existing leaks, only add new ones\n"
        "**Replace** — wipe the database and import fresh\n\n"
        "✅ Supports both old format (no `type` field → defaults to `normal`) and new format (with `type` field).",
        view=view,
        ephemeral=True
    )


@tree.command(name="leaksedit", description="Edit an existing leak entry (admin only)")
@auto_defer(ephemeral=True)
@app_commands.describe(
    leak="Exact name of the leak to edit",
    newname="New name (leave blank to keep current)",
    newlink="New download link (leave blank to keep current)",
    newpayhip="New Payhip link (leave blank to keep current)",
)
async def leaks_edit(
    interaction: discord.Interaction,
    leak: str,
    newname: Optional[str] = None,
    newlink: Optional[str] = None,
    newpayhip: Optional[str] = None,
):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)
    if not any([newname, newlink, newpayhip]):
        return await interaction.followup.send("❌ You need to provide at least one field to update. Use `/addtype` to change the type.", ephemeral=True)

    with get_db() as conn:
        row = conn.execute("SELECT * FROM leaks WHERE LOWER(name) = ?", (leak.lower().strip(),)).fetchone()
        if not row:
            return await interaction.followup.send(f"❌ No leak found with the name **{leak}**.", ephemeral=True)

        updated_name   = newname.strip()   if newname   else row["name"]
        updated_link   = newlink.strip()   if newlink   else row["link"]
        updated_payhip = newpayhip.strip() if newpayhip else row["payhip_url"]

        try:
            conn.execute("UPDATE leaks SET name = ?, link = ?, payhip_url = ? WHERE id = ?", (updated_name, updated_link, updated_payhip, row["id"]))
            conn.commit()
        except sqlite3.IntegrityError:
            return await interaction.followup.send(f"❌ A leak named **{updated_name}** already exists.", ephemeral=True)

    embed = discord.Embed(title="✏️ Leak Updated", color=discord.Color.yellow())
    embed.add_field(name="Name",          value=f"~~{row['name']}~~ → {updated_name}" if newname else updated_name, inline=False)
    embed.add_field(name="Download Link", value=updated_link,   inline=False)
    embed.add_field(name="Payhip Link",   value=updated_payhip, inline=False)
    embed.set_footer(text=f"Edited by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ============================================================
# RATING COMMANDS
# ============================================================

@tree.command(name="ratingsetup", description="Post the edit submission panel (admin only)")
@auto_defer(ephemeral=True)
async def rating_setup(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)

    if interaction.channel_id != SUBMISSION_CHANNEL_ID:
        ch = interaction.guild.get_channel(SUBMISSION_CHANNEL_ID)
        mention = ch.mention if ch else f"channel ID {SUBMISSION_CHANNEL_ID}"
        return await interaction.followup.send(f"❌ The submission panel can only be posted in {mention}.", ephemeral=True)

    embed = discord.Embed(
        title="🎬 Submit Your Edit for Rating",
        description=(
            "Want feedback on your edit? Click the button below to submit!\n\n"
            "**How it works:**\n"
            "1. Click the button and fill in your display name + video link\n"
            "2. Your edit gets posted in the ratings channel\n"
            "3. Others can rate it out of 10 and leave feedback\n"
            "4. You'll get pinged when feedback comes in\n\n"
            "🌟 **Server Boosters** will also get an admin review ping!"
        ),
        color=discord.Color.gold()
    )
    embed.set_footer(text="Be constructive with feedback • One vote per person")

    view = SubmitButtonView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.followup.send("✅ Rating panel posted!", ephemeral=True)


# ============================================================
# BACKGROUND TASKS
# ============================================================

@tasks.loop(minutes=5)
async def cleanup_expired():
    now = time.time()

    # Clean up expired trade offers
    for user_id in list(trade_offers.keys()):
        if isinstance(trade_offers[user_id], list):
            trade_offers[user_id] = [o for o in trade_offers[user_id] if isinstance(o, dict) and now - o.get("timestamp", 0) <= 300]

    # Clean up expired active boosts
    for user_id, boosts in list(user_active_boosts.items()):
        for boost_type in list(boosts.keys()):
            if boosts[boost_type]["expires"] < now:
                del user_active_boosts[user_id][boost_type]


# ============================================================
# RUN
# ============================================================

bot.run(os.getenv("BOT_TOKEN"))
