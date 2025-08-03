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
ADMIN_ROLES = ["𝓞𝔀𝓷𝓮𝓻 👑", "Tuff nonchalant aurafarmer sigma pro admin", "Administrator™🌟"]
ADMIN_ROLE_IDS = [1342599762993741855, 1385318110747164775, 1343263454114480161]
MESSAGES_PER_SHECKLE = 10  # Number of messages needed to earn 1 sheckle

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
    # Add more achievements as needed
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
    "Strawberry": (10, 50), # (Sheckles, Messages For quest)
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

trade_offers = {}  # user_id -> dict with keys: sender_id, seed_name, timestamp
trade_logs = []

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
        # ✅ Check confirmation
        if self.confirm.value.strip().lower() != "yes i understand.":
            blacklist_role = interaction.guild.get_role(1344056030153146448)
            if blacklist_role:
                await interaction.user.add_roles(blacklist_role, reason="Failed to confirm MM rules")
            return await interaction.response.send_message(
                "🚫 You failed to confirm properly and were blacklisted.",
                ephemeral=True
            )
    
        guild = interaction.guild
    
        # Get the ticket category
        ticket_category = guild.get_channel(TICKET_CATEGORY_ID)
        if not ticket_category or not isinstance(ticket_category, discord.CategoryChannel):
            return await interaction.response.send_message("❌ Ticket category not found.", ephemeral=True)
    
        middleman_role = guild.get_role(1348072637972090880)
        if not middleman_role:
            return await interaction.response.send_message("❌ Middleman role not found.", ephemeral=True)
    
        # ✅ Create the ticket
        ticket_number = len([c for c in ticket_category.channels if isinstance(c, discord.TextChannel) and c.name.startswith("ticket-")]) + 1
        
        # Create overwrites
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
    
        # ✅ Create the embed with all Q&A
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
            # Send the embed + close button into the ticket
            await ticket_channel.send(
                content=middleman_role.mention,
                embed=embed,
                view=CloseTicketView(self.bot)
            )
            
            # Confirm to the user
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

