import discord
from discord.ext import commands, tasks
from discord import app_commands
import os
import asyncio
import re
import random
import time
from collections import defaultdict
from typing import Optional
from discord.ui import Select, Button, View
from discord import ButtonStyle
import functools
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT
from io import BytesIO
from datetime import datetime
from PIL import Image



def auto_defer(ephemeral=True):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            interaction = args[0]  # Assumes interaction is first
            try:
                await interaction.response.defer(ephemeral=ephemeral)
            except discord.errors.InteractionAlreadyResponded:
                pass
            return await func(*args, **kwargs)
        return wrapper
    return decorator



intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

GIVEAWAY_CHANNEL_NAME = "🎁︱𝒩𝓊𝓂𝒷𝑒𝓇-𝒢𝒾𝓋𝑒𝒶𝓌𝒶𝓎"
TICKET_CATEGORY_ID = 1348042174159392768
ADMIN_ROLES = ["Admin"]
ADMIN_ROLE_IDS = [1517236355275428040]
MESSAGES_PER_SHECKLE = 10  # Number of messages needed to earn 1 sheckle

giveaway_logs = []
active_giveaways = {}
user_message_counts = defaultdict(int)
user_inventory = defaultdict(lambda: {"growing": [], "grown": []})
user_sheckles = defaultdict(int)
user_achievements = defaultdict(list)  # user_id -> list of achievement names
current_plant_event = None
user_fertilizers = defaultdict(lambda: defaultdict(int))  # user_id -> {fertilizer_name: count}
user_active_boosts = defaultdict(dict)  # user_id -> {boost_type: {expires: timestamp, multiplier: float}}
YOUR_ANNOUNCEMENT_CHANNEL_ID = 1386095247997665521


fertilizers = {
    "Growth Boost": {
        "cost": 50,
        "description": "Makes plants grow 25% faster for 1 hour",
        "effect": {"type": "growth", "multiplier": 0.75, "duration": 3600}
    },
    "Mutation Boost": {
        "cost": 100,
        "description": "Doubles mutation chances for 1 hour",
        "effect": {"type": "mutation", "multiplier": 2.0, "duration": 3600}
    }
}


PLANT_EVENTS = [
    {
        "name": "Solar Eclipse",
        "effect": "delay",
        "delay": 300,  # +5 minutes to all grow times
        "duration": 3600  # 1 hour
    },
    {
        "name": "Fertile Ground",
        "effect": "speed",
        "multiplier": 0.7,  # 30% faster
        "duration": 7200  # 2 hours
    }
]

achievement_definitions = {
    "First Seed": {
        "condition": lambda uid: len(user_inventory[uid]["grown"]) > 0,
        "description": "Grow your first plant"
    },
    "Mutation Master": {
        "condition": lambda uid: any(s.mutation for s in user_inventory[uid]["grown"]),
        "description": "Grow a mutated plant"
    },
    "Legendary Gardener": {
        "condition": lambda uid: any(s.name in ["Sugar Apple", "Beanstalk"] for s in user_inventory[uid]["grown"]),
        "description": "Grow a legendary plant"
    },
}

current_season = {
    "name": "Spring",
    "boosted_seeds": ["Carrot", "Strawberry"],
    "multiplier": 1.0
}

SEASONS = [
    {"name": "Spring", "boosted_seeds": ["Carrot", "Strawberry"]},
    {"name": "Summer", "boosted_seeds": ["Ember Lily", "Bamboo"]},
    {"name": "Fall", "boosted_seeds": ["Potato", "Sugar Apple"]},
    {"name": "Winter", "boosted_seeds": ["Beanstalk"]}
]

seeds = {
    "Carrot": (2, 250),
    "Strawberry": (10, 50),
    "Potato": (5, 0),
    "Bamboo": (20, 300),
    "Ember Lily": (55, 550),
    "Sugar Apple": (80, 800),
    "Beanstalk": (70, 750),
}

SEED_RARITIES = {
    "Carrot": "Uncommon",
    "Strawberry": "Common",
    "Potato": "Common",
    "Bamboo": "Rare",
    "Ember Lily": "Mythical",
    "Sugar Apple": "Legendary",
    "Beanstalk": "Legendary",
}

RARITY_CHANCES = {
    "Common": 0.9,
    "Uncommon": 0.6,
    "Rare": 0.35,
    "Mythical": 0.1,
    "Legendary": 0.05,
}

current_stock = []

mutations = {
    "global": {
        "Giant": {
            "multiplier": 1.5,
            "rarity": 0.05,
            "description": "Yields 50% more sheckles"
        },
        "Golden": {
            "multiplier": 2.0,
            "rarity": 0.01,
            "description": "Doubles the sheckle value"
        },
        "Diseased": {
            "multiplier": 0.5,
            "rarity": 0.1,
            "description": "Reduces value by half"
        }
    },
    "specific": {
        "Carrot": {
            "Perfect": {
                "multiplier": 10.0,
                "rarity": 0.001,
                "description": "The ultimate carrot - worth 10x normal value",
                "priority": True
            },
            "Bunny's Favorite": {
                "multiplier": 3.0,
                "rarity": 0.02,
                "description": "Specially loved by rabbits"
            }
        },
        "Ember Lily": {
            "Inferno": {
                "multiplier": 3.5,
                "rarity": 0.03,
                "description": "Burns with eternal flame"
            }
        },
        "Beanstalk": {
            "Skyreach": {
                "multiplier": 4.0,
                "rarity": 0.015,
                "description": "Grows high enough to reach the clouds"
            }
        }
    }
}

limited_seeds = {}

trade_offers = defaultdict(list)  # user_id -> dict with keys: sender_id, seed_name, timestamp
trade_logs = []

# ============================================================
# TICKET / MIDDLEMAN SYSTEM
# ============================================================

