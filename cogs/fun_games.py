"""cogs/fun_games.py — poll, 8ball, coinflip, trivia, would-you-rather, truth/dare, rock-paper-scissors."""
import html
import random

import aiohttp
import discord
from discord import app_commands

from bot_instance import bot
from core.features import require_feature

FEATURE = "fun_games"

EIGHTBALL_RESPONSES = [
    "It is certain! 🟢",
    "Without a doubt! 🟢",
    "Yes, definitely! 🟢",
    "You may rely on it! 🟢",
    "As I see it, yes! 🟢",
    "Most likely! 🟢",
    "Outlook good! 🟢",
    "Signs point to yes! 🟢",
    "Reply hazy, try again 🟡",
    "Ask again later 🟡",
    "Better not tell you now 🟡",
    "Cannot predict now 🟡",
    "Concentrate and ask again 🟡",
    "Don't count on it 🔴",
    "My reply is no 🔴",
    "My sources say no 🔴",
    "Outlook not so good 🔴",
    "Very doubtful 🔴",
]

WYR_QUESTIONS = [
    ("be able to fly", "be able to breathe underwater"),
    ("always speak your mind", "never speak again"),
    ("live without music", "live without TV/movies"),
    ("be the funniest person in the room", "be the smartest person in the room"),
    ("have unlimited money but no friends", "have amazing friends but always be broke"),
    ("know when you'll die", "know how you'll die"),
    ("be famous but hated", "be unknown but loved"),
    ("only eat dal bhat every day", "never eat dal bhat again"),
    ("lose all your memories", "never make new ones"),
    ("be able to talk to animals", "speak all human languages"),
    ("always be 10 minutes late", "always be 2 hours early"),
    ("have free WiFi everywhere", "have free food everywhere"),
    ("never use social media again", "never watch Netflix again"),
    ("fight 100 duck-sized horses", "fight 1 horse-sized duck"),
    ("have 3 arms", "have 3 legs"),
    ("wake up every day in a new country", "never leave your home country"),
    ("be the best player on a losing team", "be the worst player on a winning team"),
    ("give up chai/coffee forever", "give up your favourite food forever"),
    ("have to sing everything you say", "have to dance everywhere you go"),
    ("have no internet for a month", "have no friends for a month"),
]

TRUTHS = [
    "What's the most embarrassing thing you've done in public?",
    "What's a secret you've never told anyone in this server?",
    "Who in this server do you have a crush on?",
    "What's the biggest lie you've ever told?",
    "What's the most childish thing you still do?",
    "What's your most embarrassing childhood memory?",
    "Have you ever cheated on a test?",
    "What's the worst gift you've ever received?",
    "What's something you pretend to like but actually hate?",
    "What's the pettiest thing you've ever done?",
    "Have you ever blamed someone else for something you did?",
    "What's your biggest irrational fear?",
    "What's the most awkward date you've been on?",
    "What's a bad habit you have that no one knows about?",
    "What's the most embarrassing text you've sent to the wrong person?",
]

DARES = [
    "Type 'I love KP Oli' in the server chat.",
    "Change your nickname to 'Sala Boka' for 10 minutes.",
    "Send a voice message singing the first 10 seconds of a Nepali song.",
    "DM a random server member a compliment right now.",
    "Type everything in CAPS for the next 5 minutes.",
    "Send your most recent photo from your camera roll (no deleting!).",
    "Write a poem about dal bhat in 2 minutes.",
    "Let the person to your right pick your profile picture for 1 hour.",
    "Send a GIF that describes your mood right now.",
    "Use only emojis for your next 5 messages.",
    "Tag two people and say something genuinely nice about each.",
    "React to the last 10 messages in this channel.",
    "Send a voice note saying 'I am the best person in this server'.",
    "Speak only in questions for the next 3 minutes.",
    "Write a haiku about the last person who messaged in this channel.",
]

RPS_CHOICES = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
RPS_WINS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


# ==================== POLL ====================