class TradeView(View):
    def __init__(self, sender, recipient, sender_seed, recipient_seed):
        super().__init__(timeout=300)  # 5 minute timeout
        self.sender = sender
        self.recipient = recipient
        self.sender_seed = sender_seed
        self.recipient_seed = recipient_seed
    
    @discord.ui.button(label="Accept Trade", style=ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.recipient.id:
            return await interaction.followup.send("❌ This trade isn't for you!", ephemeral=True)
        
        # Perform the trade
        update_growing_seeds(self.sender.id)
        update_growing_seeds(self.recipient.id)
        
        sender_grown = user_inventory[self.sender.id]["grown"]
        recipient_grown = user_inventory[self.recipient.id]["grown"]
        
        # Find exact seeds
        sender_seed = next((s for s in sender_grown if s.name == self.sender_seed.name and s.mutation == self.sender_seed.mutation), None)
        recipient_seed = next((s for s in recipient_grown if s.name == self.recipient_seed.name and s.mutation == self.recipient_seed.mutation), None)
        
        if not sender_seed or not recipient_seed:
            trade_offers.pop(self.recipient.id, None)
            return await interaction.followup.send("❌ One or both seeds no longer available.", ephemeral=True)
        
        # Perform swap
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
        
        trade_offers.pop(self.recipient.id, None)
        
        # Update the message
        embed = discord.Embed(
            title="✅ Trade Completed",
            description=(
                f"{self.sender.mention} gave {pretty_seed(sender_seed)}\n"
                f"{self.recipient.mention} gave {pretty_seed(recipient_seed)}"
            ),
            color=discord.Color.green()
        )
        
        self.accept.disabled = True
        self.decline.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
        
        # Notify both parties
        try:
            await self.sender.send(
                f"✅ Your trade with {self.recipient.mention} was accepted!\n"
                f"You received: {pretty_seed(recipient_seed)}\n"
                f"You gave: {pretty_seed(sender_seed)}"
            )
        except:
            pass

    @discord.ui.button(label="Decline Trade", style=ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id != self.recipient.id:
            return await interaction.followup.send("❌ This trade isn't for you!", ephemeral=True)
            
        trade_offers.pop(self.recipient.id, None)
            
        embed = discord.Embed(
            title="❌ Trade Declined",
            description=f"{self.recipient.mention} declined the trade offer from {self.sender.mention}",
            color=discord.Color.red()
        )
            
        self.accept.disabled = True
        self.decline.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)
            
        try:
            await self.sender.send(f"❌ {self.recipient.mention} declined your trade offer.")
        except:
            pass

class Giveaway:
    def __init__(self, hoster, prize, winners, number_range, target, duration, channel):
        self.hoster = hoster
        self.prize = prize
        self.winners_required = winners
        self.low, self.high = number_range
        self.target = target
        self.duration = duration
        self.channel = channel
        self.winners = set()
        self.guessed_users = {}
        self.end_time = time.time() + (duration * 60) if duration > 0 else float('inf')
        self.task = None

    def check_guess(self, user, guess):
        if user.id == self.hoster.id:
            return None
        if user.id not in self.guessed_users:
            self.guessed_users[user.id] = []
        self.guessed_users[user.id].append(guess)
        return guess == self.target

class GiveawayView(discord.ui.View):
    def __init__(self, giveaway: Giveaway):
        super().__init__(timeout=None)
        self.giveaway = giveaway
    
    @discord.ui.button(label="Join Giveaway", style=discord.ButtonStyle.green, custom_id="join_giveaway")
    async def join_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GuessModal(self.giveaway))
    
    @discord.ui.button(label="Participants", style=discord.ButtonStyle.blurple, custom_id="view_participants")
    async def view_participants(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show paginated list of participants"""
        participants = list(self.giveaway.participants)
        total_pages = max(1, (len(participants) + 9) // 10)  # 10 per page
        
        if not participants:
            embed = discord.Embed(
                title="Giveaway Participants",
                description="No participants yet!",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Create initial participants embed (page 0)
        embed = self.create_participants_embed(participants, 0, total_pages)
        view = ParticipantsView(self.giveaway, participants, 0, total_pages)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @discord.ui.button(label="My Guesses", style=discord.ButtonStyle.gray, custom_id="my_guesses")
    async def my_guesses(self, interaction: discord.Interaction, button: discord.ui.Button):
        guesses = self.giveaway.guesses.get(interaction.user.id, [])
        embed = discord.Embed(
            title="Your Guesses",
            color=discord.Color.blue()
        )
        
        if not guesses:
            embed.description = "You haven't made any guesses yet!"
        else:
            embed.description = "\n".join(f"• {guess}" for guess in guesses)
            embed.set_footer(text=f"Total guesses: {len(guesses)}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    def create_participants_embed(self, participants: list, page: int, total_pages: int) -> discord.Embed:
        """Helper function to create participants embed for a specific page"""
        start_idx = page * 10
        end_idx = min(start_idx + 10, len(participants))
        
        embed = discord.Embed(
            title=f"Giveaway Participants (Page {page + 1}/{total_pages})",
            color=discord.Color.blue()
        )
        
        participant_list = []
        for i in range(start_idx, end_idx):
            participant = participants[i]
            participant_list.append(f"{i + 1}. {participant.mention}")
        
        embed.description = "\n".join(participant_list) or "No participants on this page"
        embed.set_footer(text=f"Total participants: {len(participants)}")
        
        return embed

def create_giveaway_embed(giveaway: Giveaway) -> discord.Embed:
    """Creates an embed for the giveaway with all relevant information"""
    embed = discord.Embed(
        title="🎉 NUMBER GUESS GIVEAWAY 🎉",
        description=f"Hosted by {giveaway.hoster.mention}",
        color=discord.Color.gold()
    )
    
    # Main prize information
    embed.add_field(
        name="🏆 Prize",
        value=giveaway.prize,
        inline=False
    )
    
    # Game details
    embed.add_field(
        name="🔢 Number Range",
        value=f"{giveaway.low}-{giveaway.high}",
        inline=True
    )
    
    embed.add_field(
        name="🎯 Winners Needed",
        value=str(giveaway.winners_required),
        inline=True
    )
    
    # Time information
    if giveaway.duration_minutes > 0:
        remaining = max(0, giveaway.end_time - time.time())
        mins, secs = divmod(int(remaining), 60)
        embed.add_field(
            name="⏳ Time Remaining",
            value=f"{mins}m {secs}s",
            inline=True
        )
    else:
        embed.add_field(
            name="⏳ Duration",
            value="No time limit",
            inline=True
        )
    
    # Participation stats
    embed.add_field(
        name="👥 Participants",
        value=f"{len(giveaway.participants)} joined",
        inline=True
    )
    
    embed.add_field(
        name="🏆 Winners Found",
        value=f"{len(giveaway.winners)}/{giveaway.winners_required}",
        inline=True
    )
    
    # Footer with instructions
    embed.set_footer(
        text="Click 'Join Giveaway' to participate!",
        icon_url=giveaway.hoster.display_avatar.url
    )
    
    return embed


class ParticipantsView(discord.ui.View):
    def __init__(self, giveaway: Giveaway, participants: list, current_page: int, total_pages: int):
        super().__init__(timeout=60)
        self.giveaway = giveaway
        self.participants = participants
        self.current_page = current_page
        self.total_pages = total_pages
        
        # Disable navigation buttons when appropriate
        self.prev_button.disabled = current_page == 0
        self.next_button.disabled = current_page >= total_pages - 1
    
    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.gray)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await self.update_view(interaction)
    
    @discord.ui.button(label="Back", style=discord.ButtonStyle.blurple)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Return to main giveaway view
        embed = create_giveaway_embed(self.giveaway)
        view = GiveawayView(self.giveaway)
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            await self.update_view(interaction)
    
    async def update_view(self, interaction: discord.Interaction):
        """Update the message with the new page"""
        embed = GiveawayView(self.giveaway).create_participants_embed(
            self.participants,
            self.current_page,
            self.total_pages
        )
        
        # Update button states
        self.prev_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def on_timeout(self):
        """Disable all buttons when view times out"""
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.NotFound:
            pass  # Message was already deleted


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
        
        # Keep the view active with the current selection
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
        
        # Add regular seeds
        for seed in regular_seeds:
            cost, quest = seeds[seed]
            rarity = SEED_RARITIES.get(seed, "Unknown")
            options.append(discord.SelectOption(
                label=f"{seed} - {cost} sheckles",
                description=f"{rarity} | Quest: {quest} messages",
                value=f"seed_{seed}"
            ))
        
        # Add limited seeds
        for name, data in limited_seeds.items():
            time_left = max(0, int((data["expires"] - time.time()) // 60))
            options.append(discord.SelectOption(
                label=f"🌟 {name} - {data['sheckles']} sheckles",
                description=f"Limited | {time_left}min left | Quest: {data['quest']}",
                value=f"limited_{name}"
            ))
        
        # Add fertilizers
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
        
        # Defer the response first to prevent timeout
        await interaction.response.defer(ephemeral=True)
        
        try:
            if value.startswith("seed_"):
                await self.handle_seed_purchase(interaction, value[5:], is_limited=False)
            elif value.startswith("limited_"):
                await self.handle_seed_purchase(interaction, value[8:], is_limited=True)
            elif value.startswith("fert_"):
                await self.handle_fertilizer_purchase(interaction, value[5:])
            
            # Get the original message to edit the view
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
    
        # Deduct sheckles
        user_sheckles[user_id] -= sheckles_required
        
        # Calculate grow time
        grow_time = calculate_grow_time(seed_name, user_id)
        
        # Create seed object
        seed_obj = GrowingSeed(
            seed_name,
            grow_time,
            limited=is_limited,
            allowed_mutations=allowed_mutations
        )
        
        # Add to inventory
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
        await interaction.followup.send("🚫 Removal cancelled.", ephemeral=True)

class Giveaway:
    def __init__(self, hoster, prize, winners, number_range, target, duration, channel):
        self.hoster = hoster
        self.prize = prize
        self.winners_required = winners
        self.low, self.high = number_range
        self.target = target
        self.duration = duration
        self.channel = channel
        self.winners = set()
        self.guessed_users = {}
        self.end_time = asyncio.get_event_loop().time() + (duration * 60 if duration > 0 else float('inf'))
        self.task = None

    def check_guess(self, user, guess):
        if user.id == self.hoster.id:
            return None
        if user.id not in self.guessed_users:
            self.guessed_users[user.id] = []
        self.guessed_users[user.id].append(guess)
        return guess == self.target

class GrowingSeed:
    def __init__(self, name, grow_duration, mutation=None, limited=False, allowed_mutations=None):
        self.name = name
        self.finish_time = time.time() + grow_duration  # Use grow_duration to clarify
        self.limited = limited
        self.mutation = mutation or self.determine_mutation(name, allowed_mutations)

    def determine_mutation(self, plant_name, allowed_mutations=None):
        if allowed_mutations is not None:
            # Only consider allowed mutations (either global or specific to this plant)
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

        # Normal mutation logic if no restriction
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
    """Calculate grow time with all modifiers"""
    # Base grow times (in seconds) - need to be defined
    BASE_GROW_TIMES = {
        "Carrot": 300,      # 5 minutes
        "Strawberry": 300,  # 5 minutes
        "Potato": 300,      # 5 minutes
        "Bamboo": 300,      # 5 minutes
        "Ember Lily": 300,  # 5 minutes
        "Sugar Apple": 300, # 5 minutes
        "Beanstalk": 300   # 5 minutes
    }
    
    grow_time = BASE_GROW_TIMES.get(base_seed, 300)  # Default to 5 minutes
    
    # Season boost
    if base_seed in current_season["boosted_seeds"]:
        grow_time *= 0.8  # 20% faster
    
    # Plant event modifier
    if current_plant_event:
        if current_plant_event["effect"] == "delay":
            grow_time += current_plant_event["delay"]
        elif current_plant_event["effect"] == "speed":
            grow_time *= current_plant_event["multiplier"]
    
    # Fertilizer effects (would check user's active boosts)
    if user_active_boosts.get(user_id, {}).get("growth_boost"):
        boost = user_active_boosts[user_id]["growth_boost"]
        if time.time() < boost["expires"]:
            grow_time *= boost["multiplier"]
    
    return max(30, grow_time)  # Ensure minimum 30 second grow time

def check_achievements(user_id):
    """Check for new achievement unlocks"""
    new_achievements = []
    for name, data in achievement_definitions.items():
        if name not in user_achievements[user_id] and data["condition"](user_id):
            user_achievements[user_id].append(name)
            new_achievements.append(name)
    return new_achievements

def has_admin_role(member):
    return any(role.id in ADMIN_ROLE_IDS for role in member.roles)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    bot.add_view(CloseTicketView(bot))
    refresh_stock.start()

    rotate_seasons.start()
    check_plant_events.start()
    cleanup_expired.start()
    
    try:
        print("Syncing commands...")
        synced = await tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Failed to sync: {e}")

async def end_giveaway(giveaway):
    if giveaway.task:
        giveaway.task.cancel()

    for winner in giveaway.winners:
        try:
            await winner.send(
                f"🎉 You won the giveaway in {giveaway.channel.mention}!\n**Prize:** {giveaway.prize}\nContact {giveaway.hoster.mention} to claim your reward!"
            )
        except:
            pass

    winners_text = ", ".join(w.mention for w in giveaway.winners) if giveaway.winners else "No winners"
    embed = discord.Embed(
        title="🎉 Giveaway Ended",
        description=f"**Prize:** {giveaway.prize}\n**Target:** {giveaway.target}\n**Winners:** {winners_text}",
        color=discord.Color.green()
    )
    await giveaway.channel.send(embed=embed)
    await giveaway.channel.set_permissions(giveaway.channel.guild.default_role, send_messages=False)
    active_giveaways.pop(giveaway.channel.id, None)

@tree.command(name="add_limited_seed")
@auto_defer(ephemeral=True)
@app_commands.describe(
    name="Seed name",
    sheckles="Sheckle cost",
    quest_value="Quest value",
    duration_minutes="How long it's available",
    mutations="Optional comma-separated list of possible mutations (leave blank to allow all)"
)
async def add_limited_seed(
    interaction: discord.Interaction,
    name: str,
    sheckles: int,
    quest_value: int,
    duration_minutes: int,
    mutations: str = ""
):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Not allowed", ephemeral=True)

    normalized_name, _, _ = normalize_seed_name(name)

    # If mutations is blank, allow all mutations (None = unrestricted)
    if not mutations.strip():
        mutation_list = None
    else:
        # Parse and validate mutations
        mutation_list = [m.strip().title() for m in mutations.split(",") if m.strip()]
        
        # Get valid mutations
        valid_mutations = list(mutations_global := mutations["global"].keys())
        if normalized_name in mutations["specific"]:
            valid_mutations += mutations["specific"][normalized_name].keys()

        # Check if any are invalid
        invalid = [m for m in mutation_list if m not in valid_mutations]
        if invalid:
            return await interaction.followup.send(
                f"❌ Invalid mutation(s): {', '.join(invalid)}", ephemeral=True
            )

    # Save to limited_seeds
    limited_seeds[normalized_name] = {
        "sheckles": sheckles,
        "quest": quest_value,
        "expires": time.time() + (duration_minutes * 60),
        "mutations": mutation_list  # None = allow all, list = restricted
    }

    mut_display = "All mutations allowed" if mutation_list is None else ", ".join(mutation_list)
    await interaction.followup.send(
        f"✅ Limited seed **{normalized_name}** added for {duration_minutes} minutes.\n"
        f"🔁 Mutations: {mut_display}"
    )

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

@tree.command(name="stop_giveaway")
@auto_defer(ephemeral=True)
async def stop_giveaway(interaction: discord.Interaction):
    """Forcefully end the current giveaway in this channel"""
    giveaway = active_giveaways.get(interaction.channel.id)
    
    if not giveaway:
        return await interaction.followup.send("❌ No active giveaway in this channel!", ephemeral=True)
    
    # Check permissions - host or admin can stop
    if interaction.user.id != giveaway.hoster.id and not has_admin_role(interaction.user):
        return await interaction.followup.send(
            "❌ Only the giveaway host or admins can stop this!",
            ephemeral=True
        )
    
    # Build results embed
    embed = discord.Embed(
        title="🎉 Giveaway Ended by Admin",
        color=discord.Color.orange()
    )
    
    # Add basic info
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
    
    # Add winners if any
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
    
    # Cancel the scheduled end task if it exists
    if giveaway.task:
        giveaway.task.cancel()
    
    # Clean up channel permissions
    await interaction.channel.set_permissions(
        interaction.guild.default_role,
        send_messages=False
    )
    await interaction.channel.edit(slowmode_delay=0)
    
    # Remove from active giveaways
    active_giveaways.pop(interaction.channel.id, None)
    
    # Send the results
    await interaction.followup.send(embed=embed)
    
    # DM the host with full results
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
        pass  # Host has DMs disabled
        
        
        
@tree.command(name="inventory")
@auto_defer(ephemeral=True)
async def inventory(interaction: discord.Interaction):
    try:
        """View your inventory with interactive controls"""
        update_growing_seeds(interaction.user.id)
        new_achievements = check_achievements(interaction.user.id)
        
        inv = user_inventory[interaction.user.id]
        grown_list = [pretty_seed(seed) for seed in inv["grown"]]
        growing_list = [
            f"{pretty_seed(seed)} [{max(0, int(seed.finish_time - time.time()))}s]" 
            for seed in inv["growing"]
        ]
        
        embed = discord.Embed(title="🌱 Your Garden", color=discord.Color.green())
        embed.add_field(name="🌾 Growing", value='\n'.join(growing_list) or "None", inline=False)
        embed.add_field(name="🥕 Grown", value='\n'.join(grown_list) or "None", inline=False)
        embed.add_field(name="💰 Sheckles", value=str(user_sheckles.get(interaction.user.id, 0)), inline=False)
        
        if new_achievements:
            embed.set_footer(text=f"🎉 New achievements: {', '.join(new_achievements)}")
        
        view = InventoryView(interaction.user.id)
        await interaction.edit_original_response(embed=embed, view=view)
    except Exception as e:
        print(f"Error in inventory command: {e}")
        try:
            await interaction.followup.send("❌ An error occurred while loading your inventory.", ephemeral=True)
        except:
            pass  # If we can't even send an error message

@tree.command(name="sheckles")
async def check_sheckles(interaction: discord.Interaction):
    sheckles = user_sheckles.get(interaction.user.id, 0)
    await interaction.followup.send(f"💰 You have {sheckles} sheckles.")

@tree.command(name="closest_quest")
@auto_defer(ephemeral=True)
async def closest_quest(interaction: discord.Interaction):
    count = user_message_counts[interaction.user.id]
    closest = None
    diff = float('inf')
    for name, (sheck, quest) in seeds.items():
        if quest > 0 and (quest - count) < diff and count < quest:
            closest = (name, quest - count)
            diff = quest - count
    msg = f"Closest quest seed: {closest[0]} ({closest[1]} messages left)" if closest else "You have completed all quests!"
    await interaction.followup.send(msg)

@tree.command(name="give_seed")
@auto_defer(ephemeral=True)
@app_commands.describe(user="User to give seed to", seed="Seed name")
async def give_seed(interaction: discord.Interaction, user: discord.Member, seed: str):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Not allowed", ephemeral=True)

    base, mut, seed = normalize_seed_name(seed)

    if base not in seeds and base not in limited_seeds:
        return await interaction.followup.send("❌ Invalid seed name.", ephemeral=True)

    # Calculate grow time with modifiers
    grow_time = calculate_grow_time(base, interaction.user.id)
    
    # Handle mutations and boosts
    allowed_mutations = None
    if base in limited_seeds:
        allowed_mutations = limited_seeds[base].get("mutations")

    # Check for active mutation boost
    if user_active_boosts.get(interaction.user.id, {}).get("mutation_boost"):
        if time.time() < user_active_boosts[interaction.user.id]["mutation_boost"]["expires"]:
            # This will be handled in the GrowingSeed class automatically
            pass
    
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

@tree.command(name="buy_seed")
@auto_defer(ephemeral=True)
@app_commands.describe(seed="Seed name to purchase")
async def buy_seed(interaction: discord.Interaction, seed: str):
    base, mut, seed = normalize_seed_name(seed)

    if base not in current_stock and base not in limited_seeds:
        return await interaction.followup.send("❌ This seed is not in stock right now!", ephemeral=True)

    if base in seeds:
        sheckles_required, _ = seeds[base]
        if sheckles_required <= 0:
            return await interaction.followup.send("❌ This seed is not for sale.", ephemeral=True)

        if user_sheckles.get(interaction.user.id, 0) < sheckles_required:
            return await interaction.followup.send("❌ Not enough sheckles!", ephemeral=True)

        grow_time = calculate_grow_time(base, interaction.user.id)
        
        allowed_mutations = None
        if base in limited_seeds:
            allowed_mutations = limited_seeds[base].get("mutations")

        if user_active_boosts.get(interaction.user.id, {}).get("mutation_boost"):
            if time.time() < user_active_boosts[interaction.user.id]["mutation_boost"]["expires"]:
                pass
        
        user_sheckles[interaction.user.id] -= sheckles_required
        seed_obj = GrowingSeed(
            base,
            grow_time,  # This is correctly passed as duration
            allowed_mutations=allowed_mutations
        )
        user_inventory[interaction.user.id]["growing"].append(seed_obj)

        new_achievements = check_achievements(interaction.user.id)
        achievement_msg = f"\n🎉 New achievement(s): {', '.join(new_achievements)}" if new_achievements else ""

        return await interaction.followup.send(
            f"✅ Purchased {pretty_seed(seed_obj)} seed for {sheckles_required} sheckles! "
            f"It will be ready in {int(grow_time)} seconds."
            f"{achievement_msg}"
        )

    elif base in limited_seeds:
        seed_data = limited_seeds[base]
        if time.time() > seed_data["expires"]:
            return await interaction.followup.send("❌ This limited seed is no longer available.", ephemeral=True)

        sheckles_required = seed_data["sheckles"]
        if user_sheckles.get(interaction.user.id, 0) < sheckles_required:
            return await interaction.followup.send("❌ Not enough sheckles!", ephemeral=True)

        # REMOVED THE QUEST CHECK HERE FOR CONSISTENCY
        # (though you might want to keep it for direct commands)

        grow_time = calculate_grow_time(base, interaction.user.id)
        allowed_mutations = seed_data.get("mutations")
        if user_active_boosts.get(interaction.user.id, {}).get("mutation_boost"):
            if time.time() < user_active_boosts[interaction.user.id]["mutation_boost"]["expires"]:
                pass

        user_sheckles[interaction.user.id] -= sheckles_required
        seed_obj = GrowingSeed(
            base,
            grow_time,
            limited=True,
            allowed_mutations=allowed_mutations
        )
        user_inventory[interaction.user.id]["growing"].append(seed_obj)

        new_achievements = check_achievements(interaction.user.id)
        achievement_msg = f"\n🎉 New achievement(s): {', '.join(new_achievements)}" if new_achievements else ""

        return await interaction.followup.send(
            f"✅ Purchased limited {pretty_seed(seed_obj)} seed for {sheckles_required} sheckles! "
            f"It will be ready in {int(grow_time)} seconds."
            f"{achievement_msg}"
        )

    else:
        await interaction.followup.send(
            "❌ Seed not found in shop. Use `/shoplist` to see available seeds.", ephemeral=True
        )

def pretty_seed(seed_obj):
    name = f"{seed_obj.name}"
    if seed_obj.mutation:
        name += f" ({seed_obj.mutation})"
    if getattr(seed_obj, "limited", False):
        name += " 🌟(Limited)"
    return name

def normalize_seed_name(raw: str):
    """
    Turns 'ember lily (inferno)' → 'Ember Lily (Inferno)'
    Returns (seed_name, mutation_or_None, combined_string)
    """
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

@tree.command(name="trade_offer")
@auto_defer(ephemeral=True)
@app_commands.describe(user="User to trade with", yourseed="Seed you're offering", theirseed="Seed you want")
async def trade_offer(interaction: discord.Interaction, user: discord.Member, yourseed: str, theirseed: str):
    update_growing_seeds(interaction.user.id)
    update_growing_seeds(user.id)

    sender_id = interaction.user.id
    recipient_id = user.id

    if recipient_id in trade_offers:
        return await interaction.followup.send("❌ That user already has a pending trade offer.", ephemeral=True)

    sender_seed_obj = find_matching_seed(user_inventory[sender_id]["grown"], yourseed)
    recipient_seed_obj = find_matching_seed(user_inventory[recipient_id]["grown"], theirseed)

    if not sender_seed_obj:
        return await interaction.followup.send("❌ You don't have that grown seed to offer.", ephemeral=True)
    if not recipient_seed_obj:
        return await interaction.followup.send(f"❌ {user.mention} doesn't have that seed or it's still growing.", ephemeral=True)

    # Store the trade offer
    trade_offers[recipient_id] = {
        "sender_id": sender_id,
        "sender_seed_name": sender_seed_obj.name,
        "sender_seed_mut": sender_seed_obj.mutation,
        "recipient_seed_name": recipient_seed_obj.name,
        "recipient_seed_mut": recipient_seed_obj.mutation,
        "timestamp": time.time()
    }

    # Create an embed for the trade
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
    
    await interaction.followup.send(
        f"{user.mention}, you received a trade offer from {interaction.user.mention}!",
        embed=embed,
        view=view
    )

    try:
        await user.send(
            f"You received a trade offer from {interaction.user.mention}!\n"
            f"They're offering: {pretty_seed(sender_seed_obj)}\n"
            f"They want: {pretty_seed(recipient_seed_obj)}\n"
            f"Check {interaction.channel.mention} to respond!"
        )
    except discord.Forbidden:
        pass  # User has DMs disabled



@tree.command(name="trade_offers")
@auto_defer(ephemeral=True)
async def view_trade_offers(interaction: discord.Interaction):
    offer = trade_offers.get(interaction.user.id)
    if not offer:
        return await interaction.followup.send("📭 You have no pending trade offers.", ephemeral=True)

    sender = await bot.fetch_user(offer["sender_id"])

    # Fetch the real objects from inventory so we can use pretty_seed()
    sender_grown = user_inventory[offer["sender_id"]]["grown"]
    recipient_grown = user_inventory[interaction.user.id]["grown"]
    
    sender_seed = next((s for s in sender_grown if s.name == offer["sender_seed_name"] and s.mutation == offer["sender_seed_mut"]), None)
    recipient_seed = next((s for s in recipient_grown if s.name == offer["recipient_seed_name"] and s.mutation == offer["recipient_seed_mut"]), None)
    
    from_seed = pretty_seed(sender_seed) if sender_seed else offer["sender_seed_name"]
    to_seed = pretty_seed(recipient_seed) if recipient_seed else offer["recipient_seed_name"]

    msg = (
        f"🔁 Pending Trade:\n"
        f"From: {sender.mention}\n"
        f"They offer: {from_seed}\n"
        f"They want: {to_seed}\n"
        f"Check the trade message to accept/decline!"
    )

    await interaction.followup.send(msg, ephemeral=True)

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

@tree.command(name="shop")
@auto_defer(ephemeral=True)
async def shoplist(interaction: discord.Interaction):
    """View the seed shop with interactive menu"""
    # Purge expired limited seeds
    global limited_seeds
    limited_seeds = {
        name: data for name, data in limited_seeds.items()
        if time.time() < data["expires"] and data["sheckles"] > 0
    }

    embed = discord.Embed(title="🛒 Seed Shop", color=discord.Color.purple())
    embed.add_field(
        name="🌦 Current Season",
        value=f"{current_season['name']} (Boosted: {', '.join(current_season['boosted_seeds'])})",
        inline=False
    )
    
    # Send the view with dropdown - pass limited_seeds directly (it's already a dict)
    view = SeedShopView(current_stock, limited_seeds, fertilizers)
    await interaction.edit_original_response(embed=embed, view=view)

@tree.command(name="sell_seed")
@auto_defer(ephemeral=True)
@app_commands.describe(seed="Seed name", seed_type="Seed state")
@app_commands.choices(seed_type=[
    app_commands.Choice(name="Growing", value="growing"),
    app_commands.Choice(name="Grown", value="grown")
])
async def sell_seed(interaction: discord.Interaction, seed: str, seed_type: app_commands.Choice[str]):
    update_growing_seeds(interaction.user.id)
    
    # Normalize input (handles "test (limited)" -> "Test (Limited)")
    base, mut, _ = normalize_seed_name(seed)
    inv = user_inventory[interaction.user.id]
    
    # Dynamic base prices (combine regular + limited seeds)
    base_prices = {
        "Carrot": 2,
        "Strawberry": 10,
        "Potato": 5,
        "Bamboo": 20,
        "Ember Lily": 55,
        "Sugar Apple": 80,
        "Beanstalk": 70,
    }
    
    # Add active limited seeds to pricing
    for seed_name, data in limited_seeds.items():
        if time.time() < data["expires"]:  # Only if not expired
            base_prices[seed_name] = data["sheckles"]  # Use limited seed's sheckle cost as base
    
    # Find seed in inventory
    seed_list = []
    if seed_type.value == "growing":
        seed_list = [s for s in inv["growing"] if s.name.lower() == base.lower()]
    else:
        seed_list = [s for s in inv["grown"] if s.name.lower() == base.lower()]
    
    if not seed_list or base not in base_prices:
        return await interaction.followup.send("❌ Invalid seed or type", ephemeral=True)
    
    # Calculate price with mutation
    base_price = base_prices[base]
    mult = 1.0
    if seed_list[0].mutation:
        mut = seed_list[0].mutation
        if base in mutations["specific"] and mut in mutations["specific"][base]:
            mult = mutations["specific"][base][mut]["multiplier"]
        elif mut in mutations["global"]:
            mult = mutations["global"][mut]["multiplier"]
    
    price = int(base_price * mult)
    if seed_type.value == "growing":
        price = price // 2  # Half price for growing
    
    # Complete sale
    if seed_type.value == "growing":
        inv["growing"].remove(seed_list[0])
    else:
        inv["grown"].remove(seed_list[0])
    
    user_sheckles[interaction.user.id] += price
    
    msg = f"✅ Sold {seed_list[0].name}"
    if seed_list[0].mutation:
        msg += f" ({seed_list[0].mutation})"
    if getattr(seed_list[0], "limited", False):
        msg += " 🌟(Limited)"
    msg += f" for {price} sheckles!"
    
    await interaction.followup.send(msg, ephemeral=True)


def update_growing_seeds(user_id):
    """Move finished growing seeds to grown inventory"""
    current_time = time.time()
    print(f"Current time: {current_time}")  # Debug print
    
    growing = user_inventory[user_id]["growing"]
    grown = user_inventory[user_id]["grown"]
    
    # Debug print all growing seeds
    print("Growing seeds before update:")
    for seed in growing:
        print(f"- {seed.name} finishes at {seed.finish_time} ({seed.finish_time - current_time}s remaining)")
    
    # Find all seeds that are done growing
    finished_seeds = [seed for seed in growing if seed.finish_time <= current_time]
    
    if finished_seeds:
        print(f"Moving {len(finished_seeds)} seeds to grown")
    
    # Move them to grown
    for seed in finished_seeds:
        grown.append(seed)
    
    # Remove them from growing
    user_inventory[user_id]["growing"] = [seed for seed in growing if seed.finish_time > current_time]

@tasks.loop(minutes=5)
async def refresh_stock():
    global current_stock, limited_seeds  # Add current_stock to globals
    
    # Clean expired limited seeds
    limited_seeds = {
        name: data for name, data in limited_seeds.items()
        if time.time() < data["expires"]
    }
    
    # Now modify the GLOBAL current_stock
    current_stock = []  # ✅ This modifies the global variable
    
    # Rest of your stock refresh logic...
    for seed, rarity in SEED_RARITIES.items():
        if random.random() < RARITY_CHANCES[rarity]:
            current_stock.append(seed)

    # Ensure at least 2 common seeds are always present
    commons = [s for s, r in SEED_RARITIES.items() if r == "Common"]
    commons_in_stock = [s for s in current_stock if s in commons]
    needed = max(2 - len(commons_in_stock), 0)

    if needed > 0:
        available_commons = list(set(commons) - set(commons_in_stock))
        random.shuffle(available_commons)
        current_stock += available_commons[:needed]

    # Optional: shuffle stock list
    random.shuffle(current_stock)

@tree.command(name="refresh_stock")
@auto_defer(ephemeral=True)
async def manual_refresh(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        return await interaction.followup.send("❌ Not allowed", ephemeral=True)
    refresh_stock.restart()
    await interaction.followup.send("🔁 Stock refreshed!", ephemeral=True)

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

    # Preserve the limited status when moving to grown
    grown_seed = GrowingSeed(
        match.name,
        0,  # Instant grow
        mutation=match.mutation,
        limited=getattr(match, "limited", False)  # <-- Preserve limited status
    )
    
    growing.remove(match)
    user_inventory[user.id]["grown"].append(grown_seed)

    await interaction.followup.send(f"🌱 Instantly grew {pretty_seed(grown_seed)} for {user.mention}.", ephemeral=True)

@tasks.loop(hours=24)
async def rotate_seasons():
    global current_season
    current_idx = next((i for i, s in enumerate(SEASONS) if s["name"] == current_season["name"]), 0)
    next_idx = (current_idx + 1) % len(SEASONS)
    current_season = SEASONS[next_idx]
    
    # Announce season change
    channel = bot.get_channel(YOUR_ANNOUNCEMENT_CHANNEL_ID)
    if channel:
        boosted = ", ".join(current_season["boosted_seeds"])
        await channel.send(f"🌱 The season has changed to **{current_season['name']}**! Boosted seeds: {boosted}")

@tasks.loop(minutes=30)
async def check_plant_events():
    global current_plant_event
    
    # Clear expired events
    if current_plant_event and time.time() > current_plant_event["end_time"]:
        current_plant_event = None
    
    # Random chance to start new event (5% chance per check)
    if not current_plant_event and random.random() < 0.05:
        event = random.choice(PLANT_EVENTS)
        event["start_time"] = time.time()
        event["end_time"] = time.time() + event["duration"]
        current_plant_event = event
        
        # Announce event
        channel = bot.get_channel(YOUR_ANNOUNCEMENT_CHANNEL_ID)
        if channel:
            desc = (f"All plants grow {event['multiplier']*100}% faster!" 
                   if event["effect"] == "speed" 
                   else f"All plants take {event['delay']//60} extra minutes to grow!")
            await channel.send(
                f"🌿 **PLANT EVENT: {event['name']}** 🌿\n"
                f"{desc}\n"
                f"Duration: {event['duration']//3600} hours"
            )

@tree.command(name="buy_fertilizer")
@auto_defer(ephemeral=True)
@app_commands.describe(fertilizer="Fertilizer name")
async def buy_fertilizer(interaction: discord.Interaction, fertilizer: str):
    fert = fertilizers.get(fertilizer.title())
    if not fert:
        return await interaction.followup.send("❌ Invalid fertilizer", ephemeral=True)
    
    if user_sheckles.get(interaction.user.id, 0) < fert["cost"]:
        return await interaction.followup.send("❌ Not enough sheckles", ephemeral=True)
    
    user_sheckles[interaction.user.id] -= fert["cost"]
    user_fertilizers[interaction.user.id][fertilizer.title()] += 1
    
    await interaction.followup.send(
        f"✅ Purchased {fertilizer.title()} for {fert['cost']} sheckles! "
        f"Use `/use_fertilizer {fertilizer.title()}` to activate it."
    )


@tree.command(name="use_fertilizer")
@auto_defer(ephemeral=True)
@app_commands.describe(fertilizer="Fertilizer name")
async def use_fertilizer(interaction: discord.Interaction, fertilizer: str):
    fert_name = fertilizer.title()
    if user_fertilizers[interaction.user.id].get(fert_name, 0) < 1:
        return await interaction.followup.send("❌ You don't have this fertilizer", ephemeral=True)
    
    fert = fertilizers[fert_name]
    user_fertilizers[interaction.user.id][fert_name] -= 1
    user_active_boosts[interaction.user.id][fert["effect"]["type"] + "_boost"] = {
        "expires": time.time() + fert["effect"]["duration"],
        "multiplier": fert["effect"]["multiplier"]
    }
    
    await interaction.followup.send(
        f"🌱 Activated {fert_name}! Effect will last for {fert['effect']['duration']//3600} hours."
    )

@tree.command(name="shovel")
@auto_defer(ephemeral=True)
@app_commands.describe(
    plant="Plant to remove (name or 'all') - add 'x1', 'x2' etc. to remove specific amounts",
    plant_type="Type of plant to remove",
    force="Skip confirmation (admin only)"
)
@app_commands.choices(plant_type=[
    app_commands.Choice(name="Growing", value="growing"),
    app_commands.Choice(name="Grown", value="grown"),
    app_commands.Choice(name="Both", value="both")
])
async def shovel(
    interaction: discord.Interaction,
    plant: Optional[str] = None,
    plant_type: Optional[app_commands.Choice[str]] = None,
    force: bool = False
):
    """Remove plants from your garden with confirmation"""
    update_growing_seeds(interaction.user.id)
    
    if plant is None:
        return await inventory(interaction)
    
    # Parse quantity if specified (e.g. "Carrot x2")
    quantity = 0  # 0 means all
    base_plant = plant
    if "x" in plant.lower():
        parts = plant.lower().split("x")
        if len(parts) == 2 and parts[1].isdigit():
            quantity = int(parts[1])
            base_plant = parts[0].strip()
    
    base_name, mut, _ = normalize_seed_name(base_plant)
    inv = user_inventory[interaction.user.id]
    
    # Find matching plants
    matches = []
    check_growing = plant_type is None or plant_type.value in ["growing", "both"]
    check_grown = plant_type is None or plant_type.value in ["grown", "both"]
    
    if check_growing:
        matches.extend([
            p for p in inv["growing"]
            if (base_name.lower() == "all" or p.name.lower() == base_name.lower()) and
               (mut is None or (p.mutation and p.mutation.lower() == mut.lower()))
        ])
    
    if check_grown:
        matches.extend([
            p for p in inv["grown"]
            if (base_name.lower() == "all" or p.name.lower() == base_name.lower()) and
               (mut is None or (p.mutation and p.mutation.lower() == mut.lower()))
        ])
    
    if not matches:
        return await interaction.followup.send(
            f"❌ No matching plants found in your {'garden' if plant_type is None else plant_type.name.lower()}.",
            ephemeral=True
        )
    
    # Handle quantity
    if quantity > 0:
        if quantity > len(matches):
            return await interaction.followup.send(
                f"❌ You only have {len(matches)} of those plants!",
                ephemeral=True
            )
        matches = matches[:quantity]  # Only remove the specified amount
    
    # Check for special plants needing confirmation
    needs_confirmation = not (force and has_admin_role(interaction.user))

    
    if needs_confirmation:
        limited_count = sum(1 for p in matches if getattr(p, "limited", False))
        mutated_count = sum(1 for p in matches if p.mutation)
        
        embed = discord.Embed(
            title="⚠️ Confirm Plant Removal",
            color=discord.Color.orange()
        )
        embed.description = (
            f"You're about to remove {len(matches)} plants:\n"
            f"• {limited_count} limited edition 🌟\n"
            f"• {mutated_count} mutated 🔄\n\n"
            "**These cannot be recovered!**"
        )
        
        view = ShovelConfirmView(base_name, plant_type.value if plant_type else "both", limited_count > 0, mutated_count > 0)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        await view.wait()
        
        if not view.confirmed:
            return  # Already handled in the view
    else:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        
    # Perform removal
    removed = []
    if check_growing:
        to_remove = matches[:]  # Copy the matches we want to remove
        inv["growing"] = [
            p for p in inv["growing"]
            if p not in to_remove
        ]
        removed.extend(to_remove)
    
    if check_grown:
        to_remove = matches[:]  # Copy the matches we want to remove
        inv["grown"] = [
            p for p in inv["grown"]
            if p not in to_remove
        ]
        removed.extend(to_remove)
    
    # Build result embed
    embed = discord.Embed(
        title="🪴 Shovel Results",
        color=discord.Color.green()
    )
    
    removed_counts = {
        "normal": 0,
        "mutated": 0,
        "limited": 0
    }
    
    for p in removed:
        if getattr(p, "limited", False):
            removed_counts["limited"] += 1
        elif p.mutation:
            removed_counts["mutated"] += 1
        else:
            removed_counts["normal"] += 1
    
    embed.add_field(
        name="Removed Plants",
        value=(
            f"• {removed_counts['normal']} normal\n"
            f"• {removed_counts['mutated']} mutated\n"
            f"• {removed_counts['limited']} limited 🌟"
        ),
        inline=False
    )
    
    if removed_counts["limited"] > 0 or removed_counts["mutated"] > 0:
        embed.set_footer(text="⚠️ Limited and mutated plants cannot be recovered!")
    
    try:
        # Use followup only if we deferred earlier
        if needs_confirmation:
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)
    except discord.errors.NotFound:
        # Fallback if the interaction expired
        try:
            await interaction.user.send(embed=embed)
        except:
            pass  # If we can't DM, just silently fail

@tree.command(name="apply_middleman", description="Apply for a middleman trade")
async def apply_middleman(interaction: discord.Interaction):
    blacklist_role = interaction.guild.get_role(1344056030153146448)
    if blacklist_role and blacklist_role in interaction.user.roles:
        return await interaction.response.send_message(
            "🚫 You are blacklisted from using the middleman system.",
            ephemeral=True
        )

    await interaction.response.send_modal(MiddlemanModal(bot, interaction))

@tasks.loop(minutes=5)
async def cleanup_expired():
    """Clean up expired trades and boosts"""
    current_time = time.time()
    
    # Clean expired trades
    for user_id, offer in list(trade_offers.items()):
        if current_time - offer["timestamp"] > 300:  # 5 minutes
            trade_offers.pop(user_id)
            try:
                sender = await bot.fetch_user(offer["sender_id"])
                recipient = await bot.fetch_user(user_id)
                await sender.send(f"❌ Your trade offer to {recipient.name} has expired.")
            except:
                pass

    
    # Clean expired boosts
    for user_id, boosts in list(user_active_boosts.items()):
        for boost_type, boost_data in list(boosts.items()):
            if boost_data["expires"] < current_time:
                del user_active_boosts[user_id][boost_type]

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

    # Validate winners count
    if winners < 1:
        await interaction.response.send_message("❌ Number of winners must be at least 1.", ephemeral=True)
        return

    # Parse range like "1-100"
    try:
        start, end = map(int, number_range.split("-"))
        if start >= end:
            raise ValueError
    except:
        await interaction.response.send_message("❌ Invalid number range. Use a format like `1-100`.", ephemeral=True)
        return

    # Parse duration like "30s", "2m", "1h"
    time_units = {"s": 1, "m": 60, "h": 3600}
    try:
        time_unit = duration[-1]
        time_value = int(duration[:-1])
        duration_seconds = time_value * time_units[time_unit]
        duration_minutes = duration_seconds // 60
    except:
        await interaction.response.send_message("❌ Invalid duration. Use `30s`, `1m`, `2h` etc.", ephemeral=True)
        return

    # Generate random target number
    target_number = random.randint(start, end)

    # Create the giveaway object
    giveaway = Giveaway(
        hoster=author,
        prize=prize,
        winners=winners,
        number_range=(start, end),
        target=target_number,  # The secret number to guess
        duration=duration_minutes,
        channel=interaction.channel
    )
    
    # Store giveaway
    active_giveaways[interaction.channel.id] = giveaway

    # Open the channel + set slowmode
    await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=True)
    await interaction.channel.edit(slowmode_delay=2)

    # Build embed
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

    # Send the message with the button
    view = GiveawayView(giveaway)
    await interaction.response.send_message(embed=embed, view=view)

    # Schedule the giveaway end
    if duration_minutes > 0:
        giveaway.task = asyncio.create_task(schedule_giveaway_end(giveaway))

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Message counting for sheckles
    user_message_counts[message.author.id] += 1
    if user_message_counts[message.author.id] % MESSAGES_PER_SHECKLE == 0:
        user_sheckles[message.author.id] += 1

    # Handle giveaway messages
    current_giveaway = active_giveaways.get(message.channel.id)
    if current_giveaway:
        # Use object attributes instead of dictionary keys
        if message.author.id == current_giveaway.hoster.id:
            if message.content.strip().isdigit():
                try:
                    await message.delete()
                    await message.author.send("❌ Hosts can't submit number guesses!", delete_after=10)
                except:
                    pass
            return  # Always allow non-number host messages

        # For participants - only process number guesses
        if message.content.strip().isdigit():
            try:
                guess = int(message.content.strip())
                start, end = map(int, current_giveaway['range'].split('-'))
                
                if guess < start or guess > end:
                    try:
                        await message.reply(f"❌ Guess must be between {start}-{end}!", delete_after=5)
                    except:
                        pass
                    return
                
                # Check if guess is correct
                if guess == current_giveaway['number']:
                    current_giveaway.setdefault('winners', set()).add(message.author)
                    
                    # DM the winner
                    try:
                        await message.author.send(
                            f"🎉 You guessed the correct number `{guess}`!\n"
                            f"Please contact <@{current_giveaway['host_id']}> to claim your prize."
                        )
                    except:
                        await message.channel.send(
                            f"🎉 <@{message.author.id}> guessed correctly but can't receive DMs. Please contact the host!"
                        )
                    
                    # End the giveaway
                    await message.channel.set_permissions(message.guild.default_role, send_messages=False)
                    await message.channel.edit(slowmode_delay=0)
                    await message.channel.send(
                        f"🏆 Giveaway ended! {message.author.mention} guessed the correct number `{guess}`!"
                    )
                    del active_giveaways[message.channel.id]
                
                else:
                    # Optional: Add reaction to show guess was received
                    try:
                        await message.add_reaction("🔢")
                    except:
                        pass
                    
            except ValueError:
                pass
        else:
            # Delete non-number messages from participants
            try:
                await message.delete()
            except:
                pass
            return

    await bot.process_commands(message)

# Run your bot (replace TOKEN with your bot's token)
bot.run(os.getenv("BOT_TOKEN"))
