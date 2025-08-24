import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import aiohttp

load_dotenv()

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

LOG_CHANNEL_ID = int(os.getenv("DISCORD_LOG_CHANNEL_ID", 0))
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", 0))
ROLE_ID = int(os.getenv("DISCORD_ROLE_ID", 0))

bot.task_queue = asyncio.Queue()
bot.user_tokens = {}
bot.bot_ready_event = asyncio.Event()
bot.bot_event_loop = None


@bot.event
async def on_ready():
    print(f"✅ Bot logged in as {bot.user}")
    bot.bot_event_loop = asyncio.get_running_loop()
    bot.bot_ready_event.set()
    asyncio.create_task(task_consumer())
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"⚠️ Sync error: {e}")


async def task_consumer():
    while True:
        item = await bot.task_queue.get()
        try:
            if item.get("action") == "send_log":
                await send_log(embed=item.get("embed"))
            elif item.get("action") == "assign_role":
                await assign_role(item.get("user_id"))
        except Exception as e:
            print("⚠️ タスク処理失敗:", e)


async def send_log(embed=None):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        print("⚠️ ログチャンネルが見つかりません")
        return

    if embed:
        embed_obj = discord.Embed(
            title=embed.get("title", "ログ"),
            description=embed.get("description", ""),
            color=0x00ff00
        )
        if "thumbnail" in embed and embed["thumbnail"]:
            embed_obj.set_thumbnail(url=embed["thumbnail"]["url"])
        await channel.send(embed=embed_obj)


async def assign_role(user_id):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print("⚠️ Guild not found.")
        return

    member = guild.get_member(int(user_id))
    if not member:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception as e:
            print("⚠️ メンバー取得失敗:", e)
            return

    role = guild.get_role(ROLE_ID)
    if role and member:
        try:
            await member.add_roles(role, reason="認証通過により自動付与")
            print(f"✅ {member} にロールを付与しました。")
        except Exception as e:
            print("⚠️ ロール付与失敗:", e)


def enqueue_task(embed_data=None, user_id=None):
    async def wait_and_enqueue():
        await bot.bot_ready_event.wait()
        if embed_data:
            await bot.task_queue.put({"action": "send_log", "embed": embed_data})
        if user_id:
            await bot.task_queue.put({"action": "assign_role", "user_id": user_id})

    if bot.bot_event_loop:
        asyncio.run_coroutine_threadsafe(wait_and_enqueue(), bot.bot_event_loop)
    else:
        print("⚠️ Bot がまだ準備できていません")
