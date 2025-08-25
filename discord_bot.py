# discord_bot.py
import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
import aiohttp
from queue import Queue

load_dotenv()

intents = discord.Intents.default()
intents.members = True  # ロール付与で必要
bot = commands.Bot(command_prefix="!", intents=intents)

LOG_CHANNEL_ID = int(os.getenv("DISCORD_LOG_CHANNEL_ID", "0"))
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))
ROLE_ID = int(os.getenv("DISCORD_ROLE_ID", "0"))

# Flask からのタスク受け取り用（スレッドセーフ）
task_queue: "Queue[dict]" = Queue()
user_tokens = {}  # 必要なら使う

async def _consume_queue_forever():
    """thread-safe Queue を非同期で待ち受ける常駐タスク"""
    loop = asyncio.get_running_loop()
    while True:
        # ブロッキングgetをスレッドプールで回す → awaitで安全に受け取れる
        item = await loop.run_in_executor(None, task_queue.get)
        try:
            action = item.get("action")
            if action == "send_log":
                await send_log(embed=item.get("embed"))
            elif action == "assign_role":
                await assign_role(item.get("user_id"))
        except Exception as e:
            print("⚠️ タスク処理失敗:", e)
        finally:
            # Queueのタスク完了
            try:
                task_queue.task_done()
            except Exception:
                pass

@bot.event
async def on_ready():
    print(f"✅ Bot logged in as {bot.user}")
    # 常駐コンシューマを起動
    asyncio.create_task(_consume_queue_forever())
    # スラコマ同期（任意）
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"⚠️ Sync error: {e}")

async def send_log(embed=None):
    if not LOG_CHANNEL_ID:
        print("⚠️ LOG_CHANNEL_ID が未設定")
        return
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
        thumb = embed.get("thumbnail")
        if thumb and thumb.get("url"):
            embed_obj.set_thumbnail(url=thumb["url"])
        await channel.send(embed=embed_obj)

async def assign_role(user_id):
    if not (GUILD_ID and ROLE_ID and user_id):
        return
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

# ===== Flask 側から呼ぶエクスポートAPI（非同期ループに触らない） =====
def enqueue_task(embed_data=None, user_id=None):
    if embed_data:
        task_queue.put({"action": "send_log", "embed": embed_data})
    if user_id:
        task_queue.put({"action": "assign_role", "user_id": user_id})

# 任意のスラコマ（残したい場合）
@bot.tree.command(name="adduser", description="ユーザーをサーバーに追加します")
@discord.app_commands.describe(user_id="追加したいユーザーID", guild_id="サーバーID")
async def adduser(interaction: discord.Interaction, user_id: str, guild_id: str):
    token = user_tokens.get(user_id)
    if not token:
        await interaction.response.send_message(f"ユーザー {user_id} のアクセストークンが見つかりません。", ephemeral=True)
        return

    url = f"https://discord.com/api/guilds/{guild_id}/members/{user_id}"
    headers = {
        "Authorization": f"Bot {os.getenv('DISCORD_BOT_TOKEN')}",
        "Content-Type": "application/json"
    }
    json_data = {"access_token": token}

    async with aiohttp.ClientSession() as session:
        async with session.put(url, headers=headers, json=json_data) as resp:
            if resp.status in [201, 204]:
                await interaction.response.send_message(f"✅ ユーザー {user_id} をサーバー {guild_id} に追加しました！")
            else:
                text = await resp.text()
                await interaction.response.send_message(f"⚠️ 追加失敗: {resp.status} {text}", ephemeral=True)

# Flask から import される属性
bot.user_tokens = user_tokens
bot.enqueue_task = enqueue_task