@bot.tree.command(name="poll", description="Create a poll with up to 4 options")
@app_commands.describe(
    question="The poll question",
    option1="First option",
    option2="Second option",
    option3="Third option (optional)",
    option4="Fourth option (optional)"
)
@require_feature(FEATURE)
async def poll_command(
    interaction: discord.Interaction,
    question: str,
    option1: str,
    option2: str,
    option3: str = None,
    option4: str = None
):
    options = [opt for opt in [option1, option2, option3, option4] if opt]
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]

    description = ""
    for i, opt in enumerate(options):
        description += f"{emojis[i]} {opt}\n\n"

    embed = discord.Embed(
        title=f"📊 {question}",
        description=description,
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Poll by {interaction.user.display_name}")
    embed.timestamp = discord.utils.utcnow()

    await interaction.response.send_message(embed=embed)
    poll_message = await interaction.original_response()
    for i in range(len(options)):
        await poll_message.add_reaction(emojis[i])


# ==================== 8-BALL ====================

@bot.tree.command(name="8ball", description="Ask the magic 8-ball a yes/no question")
@app_commands.describe(question="Your yes/no question")
@require_feature(FEATURE)
async def eightball_command(interaction: discord.Interaction, question: str):
    answer = random.choice(EIGHTBALL_RESPONSES)
    embed = discord.Embed(color=discord.Color.dark_purple())
    embed.add_field(name="🎱 Question", value=question, inline=False)
    embed.add_field(name="🔮 Answer", value=f"**{answer}**", inline=False)
    embed.set_footer(text=f"Asked by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@bot.command(name="8ball")
@require_feature(FEATURE)
async def eightball_prefix(ctx, *, question: str = None):
    """Ask the magic 8-ball. Usage: .8ball <question>"""
    if not question:
        await ctx.send("❌ Please ask a question! Usage: `.8ball will I pass my exams?`")
        return
    answer = random.choice(EIGHTBALL_RESPONSES)
    embed = discord.Embed(color=discord.Color.dark_purple())
    embed.add_field(name="🎱 Question", value=question, inline=False)
    embed.add_field(name="🔮 Answer", value=f"**{answer}**", inline=False)
    embed.set_footer(text=f"Asked by {ctx.author.display_name}")
    await ctx.send(embed=embed)


# ==================== COIN FLIP ====================

@bot.tree.command(name="coinflip", description="Flip a coin!")
@require_feature(FEATURE)
async def coinflip_command(interaction: discord.Interaction):
    result = random.choice(["Heads", "Tails"])
    emoji = "🪙" if result == "Heads" else "🟤"
    embed = discord.Embed(
        title="🪙 Coin Flip",
        description=f"## {emoji} {result}!",
        color=discord.Color.gold() if result == "Heads" else discord.Color.dark_grey()
    )
    embed.set_footer(text=f"Flipped by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@bot.command(name="coinflip")
@require_feature(FEATURE)
async def coinflip_prefix(ctx):
    """Flip a coin!"""
    result = random.choice(["Heads", "Tails"])
    emoji = "🪙" if result == "Heads" else "🟤"
    embed = discord.Embed(
        title="🪙 Coin Flip",
        description=f"## {emoji} {result}!",
        color=discord.Color.gold() if result == "Heads" else discord.Color.dark_grey()
    )
    embed.set_footer(text=f"Flipped by {ctx.author.display_name}")
    await ctx.send(embed=embed)


# ==================== TRIVIA ====================

async def fetch_trivia_question() -> dict | None:
    url = "https://opentdb.com/api.php?amount=1&type=multiple"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                if data.get("response_code") == 0 and data.get("results"):
                    return data["results"][0]
    except Exception as e:
        print(f"Trivia fetch error: {e}")
    return None


class TriviaView(discord.ui.View):
    def __init__(self, correct: str, options: list[str], question: str):
        super().__init__(timeout=30)
        self.correct = correct
        self.answered = set()

        emojis = ["🇦", "🇧", "🇨", "🇩"]
        for i, option in enumerate(options):
            btn = discord.ui.Button(
                label=f"{emojis[i]} {option[:60]}",
                custom_id=f"trivia_{i}",
                style=discord.ButtonStyle.secondary
            )
            btn.callback = self.make_callback(option)
            self.add_item(btn)

    def make_callback(self, option: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id in self.answered:
                await interaction.response.send_message("You already answered!", ephemeral=True)
                return
            self.answered.add(interaction.user.id)
            if option == self.correct:
                await interaction.response.send_message(f"✅ Correct! The answer was **{self.correct}**", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Wrong! The correct answer was **{self.correct}**", ephemeral=True)
        return callback


@bot.tree.command(name="trivia", description="Get a random trivia question")
@require_feature(FEATURE)
async def trivia_command(interaction: discord.Interaction):
    await interaction.response.defer()
    question_data = await fetch_trivia_question()
    if not question_data:
        await interaction.followup.send("❌ Could not fetch a trivia question. Try again in a moment.")
        return

    question = html.unescape(question_data["question"])
    correct = html.unescape(question_data["correct_answer"])
    incorrects = [html.unescape(a) for a in question_data["incorrect_answers"]]
    options = incorrects + [correct]
    random.shuffle(options)

    category = question_data.get("category", "General")
    difficulty = question_data.get("difficulty", "medium").title()
    diff_colors = {"Easy": discord.Color.green(), "Medium": discord.Color.orange(), "Hard": discord.Color.red()}

    embed = discord.Embed(
        title="🧠 Trivia Time!",
        description=f"**{question}**",
        color=diff_colors.get(difficulty, discord.Color.blurple())
    )
    embed.add_field(name="Category", value=category, inline=True)
    embed.add_field(name="Difficulty", value=difficulty, inline=True)
    embed.set_footer(text="You have 30 seconds to answer!")

    view = TriviaView(correct, options, question)
    await interaction.followup.send(embed=embed, view=view)


# ==================== WOULD YOU RATHER ====================

@bot.tree.command(name="wyr", description="Get a Would You Rather question")
@require_feature(FEATURE)
async def wyr_command(interaction: discord.Interaction):
    option_a, option_b = random.choice(WYR_QUESTIONS)
    embed = discord.Embed(
        title="🤔 Would You Rather...",
        color=discord.Color.purple()
    )
    embed.add_field(name="🅰️ Option A", value=option_a.capitalize(), inline=False)
    embed.add_field(name="🅱️ Option B", value=option_b.capitalize(), inline=False)
    embed.set_footer(text="React with 🅰️ or 🅱️ to vote!")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("🅰️")
    await msg.add_reaction("🅱️")


# ==================== TRUTH OR DARE ====================

@bot.tree.command(name="truth", description="Get a random truth question")
@require_feature(FEATURE)
async def truth_command(interaction: discord.Interaction):
    question = random.choice(TRUTHS)
    embed = discord.Embed(
        title="🫣 Truth!",
        description=f"**{question}**",
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Dare for {interaction.user.display_name} — no lying!")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="dare", description="Get a random dare")
@require_feature(FEATURE)
async def dare_command(interaction: discord.Interaction):
    dare = random.choice(DARES)
    embed = discord.Embed(
        title="😈 Dare!",
        description=f"**{dare}**",
        color=discord.Color.red()
    )
    embed.set_footer(text=f"Dare for {interaction.user.display_name} — no chickening out!")
    await interaction.response.send_message(embed=embed)


# ==================== ROCK PAPER SCISSORS ====================

class RPSView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def play(self, interaction: discord.Interaction, player_choice: str):
        bot_choice = random.choice(list(RPS_CHOICES.keys()))
        player_emoji = RPS_CHOICES[player_choice]
        bot_emoji = RPS_CHOICES[bot_choice]

        if player_choice == bot_choice:
            result = "🤝 It's a tie!"
            color = discord.Color.yellow()
        elif RPS_WINS[player_choice] == bot_choice:
            result = "🎉 You win!"
            color = discord.Color.green()
        else:
            result = "😂 Bot wins!"
            color = discord.Color.red()

        embed = discord.Embed(title="🪨📄✂️ Rock Paper Scissors", color=color)
        embed.add_field(name=f"You ({interaction.user.display_name})", value=f"{player_emoji} {player_choice.title()}", inline=True)
        embed.add_field(name="KP Bot", value=f"{bot_emoji} {bot_choice.title()}", inline=True)
        embed.add_field(name="Result", value=f"**{result}**", inline=False)

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🪨 Rock", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "rock")

    @discord.ui.button(label="📄 Paper", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "paper")

    @discord.ui.button(label="✂️ Scissors", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "scissors")


@bot.tree.command(name="rps", description="Play Rock Paper Scissors against the bot")
@require_feature(FEATURE)
async def rps_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🪨📄✂️ Rock Paper Scissors",
        description="Choose your weapon!",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=RPSView())