class CloseTicketView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="❌ Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket_button")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        middleman_role_id = 1348072637972090880
        if not any(role.id == middleman_role_id for role in interaction.user.roles):
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
    private_server = discord.ui.TextInput(
        label="Can both traders join Private servers?",
        placeholder="YES/NO"
    )
    ready_status = discord.ui.TextInput(
        label="Are BOTH traders ready?",
        placeholder="YES/NO"
    )
    trade_details = discord.ui.TextInput(
        label="What are you GIVING and RECEIVING?",
        placeholder="GIVING some random plant. RECEIVING some random plant.",
        style=discord.TextStyle.paragraph
    )

    def __init__(self, bot, interaction):
        super().__init__()
        self.bot = bot
        self.interaction = interaction

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip().lower() != "yes i understand.":
            blacklist_role = interaction.guild.get_role(1344056030153146448)
            if blacklist_role:
                await interaction.user.add_roles(blacklist_role, reason="Failed to confirm MM rules")
            return await interaction.response.send_message(
                "🚫 You failed to confirm properly and were blacklisted.",
                ephemeral=True
            )
    
        guild = interaction.guild
        ticket_category = guild.get_channel(TICKET_CATEGORY_ID)
        if not ticket_category or not isinstance(ticket_category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ Ticket category not found.", ephemeral=True)
    
        middleman_role = guild.get_role(1348072637972090880)
        if not middleman_role:
            return await interaction.response.send_message("❌ Middleman role not found.", ephemeral=True)
    
        ticket_number = len([c for c in ticket_category.channels if isinstance(c, discord.TextChannel) and c.name.startswith("ticket-")]) + 1
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            middleman_role: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_permissions=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)
        }
    
        try:
            ticket_channel = await ticket_category.create_text_channel(
                name=f"ticket-{ticket_number}",
                overwrites=overwrites,
                reason="Middleman ticket"
            )
        except Exception as e:
            print(f"Error creating ticket channel: {e}")
            return await interaction.response.send_message(
                "❌ Failed to create ticket channel.",
                ephemeral=True
            )
    
        embed = discord.Embed(
            title="📨 Middleman Ticket Application",
            color=discord.Color.green()
        )
        embed.add_field(name="Trader Info", value=self.trader_info.value, inline=False)
        embed.add_field(name="Private Server Access", value=self.private_server.value, inline=False)
        embed.add_field(name="Ready Status", value=self.ready_status.value, inline=False)
        embed.add_field(name="Trade Details", value=self.trade_details.value, inline=False)
        embed.set_footer(text=f"Ticket opened by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
    
        try:
            await ticket_channel.send(
                content=middleman_role.mention,
                embed=embed,
                view=CloseTicketView(self.bot)
            )
            await interaction.response.send_message(
                f"✅ Ticket created: {ticket_channel.mention}",
                ephemeral=True
            )
        except Exception as e:
            print(f"Error sending ticket message: {e}")
            await interaction.response.send_message(
                "❌ Ticket created but failed to send details.",
                ephemeral=True
            )

# ============================================================
# TRADING SYSTEM
# ============================================================

class TradeView(View):
    def __init__(self, sender, recipient, sender_seed, recipient_seed, original_message=None, viewer=None, trade_messages=None):
        super().__init__(timeout=300)
        self.sender = sender
        self.recipient = recipient
        self.sender_seed = sender_seed
        self.recipient_seed = recipient_seed
        self.original_message = original_message
        self.viewer = viewer
        self.trade_messages = trade_messages or []
        
        if viewer and viewer.id != sender.id:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.label == "Cancel Trade":
                    self.remove_item(item)

    async def update_all_messages(self, interaction, embed):
        messages_to_update = []
        
        if self.original_message:
            try:
                self.original_message = await interaction.channel.fetch_message(self.original_message.id)
                messages_to_update.append(self.original_message)
            except discord.NotFound:
                pass
                
        for msg_id in self.trade_messages:
            try:
                msg = await interaction.channel.fetch_message(msg_id)
                messages_to_update.append(msg)
            except:
                pass
        
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        
        try:
            if not interaction.response.is_done():
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            print(f"Error responding to interaction: {e}")
            try:
                await interaction.followup.send("✅ Trade completed successfully!", ephemeral=True)
            except:
                pass
        
        current_msg_id = interaction.message.id if hasattr(interaction, 'message') else None
        
        for msg in messages_to_update:
            if msg.id != current_msg_id:
                try:
                    disabled_view = TradeView(
                        self.sender, 
                        self.recipient, 
                        self.sender_seed, 
                        self.recipient_seed,
                        viewer=self.viewer
                    )
                    for item in disabled_view.children:
                        if isinstance(item, discord.ui.Button):
                            item.disabled = True
                    
                    await msg.edit(embed=embed, view=disabled_view)
                except Exception as e:
                    print(f"Error updating message {msg.id}: {e}")
                    continue

    @discord.ui.button(label="Accept Trade", style=ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.recipient.id:
            await safe_send_ephemeral(interaction, "❌ This trade isn't for you!")
            return
        
        try:
            update_growing_seeds(self.sender.id)
            update_growing_seeds(self.recipient.id)
            
            sender_grown = user_inventory[self.sender.id]["grown"]
            recipient_grown = user_inventory[self.recipient.id]["grown"]
            
            sender_seed = next((s for s in sender_grown if s.name == self.sender_seed.name and s.mutation == self.sender_seed.mutation), None)
            recipient_seed = next((s for s in recipient_grown if s.name == self.recipient_seed.name and s.mutation == self.recipient_seed.mutation), None)
            
            if not sender_seed or not recipient_seed:
                remove_trade_offer(
                    self.sender.id,
                    self.recipient.id,
                    self.sender_seed.name,
                    self.recipient_seed.name
                )
                return await safe_send_ephemeral(interaction, "❌ One or both seeds no longer available.")
            
            sender_grown.remove(sender_seed)
            recipient_grown.remove(recipient_seed)
            sender_grown.append(recipient_seed)
            recipient_grown.append(sender_seed)
            
            trade_logs.append({
                "from": self.sender.id,
                "to": self.recipient.id,
                "gave": sender_seed.name,
                "got": recipient_seed.name,
                "time": time.time()
            })
            
            remove_trade_offer(
                self.sender.id,
                self.recipient.id,
                self.sender_seed.name,
                self.recipient_seed.name
            )
    
            embed = discord.Embed(
                title="✅ Trade Completed",
                description=(
                    f"{self.sender.mention} gave {pretty_seed(sender_seed)}\n"
                    f"{self.recipient.mention} gave {pretty_seed(recipient_seed)}"
                ),
                color=discord.Color.green()
            )
            
            await self.update_all_messages(interaction, embed)
    
            try:
                await self.sender.send(
                    f"✅ Your trade with {self.recipient.mention} was accepted!\n"
                    f"You received: {pretty_seed(recipient_seed)}\n"
                    f"You gave: {pretty_seed(sender_seed)}"
                )
            except:
                pass
    
            try:
                await self.recipient.send(
                    f"✅ You accepted the trade with {self.sender.mention}!\n"
                    f"You received: {pretty_seed(sender_seed)}\n"
                    f"You gave: {pretty_seed(recipient_seed)}"
                )
            except:
                pass

        except Exception as e:
            print(f"Error in trade accept: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ An error occurred while processing the trade.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ An error occurred while processing the trade.", ephemeral=True)
            except:
                pass

    @discord.ui.button(label="Decline Trade", style=ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.recipient.id:
            await safe_send_ephemeral(interaction, "❌ This trade isn't for you!")
            return
            
        remove_trade_offer(
            self.sender.id,
            self.recipient.id,
            self.sender_seed.name,
            self.recipient_seed.name
        )
    
        embed = discord.Embed(
            title="❌ Trade Declined",
            description=f"{self.recipient.mention} declined the trade offer from {self.sender.mention}",
            color=discord.Color.red()
        )
            
        await self.update_all_messages(interaction, embed)
    
        try:
            await self.sender.send(f"❌ {self.recipient.mention} declined your trade offer.")
        except:
            pass

    @discord.ui.button(label="Cancel Trade", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.sender.id:
            await safe_send_ephemeral(interaction, "❌ Only the sender can cancel this trade.")
            return
    
        remove_trade_offer(
            self.sender.id,
            self.recipient.id,
            self.sender_seed.name,
            self.recipient_seed.name
        )
    
        embed = discord.Embed(
            title="🚫 Trade Cancelled",
            description=f"{self.sender.mention} cancelled the trade offer.",
            color=discord.Color.red()
        )
    
        await self.update_all_messages(interaction, embed)
    
        try:
            await self.recipient.send(f"🚫 {self.sender.mention} cancelled their trade offer.")
        except:
            pass

# ============================================================
# GIVEAWAY SYSTEM
# ============================================================

class Giveaway:
    def __init__(self, hoster, prize, winners, number_range, target, duration, channel):
        self.hoster = hoster
        self.prize = prize
        self.winners_required = winners
        self.low, self.high = number_range
        self.target = target
        self.duration = duration
        self.duration_minutes = duration
        self.channel = channel
        self.winners = set()
        self.participants = set()
        self.guessed_users = {}
        self.user_guesses = {}
        self.end_time = time.time() + (duration * 60) if duration > 0 else None
        self.task = None

    def check_guess(self, user, guess):
        if self.end_time is not None and time.time() > self.end_time:
            return None
            
        if user.id == self.hoster.id:
            return None
            
        self.participants.add(user)
        
        if user.id not in self.guessed_users:
            self.guessed_users[user.id] = []
        self.guessed_users[user.id].append(guess)
        
        return guess == self.target

class GuessModal(discord.ui.Modal, title='Enter Your Guess'):
    guess = discord.ui.TextInput(label='Your guess', placeholder=f'Enter a number between X and Y')
    
    def __init__(self, giveaway: Giveaway):
        super().__init__()
        self.giveaway = giveaway
    
    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id == self.giveaway.hoster.id:
            return await interaction.response.send_message(
                "❌ You can't participate in your own giveaway!",
                ephemeral=True
            )
        if time.time() > self.giveaway.end_time:
            return await interaction.response.send_message(
                "❌ This giveaway has already ended!",
                ephemeral=True
            )
        
        try:
            guess = int(self.guess.value)
            if guess < self.giveaway.low or guess > self.giveaway.high:
                return await interaction.response.send_message(
                    f"❌ Guess must be between {self.giveaway.low}-{self.giveaway.high}!",
                    ephemeral=True
                )
                
            self.giveaway.user_guesses.setdefault(interaction.user.id, []).append(guess)
            
            if self.giveaway.check_guess(interaction.user, guess):
                self.giveaway.winners.add(interaction.user)
                
                await interaction.response.send_message(
                    f"🎉 You guessed the correct number `{guess}`!",
                    ephemeral=True
                )
                
                if len(self.giveaway.winners) >= self.giveaway.winners_required:
                    await end_giveaway(self.giveaway)
            else:
                await interaction.response.send_message(
                    "❌ That's not the correct number. Try again!",
                    ephemeral=True
                )
                
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number!",
                ephemeral=True
            )

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway: Giveaway):
        super().__init__(timeout=None)
        self.giveaway = giveaway
        self.message = None

        if time.time() > giveaway.end_time:
            self.disable_expired_buttons()

    def disable_expired_buttons(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == "join_giveaway":
                    item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.message is None:
            self.message = interaction.message
        return True

    @discord.ui.button(label="Join Giveaway", style=discord.ButtonStyle.green, custom_id="join_giveaway")
    async def join_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        if time.time() > self.giveaway.end_time:
            return await interaction.response.send_message(
                "❌ This giveaway has already ended!",
                ephemeral=True
            )

        await interaction.response.send_modal(GuessModal(self.giveaway))

    @discord.ui.button(label="Participants", style=discord.ButtonStyle.blurple, custom_id="view_participants")
    async def view_participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        participants = list(self.giveaway.participants)
        total_pages = max(1, (len(participants) + 9) // 10)

        if not participants:
            return await interaction.response.send_message(
                "No participants yet!",
                ephemeral=True
            )

        embed = self.create_participants_embed(participants, 0, total_pages)
        view = ParticipantsView(
            giveaway=self.giveaway,
            participants=participants,
            current_page=0,
            total_pages=total_pages,
            original_view=self
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="My Guesses", style=discord.ButtonStyle.secondary, custom_id="my_guesses")
    async def my_guesses(self, interaction: discord.Interaction, button: discord.ui.Button):
        guesses = self.giveaway.user_guesses.get(interaction.user.id, [])
        if not guesses:
            return await interaction.response.send_message("You haven't made any guesses yet!", ephemeral=True)

        formatted = ', '.join(map(str, guesses))
        await interaction.response.send_message(f"📋 Your guesses: `{formatted}`", ephemeral=True)
        
    def create_participants_embed(self, participants, page, total_pages):
        start_index = page * 10
        end_index = start_index + 10
        page_participants = participants[start_index:end_index]
        
        embed = discord.Embed(
            title="👥 Giveaway Participants",
            description="\n".join(f"{i+1}. {p.mention}" for i, p in enumerate(page_participants, start=start_index)),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Page {page+1} of {total_pages}")
        return embed

def create_giveaway_embed(giveaway: Giveaway) -> discord.Embed:
    embed = discord.Embed(
        title="🎉 NUMBER GUESS GIVEAWAY 🎉",
        description=f"Hosted by {giveaway.hoster.mention}",
        color=discord.Color.gold()
    )
    
    embed.add_field(name="🏆 Prize", value=giveaway.prize, inline=False)
    embed.add_field(name="🔢 Number Range", value=f"{giveaway.low}-{giveaway.high}", inline=True)
    embed.add_field(name="🎯 Winners Needed", value=str(giveaway.winners_required), inline=True)
    
    if giveaway.duration_minutes > 0:
        remaining = max(0, giveaway.end_time - time.time())
        mins, secs = divmod(int(remaining), 60)
        embed.add_field(name="⏳ Time Remaining", value=f"{mins}m {secs}s", inline=True)
    else:
        embed.add_field(name="⏳ Duration", value="No time limit", inline=True)
    
    embed.add_field(name="👥 Participants", value=f"{len(giveaway.participants)} joined", inline=True)
    embed.add_field(name="🏆 Winners Found", value=f"{len(giveaway.winners)}/{giveaway.winners_required}", inline=True)
    
    embed.set_footer(
        text="Click 'Join Giveaway' to participate!",
        icon_url=giveaway.hoster.display_avatar.url
    )
    
    return embed

class ParticipantsView(discord.ui.View):
    def __init__(self, giveaway: Giveaway, participants: list, current_page: int, total_pages: int, original_view: GiveawayView = None):
        super().__init__(timeout=60)
        self.giveaway = giveaway
        self.participants = participants
        self.current_page = current_page
        self.total_pages = total_pages
        self.original_view = original_view
        
        self.prev_button.disabled = current_page == 0
        self.next_button.disabled = current_page >= total_pages - 1
        
        if time.time() > self.giveaway.end_time:
            for item in self.children:
                if isinstance(item, discord.ui.Button) and item.custom_id == "join_giveaway":
                    item.disabled = True
    
    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_view(interaction)
    
    @discord.ui.button(label="Back", style=discord.ButtonStyle.blurple)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.original_view and hasattr(self.original_view, 'message'):
            embed = create_giveaway_embed(self.giveaway)
            await self.original_view.message.edit(embed=embed, view=self.original_view)
            await interaction.response.defer()
        else:
            embed = create_giveaway_embed(self.giveaway)
            view = GiveawayView(self.giveaway)
            view.disable_expired_buttons()
            await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.update_view(interaction)
    
    async def update_view(self, interaction: discord.Interaction):
        embed = GiveawayView(self.giveaway).create_participants_embed(
            self.participants,
            self.current_page,
            self.total_pages
        )
        
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            if hasattr(self, 'message'):
                await self.message.edit(view=self)
        except discord.NotFound:
            pass

# ============================================================
# SHARED UI / HELPERS
# ============================================================

class ConfirmView(View):
    def __init__(self):
        super().__init__(timeout=60)
        self.value = None
    
    @discord.ui.button(label="Confirm", style=ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        self.value = True
        self.stop()
    
    @discord.ui.button(label="Cancel", style=ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        self.value = False
        self.stop()

# ============================================================
# GARDEN SYSTEM — classes & logic kept but commands disabled
# ============================================================

class InventoryView(View):
    def __init__(self, user_id):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.add_item(InventorySelect())
    
    @discord.ui.button(label="Refresh", style=ButtonStyle.blurple)
    async def refresh(self, interaction: discord.Interaction, button: Button):
        update_growing_seeds(interaction.user.id)
        new_achievements = check_achievements(interaction.user.id)
        
        inv = user_inventory[interaction.user.id]
        grown_list = [pretty_seed(seed) for seed in inv["grown"]]
        growing_list = [
            f"{pretty_seed(seed)} [{max(0, int(seed.finish_time - time.time()))}s]"
            for seed in inv["growing"]
        ]
        
        embed = discord.Embed(title="🌱 Your Garden (Refreshed)", color=discord.Color.green())
        embed.add_field(name="🌾 Growing", value='\n'.join(growing_list) or "None", inline=False)
        embed.add_field(name="🥕 Grown", value='\n'.join(grown_list) or "None", inline=False)
        embed.add_field(name="💰 Sheckles", value=str(user_sheckles.get(interaction.user.id, 0)), inline=False)
        
        if new_achievements:
            embed.set_footer(text=f"🎉 New achievements: {', '.join(new_achievements)}")
        
        await interaction.response.edit_message(embed=embed)

class InventorySelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="View Growing Plants", description="See what's currently growing", value="growing"),
            discord.SelectOption(label="View Grown Plants", description="See your harvested plants", value="grown"),
            discord.SelectOption(label="View Achievements", description="See your unlocked achievements", value="achievements"),
            discord.SelectOption(label="View Fertilizers", description="See your fertilizer stock", value="fertilizers")
        ]
        super().__init__(
            placeholder="Select inventory section...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        selection = self.values[0]
        inv = user_inventory[interaction.user.id]
        
        if selection == "growing":
            growing_list = [
                f"{pretty_seed(seed)} [{max(0, int(seed.finish_time - time.time()))}s]" 
                for seed in inv["growing"]
            ]
            embed = discord.Embed(title="🌾 Growing Plants", description='\n'.join(growing_list) or "None", color=discord.Color.green())
        elif selection == "grown":
            grown_list = [pretty_seed(seed) for seed in inv["grown"]]
            embed = discord.Embed(title="🥕 Grown Plants", description='\n'.join(grown_list) or "None", color=discord.Color.green())
        elif selection == "achievements":
            achievements = user_achievements.get(interaction.user.id, [])
            embed = discord.Embed(title="🏆 Achievements", description='\n'.join(achievements) or "None", color=discord.Color.gold())
        elif selection == "fertilizers":
            ferts = [
                f"{name}: {count}" 
                for name, count in user_fertilizers[interaction.user.id].items() 
                if count > 0
            ]
            embed = discord.Embed(title="🧪 Fertilizers", description='\n'.join(ferts) or "None", color=discord.Color.blue())
        
        view = InventoryView(interaction.user.id)
        await interaction.response.edit_message(embed=embed, view=view)

class SeedShopView(View):
    def __init__(self, regular_seeds, limited_seeds, fertilizers):
        super().__init__(timeout=120)
        self.regular_seeds = regular_seeds
        self.limited_seeds = limited_seeds
        self.fertilizers = fertilizers
        self.add_item(SeedSelect(regular_seeds, limited_seeds, fertilizers))


class SeedSelect(Select):
    def __init__(self, regular_seeds, limited_seeds, fertilizers):
        self.regular_seeds = regular_seeds
        self.limited_seeds = limited_seeds
        self.fertilizers = fertilizers
        
        options = []
        
        for seed in regular_seeds:
            cost, quest = seeds[seed]
            rarity = SEED_RARITIES.get(seed, "Unknown")
            options.append(discord.SelectOption(
                label=f"{seed} - {cost} sheckles",
                description=f"{rarity} | Quest: {quest} messages",
                value=f"seed_{seed}"
            ))
        
        for name, data in limited_seeds.items():
            time_left = max(0, int((data["expires"] - time.time()) // 60))
            options.append(discord.SelectOption(
                label=f"🌟 {name} - {data['sheckles']} sheckles",
                description=f"Limited | {time_left}min left | Quest: {data['quest']}",
                value=f"limited_{name}"
            ))
        
        for name, data in fertilizers.items():
            options.append(discord.SelectOption(
                label=f"🧪 {name} - {data['cost']} sheckles",
                description=data["description"],
                value=f"fert_{name}"
            ))
        
        super().__init__(
            placeholder="Select an item to purchase...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        await interaction.response.defer(ephemeral=True)
        
        try:
            if value.startswith("seed_"):
                await self.handle_seed_purchase(interaction, value[5:], is_limited=False)
            elif value.startswith("limited_"):
                await self.handle_seed_purchase(interaction, value[8:], is_limited=True)
            elif value.startswith("fert_"):
                await self.handle_fertilizer_purchase(interaction, value[5:])
            
            message = await interaction.original_response()
            view = SeedShopView(self.regular_seeds, self.limited_seeds, self.fertilizers)
            await message.edit(view=view)
            
        except Exception as e:
            print(f"Error in callback: {e}")
            await interaction.followup.send("❌ An error occurred while processing your request.", ephemeral=True)

    async def handle_seed_purchase(self, interaction: discord.Interaction, seed_name: str, is_limited: bool):
        user_id = interaction.user.id
        
        if is_limited:
            if seed_name not in limited_seeds:
                return await interaction.followup.send("❌ This limited seed is no longer available.", ephemeral=True)
            
            seed_data = limited_seeds[seed_name]
            sheckles_required = seed_data["sheckles"]
            
            if time.time() > seed_data["expires"]:
                return await interaction.followup.send("❌ This limited seed has expired.", ephemeral=True)
                
            allowed_mutations = seed_data.get("mutations")
        else:
            if seed_name not in seeds:
                return await interaction.followup.send("❌ This seed is not available.", ephemeral=True)
            
            sheckles_required, _ = seeds[seed_name]
            allowed_mutations = None
    
        if user_sheckles.get(user_id, 0) < sheckles_required:
            return await interaction.followup.send("❌ Not enough sheckles!", ephemeral=True)
    
        user_sheckles[user_id] -= sheckles_required
        grow_time = calculate_grow_time(seed_name, user_id)
        seed_obj = GrowingSeed(seed_name, grow_time, limited=is_limited, allowed_mutations=allowed_mutations)
        user_inventory[user_id]["growing"].append(seed_obj)
    
        new_achievements = check_achievements(user_id)
        achievement_msg = f"\n🎉 New achievement(s): {', '.join(new_achievements)}" if new_achievements else ""
    
        await interaction.followup.send(
            f"✅ Purchased {'limited ' if is_limited else ''}{pretty_seed(seed_obj)} seed for {sheckles_required} sheckles! "
            f"It will be ready in {int(grow_time)} seconds."
            f"{achievement_msg}",
            ephemeral=True
        )
    
    async def handle_fertilizer_purchase(self, interaction: discord.Interaction, fert_name: str):
        fert = fertilizers.get(fert_name)
        if not fert:
            return await interaction.followup.send("❌ Invalid fertilizer", ephemeral=True)
        
        user_id = interaction.user.id
        
        if user_sheckles.get(user_id, 0) < fert["cost"]:
            return await interaction.followup.send("❌ Not enough sheckles", ephemeral=True)
        
        user_sheckles[user_id] -= fert["cost"]
        user_fertilizers[user_id][fert_name] += 1
        
        await interaction.followup.send(
            f"✅ Purchased {fert_name} for {fert['cost']} sheckles! "
            f"You now have {user_fertilizers[user_id][fert_name]} of this fertilizer.",
            ephemeral=True
        )

class ShovelConfirmView(discord.ui.View):
    def __init__(self, plant_name, plant_type, is_limited, is_mutated):
        super().__init__(timeout=60)
        self.plant_name = plant_name
        self.plant_type = plant_type
        self.is_limited = is_limited
        self.is_mutated = is_mutated
        self.confirmed = False

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await interaction.response.defer()
        
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.send_message("🚫 Removal cancelled.", ephemeral=True)

class GrowingSeed:
    def __init__(self, name, grow_duration, mutation=None, limited=False, allowed_mutations=None):
        self.name = name
        self.finish_time = time.time() + grow_duration
        self.limited = limited
        self.mutation = mutation or self.determine_mutation(name, allowed_mutations)

    def determine_mutation(self, plant_name, allowed_mutations=None):
        if allowed_mutations is not None:
            pool = []
            for mut in allowed_mutations:
                if mut in mutations["global"]:
                    pool.append((mut, mutations["global"][mut]["rarity"]))
                elif plant_name in mutations["specific"] and mut in mutations["specific"][plant_name]:
                    pool.append((mut, mutations["specific"][plant_name][mut]["rarity"]))

            for mut, rarity in pool:
                if random.random() < rarity:
                    return mut
            return None

        if plant_name in mutations["specific"]:
            for mut, data in mutations["specific"][plant_name].items():
                if data.get("priority", False) and random.random() < data["rarity"]:
                    return mut
            for mut, data in mutations["specific"][plant_name].items():
                if mut != "Perfect" and random.random() < data["rarity"]:
                    return mut

        for mut, data in mutations["global"].items():
            if random.random() < data["rarity"]:
                return mut

        return None

def calculate_grow_time(base_seed, user_id):
    BASE_GROW_TIMES = {
        "Carrot": 300,
        "Strawberry": 300,
        "Potato": 300,
        "Bamboo": 300,
        "Ember Lily": 300,
        "Sugar Apple": 300,
        "Beanstalk": 300
    }
    
    grow_time = BASE_GROW_TIMES.get(base_seed, 300)
    
    if base_seed in current_season["boosted_seeds"]:
        grow_time *= 0.8
    
    if current_plant_event:
        if current_plant_event["effect"] == "delay":
            grow_time += current_plant_event["delay"]
        elif current_plant_event["effect"] == "speed":
            grow_time *= current_plant_event["multiplier"]
    
    if user_active_boosts.get(user_id, {}).get("growth_boost"):
        boost = user_active_boosts[user_id]["growth_boost"]
        if time.time() < boost["expires"]:
            grow_time *= boost["multiplier"]
    
    return max(30, grow_time)

def check_achievements(user_id):
    new_achievements = []
    for name, data in achievement_definitions.items():
        if name not in user_achievements[user_id] and data["condition"](user_id):
            user_achievements[user_id].append(name)
            new_achievements.append(name)
    return new_achievements

def has_admin_role(member):
    return any(role.id in ADMIN_ROLE_IDS for role in member.roles)

def pretty_seed(seed_obj):
    name = f"{seed_obj.name}"
    if seed_obj.mutation:
        name += f" ({seed_obj.mutation})"
    if getattr(seed_obj, "limited", False):
        name += " 🌟(Limited)"
    return name

def normalize_seed_name(raw: str):
    raw = raw.strip()
    m = re.match(r'^(.*?)(?:\((.*?)\))?$', raw)
    base = ' '.join(word.capitalize() for word in m.group(1).split()).strip()
    mut  = m.group(2)
    if mut:
        mut = ' '.join(word.capitalize() for word in mut.split()).strip()
        combined = f"{base} ({mut})"
    else:
        combined = base
    return base, mut, combined

def find_matching_seed(seed_list, desired_input):
    base, mut, _ = normalize_seed_name(desired_input)
    for s in seed_list:
        if s.name != base:
            continue
        if mut is None or (s.mutation and s.mutation.lower() == mut.lower()):
            return s
    return None

def remove_trade_offer(sender_id, recipient_id, sender_seed_name, recipient_seed_name):
    trade_offers[recipient_id] = [
        offer for offer in trade_offers[recipient_id]
        if not (
            offer["sender_id"] == sender_id and
            offer["sender_seed_name"] == sender_seed_name and
            offer["recipient_seed_name"] == recipient_seed_name
        )
    ]

async def safe_send_ephemeral(interaction, message):
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)
    except discord.NotFound:
        pass

def update_growing_seeds(user_id):
    current_time = time.time()
    growing = user_inventory[user_id]["growing"]
    grown = user_inventory[user_id]["grown"]
    
    finished_seeds = [seed for seed in growing if seed.finish_time <= current_time]
    for seed in finished_seeds:
        grown.append(seed)
    
    user_inventory[user_id]["growing"] = [seed for seed in growing if seed.finish_time > current_time]

# ============================================================
# BOT EVENTS
# ============================================================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    for giveaway in active_giveaways.values():
        if hasattr(giveaway, 'view') and giveaway.view:
            bot.add_view(giveaway.view, message_id=giveaway.view.message.id)
    bot.add_view(CloseTicketView(bot))

    # Garden background tasks are disabled — uncomment to re-enable:
    # refresh_stock.start()
    # rotate_seasons.start()
    # check_plant_events.start()

    cleanup_expired.start()
    
    try:
        print("Syncing commands...")
        synced = await tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Failed to sync: {e}")

# ============================================================
# GIVEAWAY COMMANDS
# ============================================================

async def end_giveaway(giveaway):
    if giveaway.task:
        giveaway.task.cancel()

    if hasattr(giveaway, 'view') and giveaway.view:
        for item in giveaway.view.children:
            item.disabled = True
        try:
            if hasattr(giveaway.view, 'message') and giveaway.view.message:
                giveaway.view.disable_expired_buttons()
                await giveaway.view.message.edit(view=giveaway.view)
        except discord.NotFound:
            pass

    for winner in giveaway.winners:
        try:
            await winner.send(
                f"🎉 You won the giveaway in {giveaway.channel.mention}!\n"
                f"**Prize:** {giveaway.prize}\n"
                f"Contact {giveaway.hoster.mention} to claim your reward!"
            )
        except discord.Forbidden:
            try:
                await giveaway.channel.send(
                    f"{winner.mention} won but can't receive DMs! "
                    f"Please contact {giveaway.hoster.mention} to claim your prize."
                )
            except:
                pass

    winners_text = ", ".join(w.mention for w in giveaway.winners) if giveaway.winners else "No winners"
    embed = discord.Embed(
        title="🎉 Giveaway Ended",
        description=(
            f"**Prize:** {giveaway.prize}\n"
            f"**Target Number:** ||{giveaway.target}||\n"
            f"**Winners:** {winners_text}\n"
            f"**Total Participants:** {len(giveaway.guessed_users)}"
        ),
        color=discord.Color.green()
    )

    try:
        await giveaway.channel.send(embed=embed)
        await giveaway.channel.set_permissions(
            giveaway.channel.guild.default_role,
            send_messages=False
        )
        await giveaway.channel.edit(slowmode_delay=0)
    except discord.Forbidden:
        pass

    if giveaway.channel.id in active_giveaways:
        active_giveaways.pop(giveaway.channel.id)

    giveaway_logs.append({
        'channel_id': giveaway.channel.id,
        'hoster_id': giveaway.hoster.id,
        'prize': giveaway.prize,
        'winners': [w.id for w in giveaway.winners],
        'end_time': time.time()
    })

async def start_giveaway(interaction: discord.Interaction, winners: int, prize: str, number_range: str, hoster: discord.Member, duration: int = 0, target: Optional[int] = None):
    if not interaction.response.is_done():
        await interaction.response.defer()

    if interaction.channel.name != GIVEAWAY_CHANNEL_NAME and not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Use the giveaway channel!", ephemeral=True)
    if interaction.channel.id in active_giveaways:
        return await interaction.followup.send("❌ Giveaway already running here!", ephemeral=True)

    match = re.match(r"(\d+)-(\d+)", number_range)
    if not match:
        return await interaction.followup.send("❌ Invalid range!", ephemeral=True)
    low, high = int(match[1]), int(match[2])
    if low >= high:
        return await interaction.followup.send("❌ Invalid range!", ephemeral=True)

    target = target or random.randint(low, high)
    giveaway = Giveaway(hoster, prize, winners, (low, high), target, duration, interaction.channel)
    active_giveaways[interaction.channel.id] = giveaway

    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.channel.edit(slowmode_delay=2)

    embed = discord.Embed(
        title="🎉 NUMBER GUESS GIVEAWAY",
        description=(
            f"**Host:** {hoster.mention}\n**Range:** {low}-{high}\n**Prize:** {prize}\n"
            f"**Winners:** {winners}\n**Duration:** {'No limit' if duration == 0 else f'{duration} min'}"
        ),
        color=discord.Color.gold()
    )
    
    view = GiveawayView(giveaway)
    await interaction.edit_original_response(embed=embed, view=view)

    if duration > 0:
        giveaway.task = asyncio.create_task(schedule_giveaway_end(giveaway))

async def schedule_giveaway_end(giveaway):
    await asyncio.sleep(giveaway.duration * 60)
    if giveaway.channel.id in active_giveaways:
        await end_giveaway(giveaway)

@tree.command(name="giveaway", description="Start a number guessing giveaway")
@app_commands.describe(
    prize="Prize for the giveaway",
    author="User hosting the giveaway",
    number_range="Range for guessing (e.g. 1-100)",
    duration="How long the giveaway lasts (e.g. 1m, 30s, 2h)",
    winners="Number of winners needed (default: 1)",
    target="Optional target user"
)
@app_commands.checks.has_any_role(*ADMIN_ROLE_IDS)
async def giveaway(
    interaction: discord.Interaction,
    prize: str,
    author: discord.User,
    number_range: str,
    duration: str,
    winners: Optional[int] = 1,
    target: Optional[discord.User] = None
):
    if interaction.channel.id != 1363495611995001013:
        await interaction.response.send_message("This command can only be used in the giveaway channel.", ephemeral=True)
        return

    if winners < 1:
        await interaction.response.send_message("❌ Number of winners must be at least 1.", ephemeral=True)
        return

    try:
        start, end = map(int, number_range.split("-"))
        if start >= end:
            raise ValueError
    except:
        await interaction.response.send_message("❌ Invalid number range. Use a format like `1-100`.", ephemeral=True)
        return

    time_units = {"s": 1, "m": 60, "h": 3600}
    try:
        time_unit = duration[-1]
        time_value = int(duration[:-1])
        duration_seconds = time_value * time_units[time_unit]
        duration_minutes = duration_seconds // 60
    except:
        await interaction.response.send_message("❌ Invalid duration. Use `30s`, `1m`, `2h` etc.", ephemeral=True)
        return

    target_number = random.randint(start, end)

    giveaway = Giveaway(
        hoster=author,
        prize=prize,
        winners=winners,
        number_range=(start, end),
        target=target_number,
        duration=duration_minutes,
        channel=interaction.channel
    )
    
    active_giveaways[interaction.channel.id] = giveaway

    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.channel.edit(slowmode_delay=2)

    embed = discord.Embed(
        title="🎁 NUMBER GUESS GIVEAWAY",
        description=(
            f"**Host:** {author.mention}\n"
            f"**Range:** {number_range}\n"
            f"**Prize:** {prize}\n"
            f"**Winners Needed:** {winners}\n"
            f"**Duration:** {duration}\n\n"
            "Click the button below to enter your guess!"
        ),
        color=discord.Color.gold()
    )

    if target:
        embed.add_field(name="🎯 Target Player", value=target.mention)

    view = GiveawayView(giveaway)
    
    if interaction.response.is_done():
        message = await interaction.followup.send(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
    
    view.message = message
    giveaway.view = view

    if duration_minutes > 0:
        giveaway.task = asyncio.create_task(schedule_giveaway_end(giveaway))

@tree.command(name="stop_giveaway")
@auto_defer(ephemeral=True)
async def stop_giveaway(interaction: discord.Interaction):
    """Forcefully end the current giveaway in this channel"""
    giveaway = active_giveaways.get(interaction.channel.id)
    
    if not giveaway:
        return await interaction.followup.send("❌ No active giveaway in this channel!", ephemeral=True)
    
    if interaction.user.id != giveaway.hoster.id and not has_admin_role(interaction.user):
        return await interaction.followup.send(
            "❌ Only the giveaway host or admins can stop this!",
            ephemeral=True
        )
    
    embed = discord.Embed(
        title="🎉 Giveaway Ended by Admin",
        color=discord.Color.orange()
    )
    
    embed.add_field(
        name="Giveaway Details",
        value=(
            f"**Host:** {giveaway.hoster.mention}\n"
            f"**Range:** {giveaway.low}-{giveaway.high}\n"
            f"**Prize:** {giveaway.prize}\n"
            f"**Target Number:** ||{giveaway.target}||"
        ),
        inline=False
    )
    
    if giveaway.winners:
        winners_text = ", ".join(winner.mention for winner in giveaway.winners)
        embed.add_field(
            name=f"🏆 Winner{'s' if len(giveaway.winners) > 1 else ''}",
            value=winners_text or "No winners yet",
            inline=False
        )
    else:
        embed.add_field(
            name="❌ No Winners",
            value="No one guessed the correct number!",
            inline=False
        )
    
    if giveaway.task:
        giveaway.task.cancel()
    
    await interaction.channel.set_permissions(
        interaction.guild.default_role,
        send_messages=False
    )
    await interaction.channel.edit(slowmode_delay=0)
    
    active_giveaways.pop(interaction.channel.id, None)
    
    await interaction.followup.send(embed=embed)
    
    try:
        host_embed = discord.Embed(
            title="🎁 Giveaway Stopped",
            description=f"Your giveaway in {interaction.channel.mention} was ended by {interaction.user.mention}",
            color=discord.Color.blue()
        )
        host_embed.add_field(
            name="Results",
            value=(
                f"Winners: {len(giveaway.winners)}\n"
                f"Total Participants: {len(giveaway.guessed_users) if hasattr(giveaway, 'guessed_users') else 'Unknown'}"
            )
        )
        await giveaway.hoster.send(embed=host_embed)
    except discord.Forbidden:
        pass

# ============================================================
# TRADING COMMANDS
# ============================================================

@tree.command(name="trade_offer")
@auto_defer(ephemeral=False)
@app_commands.describe(user="User to trade with", yourseed="Seed you're offering", theirseed="Seed you want")
async def trade_offer(interaction: discord.Interaction, user: discord.Member, yourseed: str, theirseed: str):
    update_growing_seeds(interaction.user.id)
    update_growing_seeds(user.id)

    sender_id = interaction.user.id
    recipient_id = user.id

    sender_seed_obj = find_matching_seed(user_inventory[sender_id]["grown"], yourseed)
    recipient_seed_obj = find_matching_seed(user_inventory[recipient_id]["grown"], theirseed)

    if not sender_seed_obj:
        return await interaction.followup.send("❌ You don't have that grown seed to offer.", ephemeral=True)
    if not recipient_seed_obj:
        return await interaction.followup.send(f"❌ {user.mention} doesn't have that seed or it's still growing.", ephemeral=True)

    embed = discord.Embed(
        title="🔔 Trade Offer",
        description=(
            f"{interaction.user.mention} wants to trade with {user.mention}!\n\n"
            f"**{interaction.user.display_name} offers:** {pretty_seed(sender_seed_obj)}\n"
            f"**{user.display_name} would give:** {pretty_seed(recipient_seed_obj)}"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text="This trade offer will expire in 5 minutes")

    view = TradeView(interaction.user, user, sender_seed_obj, recipient_seed_obj)

    msg = await interaction.channel.send(
        f"{user.mention}, you received a trade offer from {interaction.user.mention}!",
        embed=embed,
        view=view
    )

    view.original_message = msg

    trade_offers[recipient_id].append({
        "sender_id": sender_id,
        "sender_seed_name": sender_seed_obj.name,
        "sender_seed_mut": sender_seed_obj.mutation,
        "recipient_seed_name": recipient_seed_obj.name,
        "recipient_seed_mut": recipient_seed_obj.mutation,
        "timestamp": time.time(),
        "original_message_id": msg.id,
        "trade_messages": [msg.id]
    })

    try:
        await user.send(
            f"You received a trade offer from {interaction.user.mention}!\n"
            f"They're offering: {pretty_seed(sender_seed_obj)}\n"
            f"They want: {pretty_seed(recipient_seed_obj)}\n"
            f"Check {interaction.channel.mention} to respond!"
        )
    except discord.Forbidden:
        pass


@tree.command(name="trade_offers")
@auto_defer(ephemeral=True)
async def view_trade_offers(interaction: discord.Interaction):
    user_id = interaction.user.id
    if isinstance(trade_offers.get(user_id), dict):
        trade_offers[user_id] = [trade_offers[user_id]]
    elif isinstance(trade_offers.get(user_id), str):
        trade_offers[user_id] = []
    elif isinstance(trade_offers.get(user_id), list):
        trade_offers[user_id] = [x for x in trade_offers[user_id] if isinstance(x, dict)]

    offers = trade_offers.get(user_id, [])
    if not offers:
        return await interaction.followup.send("📭 You have no pending trade offers.", ephemeral=True)

    now = time.time()
    trade_offers[user_id] = [offer for offer in offers if now - offer["timestamp"] <= 300]

    offers = trade_offers[user_id]
    if not offers:
        return await interaction.followup.send("📭 All trade offers expired.", ephemeral=True)

    shown = 0
    for offer in offers:
        try:
            sender = await bot.fetch_user(offer["sender_id"])
            sender_grown = user_inventory[offer["sender_id"]]["grown"]
            recipient_grown = user_inventory[user_id]["grown"]

            sender_seed = next(
                (s for s in sender_grown if s.name == offer["sender_seed_name"] and s.mutation == offer["sender_seed_mut"]),
                None
            )
            recipient_seed = next(
                (s for s in recipient_grown if s.name == offer["recipient_seed_name"] and s.mutation == offer["recipient_seed_mut"]),
                None
            )

            if not sender_seed or not recipient_seed:
                continue

            embed = discord.Embed(
                title=f"🔁 Trade Offer from {sender.display_name}",
                description=(
                    f"**They offer:** {pretty_seed(sender_seed)}\n"
                    f"**They want:** {pretty_seed(recipient_seed)}\n"
                    f"Sent <t:{int(offer['timestamp'])}:R>"
                ),
                color=discord.Color.blurple()
            )

            original_message = None
            if "original_message_id" in offer:
                try:
                    original_message = await interaction.channel.fetch_message(offer["original_message_id"])
                except:
                    pass

            view = TradeView(
                sender=sender,
                recipient=interaction.user,
                sender_seed=sender_seed,
                recipient_seed=recipient_seed,
                original_message=original_message,
                viewer=interaction.user,
                trade_messages=offer.get("trade_messages", [])
            )
            
            trade_msg = await interaction.channel.send(embed=embed, view=view)
            
            if "trade_messages" not in offer:
                offer["trade_messages"] = []
            offer["trade_messages"].append(trade_msg.id)
            
            shown += 1

        except Exception as e:
            print(f"Error showing trade offer: {e}")
            continue

    if shown == 0:
        await interaction.followup.send("❌ No valid trade offers could be shown.", ephemeral=True)
    else:
        await interaction.followup.send(f"📬 Shown {shown} trade offer(s).", ephemeral=True)

@tree.command(name="trade_logs")
@auto_defer(ephemeral=True)
async def trade_logs_command(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)
    if not trade_logs:
        return await interaction.followup.send("📭 No trade logs available.", ephemeral=True)

    embed = discord.Embed(title="📜 Recent Trade Logs", color=discord.Color.gold())
    for log in trade_logs[-10:][::-1]:
        from_user = await bot.fetch_user(log["from"])
        to_user = await bot.fetch_user(log["to"])
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(log["time"]))
        embed.add_field(
            name=f"{from_user.name} ➝ {to_user.name} @ {timestamp}",
            value=f"{from_user.name} gave {log['gave']}, got {log['got']}",
            inline=False
        )
    await interaction.followup.send(embed=embed, ephemeral=True)

# ============================================================
# MIDDLEMAN COMMAND
# ============================================================

@tree.command(name="apply_middleman", description="Apply for a middleman trade")
async def apply_middleman(interaction: discord.Interaction):
    blacklist_role = interaction.guild.get_role(1344056030153146448)
    if blacklist_role and blacklist_role in interaction.user.roles:
        return await interaction.response.send_message(
            "🚫 You are blacklisted from using the middleman system.",
            ephemeral=True
        )

    await interaction.response.send_modal(MiddlemanModal(bot, interaction))

# ============================================================
# ADMIN COMMANDS (kept for garden management if re-enabled)
# ============================================================

@tree.command(name="give_seed")
@auto_defer(ephemeral=True)
@app_commands.describe(user="User to give seed to", seed="Seed name")
async def give_seed(interaction: discord.Interaction, user: discord.Member, seed: str):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Not allowed", ephemeral=True)

    base, mut, seed = normalize_seed_name(seed)

    if base not in seeds and base not in limited_seeds:
        return await interaction.followup.send("❌ Invalid seed name.", ephemeral=True)

    grow_time = calculate_grow_time(base, interaction.user.id)
    allowed_mutations = None
    if base in limited_seeds:
        allowed_mutations = limited_seeds[base].get("mutations")

    seed_obj = GrowingSeed(
        base, 
        grow_time, 
        limited=base in limited_seeds,
        allowed_mutations=allowed_mutations
    )
    
    user_inventory[user.id]["growing"].append(seed_obj)
    await interaction.followup.send(f"✅ Gave {pretty_seed(seed_obj)} to {user.mention}")

@tree.command(name="give_sheckles")
@auto_defer(ephemeral=True)
@app_commands.describe(user="User to give sheckles to", amount="Amount of sheckles")
async def give_sheckles(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Not allowed", ephemeral=True)
    user_sheckles[user.id] += amount
    await interaction.followup.send(f"✅ Gave {amount} sheckles to {user.mention}")

@tree.command(name="growinstant")
@auto_defer(ephemeral=True)
@app_commands.describe(user="User whose plant to instantly grow", plant="Plant name")
async def growinstant(interaction: discord.Interaction, user: discord.Member, plant: str):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Not allowed", ephemeral=True)

    base, mut, _ = normalize_seed_name(plant)
    update_growing_seeds(user.id)

    growing = user_inventory[user.id]["growing"]
    match = next((s for s in growing if s.name == base and (mut is None or s.mutation == mut)), None)

    if not match:
        return await interaction.followup.send(f"❌ No matching growing seed found for {user.mention}.", ephemeral=True)

    grown_seed = GrowingSeed(
        match.name,
        0,
        mutation=match.mutation,
        limited=getattr(match, "limited", False)
    )
    
    growing.remove(match)
    user_inventory[user.id]["grown"].append(grown_seed)

    await interaction.followup.send(f"🌱 Instantly grew {pretty_seed(grown_seed)} for {user.mention}.", ephemeral=True)

# ============================================================
# BACKGROUND TASKS
# ============================================================

# Garden tasks — disabled, uncomment to re-enable:
#
# @tasks.loop(minutes=5)
# async def refresh_stock():
#     global current_stock, limited_seeds
#     limited_seeds = {
#         name: data for name, data in limited_seeds.items()
#         if time.time() < data["expires"]
#     }
#     current_stock = []
#     for seed, rarity in SEED_RARITIES.items():
#         if random.random() < RARITY_CHANCES[rarity]:
#             current_stock.append(seed)
#     commons = [s for s, r in SEED_RARITIES.items() if r == "Common"]
#     commons_in_stock = [s for s in current_stock if s in commons]
#     needed = max(2 - len(commons_in_stock), 0)
#     if needed > 0:
#         available_commons = list(set(commons) - set(commons_in_stock))
#         random.shuffle(available_commons)
#         current_stock += available_commons[:needed]
#     random.shuffle(current_stock)
#
# @tasks.loop(hours=24)
# async def rotate_seasons():
#     global current_season
#     current_idx = next((i for i, s in enumerate(SEASONS) if s["name"] == current_season["name"]), 0)
#     next_idx = (current_idx + 1) % len(SEASONS)
#     current_season = SEASONS[next_idx]
#     channel = bot.get_channel(YOUR_ANNOUNCEMENT_CHANNEL_ID)
#     if channel:
#         boosted = ", ".join(current_season["boosted_seeds"])
#         await channel.send(f"🌱 The season has changed to **{current_season['name']}**! Boosted seeds: {boosted}")
#
# @tasks.loop(minutes=30)
# async def check_plant_events():
#     global current_plant_event
#     if current_plant_event and time.time() > current_plant_event["end_time"]:
#         current_plant_event = None
#     if not current_plant_event and random.random() < 0.05:
#         event = random.choice(PLANT_EVENTS)
#         event["start_time"] = time.time()
#         event["end_time"] = time.time() + event["duration"]
#         current_plant_event = event
#         channel = bot.get_channel(YOUR_ANNOUNCEMENT_CHANNEL_ID)
#         if channel:
#             desc = (f"All plants grow {event['multiplier']*100}% faster!"
#                    if event["effect"] == "speed"
#                    else f"All plants take {event['delay']//60} extra minutes to grow!")
#             await channel.send(
#                 f"🌿 **PLANT EVENT: {event['name']}** 🌿\n"
#                 f"{desc}\n"
#                 f"Duration: {event['duration']//3600} hours"
#             )

@tasks.loop(minutes=5)
async def cleanup_expired():
    """Clean up expired trades and boosts"""
    current_time = time.time()
    
    for user_id, offer in list(trade_offers.items()):
        if isinstance(offer, dict) and current_time - offer.get("timestamp", 0) > 300:
            trade_offers.pop(user_id)
            try:
                sender = await bot.fetch_user(offer["sender_id"])
                recipient = await bot.fetch_user(user_id)
                await sender.send(f"❌ Your trade offer to {recipient.name} has expired.")
            except:
                pass

    for user_id, boosts in list(user_active_boosts.items()):
        for boost_type, boost_data in list(boosts.items()):
            if boost_data["expires"] < current_time:
                del user_active_boosts[user_id][boost_type]

# ============================================================
# MESSAGE HANDLER
# ============================================================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # ── JSON import handler ──────────────────────────────────
    if message.author.id in pending_imports and message.attachments:
        pending = pending_imports[message.author.id]
        if message.channel.id == pending["channel_id"]:
            attachment = next((a for a in message.attachments if a.filename.endswith(".json")), None)
            if attachment:
                del pending_imports[message.author.id]
                try:
                    raw = await attachment.read()
                    data = json.loads(raw.decode("utf-8"))

                    if not isinstance(data, list):
                        await message.reply("❌ Invalid format — expected a JSON array.")
                        return

                    mode = pending["mode"]
                    added = 0
                    skipped = 0

                    with get_db() as conn:
                        if mode == "replace":
                            conn.execute("DELETE FROM leaks")
                            conn.commit()

                        for entry in data:
                            try:
                                conn.execute(
                                    "INSERT INTO leaks (name, link, payhip_url, added_by, added_at) VALUES (?, ?, ?, ?, ?)",
                                    (
                                        entry["name"].strip(),
                                        entry["link"].strip(),
                                        entry["payhip_url"].strip(),
                                        entry.get("added_by", message.author.id),
                                        entry.get("added_at", time.time())
                                    )
                                )
                                added += 1
                            except (sqlite3.IntegrityError, KeyError):
                                skipped += 1
                        conn.commit()

                    embed = discord.Embed(
                        title="✅ Import Complete",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="Mode", value=mode.capitalize(), inline=True)
                    embed.add_field(name="Added", value=str(added), inline=True)
                    embed.add_field(name="Skipped", value=str(skipped), inline=True)
                    if skipped > 0:
                        embed.set_footer(text="Skipped entries already exist or had missing fields.")
                    await message.reply(embed=embed)
                    return

                except (json.JSONDecodeError, UnicodeDecodeError):
                    await message.reply("❌ Couldn't parse the file — make sure it's a valid JSON file.")
                    return

    # ── Sheckle counting ────────────────────────────────────
    user_message_counts[message.author.id] += 1
    if user_message_counts[message.author.id] % MESSAGES_PER_SHECKLE == 0:
        user_sheckles[message.author.id] += 1

    current_giveaway = active_giveaways.get(message.channel.id)
    if current_giveaway:
        if current_giveaway.end_time is not None and time.time() > current_giveaway.end_time:
            try:
                await message.delete()
                await message.author.send("❌ This giveaway has already ended!", delete_after=10)
            except:
                pass
            return

        if message.content.strip().isdigit():
            try:
                guess = int(message.content.strip())
                
                if guess < current_giveaway.low or guess > current_giveaway.high:
                    try:
                        await message.reply(f"❌ Guess must be between {current_giveaway.low}-{current_giveaway.high}!", delete_after=5)
                    except:
                        pass
                    return
                
                if current_giveaway.check_guess(message.author, guess):
                    current_giveaway.winners.add(message.author)
                    
                    try:
                        await message.author.send(
                            f"🎉 You guessed the correct number `{guess}`!\n"
                            f"Please contact {current_giveaway.hoster.mention} to claim your prize."
                        )
                    except:
                        await message.channel.send(
                            f"🎉 {message.author.mention} guessed correctly but can't receive DMs. Please contact the host!"
                        )
                    
                    if len(current_giveaway.winners) >= current_giveaway.winners_required:
                        await end_giveaway(current_giveaway)
                
                try:
                    await message.add_reaction("🔢")
                except:
                    pass
                    
            except ValueError:
                pass
        else:
            try:
                await message.delete()
            except:
                pass
            return

    await bot.process_commands(message)


# ============================================================
# LEAKS SYSTEM
# ============================================================

import sqlite3
import json

LEAKS_CHANNEL_NAME = "𝙇𝙀𝘼𝙆𝙎-𝙍𝙀𝙌𝙐𝙀𝙎𝙏"
PAGE_SIZE = 15
SEARCH_PAGE_SIZE = 10

def get_db():
    conn = sqlite3.connect("leaks.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leaks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                link TEXT NOT NULL,
                payhip_url TEXT NOT NULL,
                added_by INTEGER NOT NULL,
                added_at REAL NOT NULL
            )
        """)
        conn.commit()

def search_leaks(query: str):
    query = query.lower().strip()
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM leaks").fetchall()
    return [r for r in rows if query in r["name"].lower()]

def get_all_leaks():
    with get_db() as conn:
        return conn.execute("SELECT * FROM leaks ORDER BY name ASC").fetchall()

def add_leak(name: str, link: str, payhip_url: str, user_id: int):
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO leaks (name, link, payhip_url, added_by, added_at) VALUES (?, ?, ?, ?, ?)",
                (name.strip(), link.strip(), payhip_url.strip(), user_id, time.time())
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def delete_leak(name: str):
    with get_db() as conn:
        cursor = conn.execute("DELETE FROM leaks WHERE LOWER(name) = ?", (name.lower().strip(),))
        conn.commit()
        return cursor.rowcount > 0

# Initialize DB on load
init_db()

# Tracks pending imports: user_id -> {"mode": "merge"|"replace", "channel_id": int}
pending_imports = {}


def build_list_embed(rows, page: int) -> discord.Embed:
    total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    start = page * PAGE_SIZE
    chunk = rows[start:start + PAGE_SIZE]
    embed = discord.Embed(
        title="📦 All Available Leaks",
        description="\n".join(f"• **{r['name']}**" for r in chunk),
        color=discord.Color.blurple()
    )
    embed.set_footer(
        text=f"Page {page + 1} of {total_pages} | {len(rows)} total leaks | Use /leaks <name> to get links"
    )
    return embed


def build_search_embed(results, query: str, page: int) -> discord.Embed:
    total_pages = max(1, (len(results) + SEARCH_PAGE_SIZE - 1) // SEARCH_PAGE_SIZE)
    start = page * SEARCH_PAGE_SIZE
    chunk = results[start:start + SEARCH_PAGE_SIZE]
    embed = discord.Embed(
        title=f"🔍 Results for \"{query}\"",
        description="Multiple matches found. Be more specific to see Payhip links.",
        color=discord.Color.blurple()
    )
    for row in chunk:
        embed.add_field(
            name=row["name"],
            value=f"[Download Link]({row['link']})",
            inline=False
        )
    embed.set_footer(text=f"Page {page + 1} of {total_pages} | {len(results)} results")
    return embed


class LeaksListView(discord.ui.View):
    def __init__(self, rows, page: int = 0):
        super().__init__(timeout=120)
        self.rows = rows
        self.page = page
        self.total_pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.gray)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        await interaction.response.edit_message(embed=build_list_embed(self.rows, self.page), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.gray)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        await interaction.response.edit_message(embed=build_list_embed(self.rows, self.page), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class LeaksSearchView(discord.ui.View):
    def __init__(self, results, query: str, page: int = 0):
        super().__init__(timeout=120)
        self.results = results
        self.query = query
        self.page = page
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

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class ImportModeView(discord.ui.View):
    def __init__(self, admin_id: int, channel_id: int):
        super().__init__(timeout=60)
        self.admin_id = admin_id
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
        await interaction.response.edit_message(
            content="✅ Mode set to **Merge**. Now send the `.json` file in this channel.",
            view=self
        )

    @discord.ui.button(label="Replace", style=discord.ButtonStyle.red)
    async def replace(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending_imports[self.admin_id] = {"mode": "replace", "channel_id": self.channel_id}
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="✅ Mode set to **Replace** (⚠️ this will wipe the current database). Now send the `.json` file in this channel.",
            view=self
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ============================================================
# LEAKS COMMANDS
# ============================================================

@tree.command(name="leakscreate", description="Add a new leak entry (admin only)")
@auto_defer(ephemeral=False)
@app_commands.describe(
    leak="Name of the leak",
    link="Download/access link",
    payhip="Payhip product link"
)
async def leaks_create(interaction: discord.Interaction, leak: str, link: str, payhip: str):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)

    success = add_leak(leak, link, payhip, interaction.user.id)
    if not success:
        return await interaction.followup.send(
            f"❌ A leak named **{leak}** already exists. Use `/leaksdelete` first if you want to replace it.",
            ephemeral=True
        )

    embed = discord.Embed(title="✅ Leak Added", color=discord.Color.green())
    embed.add_field(name="Name", value=leak, inline=False)
    embed.add_field(name="Download Link", value=link, inline=False)
    embed.add_field(name="Payhip Link", value=payhip, inline=False)
    embed.set_footer(text=f"Added by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


@tree.command(name="leaks", description="Search for a game asset leak by name")
@auto_defer(ephemeral=False)
@app_commands.describe(leak="Name or partial name to search for")
async def leaks_search(interaction: discord.Interaction, leak: str):
    if interaction.channel.name != LEAKS_CHANNEL_NAME:
        return await interaction.followup.send(
            "❌ This command can only be used in #𝙇𝙀𝘼𝙆𝙎-𝙍𝙀𝙌𝙐𝙀𝙎𝙏.", ephemeral=True
        )

    results = search_leaks(leak)

    if not results:
        return await interaction.followup.send(f"❌ No leaks found matching **{leak}**.")

    # Single result — show full embed with payhip, no pagination needed
    if len(results) == 1:
        row = results[0]
        embed = discord.Embed(title=f"📦 {row['name']}", color=discord.Color.blurple())
        embed.add_field(name="⬇️ Download", value=f"[Click here]({row['link']})", inline=True)
        embed.add_field(name="🛒 Buy on Payhip", value=f"[Click here]({row['payhip_url']})", inline=True)
        embed.set_footer(text=f"Added <t:{int(row['added_at'])}:R> by user ID {row['added_by']}")
        return await interaction.followup.send(embed=embed)

    # Multiple results — paginated, no payhip
    view = LeaksSearchView(results, leak)
    await interaction.followup.send(embed=build_search_embed(results, leak, 0), view=view)


@tree.command(name="leakslist", description="List all available leaks")
@auto_defer(ephemeral=False)
async def leaks_list(interaction: discord.Interaction):
    if interaction.channel.name != LEAKS_CHANNEL_NAME:
        return await interaction.followup.send(
            "❌ This command can only be used in #𝙇𝙀𝘼𝙆𝙎-𝙍𝙀𝙌𝙐𝙀𝙎𝙏.", ephemeral=True
        )

    rows = get_all_leaks()

    if not rows:
        return await interaction.followup.send("📭 No leaks in the database yet.")

    view = LeaksListView(rows)
    await interaction.followup.send(embed=build_list_embed(rows, 0), view=view)


@tree.command(name="leaksdelete", description="Delete a leak entry (admin only)")
@auto_defer(ephemeral=False)
@app_commands.describe(leak="Exact name of the leak to delete")
async def leaks_delete(interaction: discord.Interaction, leak: str):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)

    success = delete_leak(leak)
    if not success:
        return await interaction.followup.send(
            f"❌ No leak found with the name **{leak}**. Names are case-insensitive."
        )

    embed = discord.Embed(
        title="🗑️ Leak Deleted",
        description=f"**{leak}** has been removed from the database.",
        color=discord.Color.red()
    )
    await interaction.followup.send(embed=embed)


@tree.command(name="leaksexport", description="Export the entire leaks database as a JSON file (admin only)")
@auto_defer(ephemeral=True)
async def leaks_export(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)

    rows = get_all_leaks()

    if not rows:
        return await interaction.followup.send("📭 No leaks in the database to export.", ephemeral=True)

    data = [
        {
            "name": r["name"],
            "link": r["link"],
            "payhip_url": r["payhip_url"],
            "added_by": r["added_by"],
            "added_at": r["added_at"]
        }
        for r in rows
    ]

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
        "**Replace** — wipe the database and import fresh",
        view=view,
        ephemeral=True
    )


@tree.command(name="leaksedit", description="Edit an existing leak entry (admin only)")
@auto_defer(ephemeral=True)
@app_commands.describe(
    leak="Exact name of the leak to edit",
    newname="New name (leave blank to keep current)",
    newlink="New download link (leave blank to keep current)",
    newpayhip="New Payhip link (leave blank to keep current)"
)
async def leaks_edit(
    interaction: discord.Interaction,
    leak: str,
    newname: Optional[str] = None,
    newlink: Optional[str] = None,
    newpayhip: Optional[str] = None
):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Admins only.", ephemeral=True)

    if not any([newname, newlink, newpayhip]):
        return await interaction.followup.send(
            "❌ You need to provide at least one field to update.", ephemeral=True
        )

    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM leaks WHERE LOWER(name) = ?", (leak.lower().strip(),)
        ).fetchone()

        if not row:
            return await interaction.followup.send(
                f"❌ No leak found with the name **{leak}**.", ephemeral=True
            )

        updated_name = newname.strip() if newname else row["name"]
        updated_link = newlink.strip() if newlink else row["link"]
        updated_payhip = newpayhip.strip() if newpayhip else row["payhip_url"]

        try:
            conn.execute(
                "UPDATE leaks SET name = ?, link = ?, payhip_url = ? WHERE id = ?",
                (updated_name, updated_link, updated_payhip, row["id"])
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return await interaction.followup.send(
                f"❌ A leak named **{updated_name}** already exists.", ephemeral=True
            )

    embed = discord.Embed(title="✏️ Leak Updated", color=discord.Color.yellow())
    embed.add_field(name="Name", value=f"~~{row['name']}~~ → {updated_name}" if newname else updated_name, inline=False)
    embed.add_field(name="Download Link", value=updated_link, inline=False)
    embed.add_field(name="Payhip Link", value=updated_payhip, inline=False)
    embed.set_footer(text=f"Edited by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed, ephemeral=True)


bot.run(os.getenv("BOT_TOKEN"))
