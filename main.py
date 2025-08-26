# app.py
from flask import Flask, request, render_template
import requests, json, os, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot

load_dotenv()
app = Flask(__name__)

ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")


# --- IP取得 ---
def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr


# --- IPから地域情報 ---
def get_geo_info(ip):
    try:
        response = requests.get(
            f"http://ip-api.com/json/{ip}?lang=ja&fields=status,message,country,regionName,lat,lon,proxy,hosting,query"
        )
        data = response.json()
        return {
            "country": data.get("country", "不明"),
            "region": data.get("regionName", "不明"),
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "proxy": data.get("proxy", False),
            "hosting": data.get("hosting", False),
            "ip": data.get("query"),
        }
    except:
        return {
            "country": "不明",
            "region": "不明",
            "lat": None,
            "lon": None,
            "proxy": False,
            "hosting": False,
            "ip": ip,
        }


# --- ログ保存 ---
def save_log(discord_id, data):
    logs = {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)

    if discord_id not in logs:
        logs[discord_id] = {"history": []}

    data["timestamp"] = now
    logs[discord_id]["history"].append(data)

    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)


# --- 認証ページ ---
@app.route("/")
def index():
    url = (
        f"https://discord.com/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify%20email%20guilds%20connections"
    )
    return render_template("index.html", discord_auth_url=url)


# --- コールバック処理 ---
@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "コードがありません", 400

    token = requests.post(
        "https://discord.com/api/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "scope": "identify email guilds connections",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ).json()

    access_token = token.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    # ユーザー情報
    user = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()

    # サーバー参加処理
    requests.put(
        f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/members/{user['id']}",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"access_token": access_token},
    )

    # --- IP & GEO ---
    ip = get_client_ip()
    if ip.startswith(("127.", "192.", "10.", "172.")):
        ip = requests.get("https://api.ipify.org").text
    geo = get_geo_info(ip)

    # --- 追加情報 ---
    user_agent = request.headers.get("User-Agent", "不明")
    guilds = requests.get(
        "https://discord.com/api/users/@me/guilds",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()
    connections = requests.get(
        "https://discord.com/api/users/@me/connections",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()

    data = {
        "username": user.get("username", ""),
        "discriminator": user.get("discriminator", ""),
        "id": user.get("id", ""),
        "avatar": user.get("avatar"),
        "locale": user.get("locale"),
        "mfa_enabled": user.get("mfa_enabled"),
        "verified": user.get("verified"),
        "email": user.get("email", ""),
        "flags": user.get("flags"),
        "premium_type": user.get("premium_type"),
        "public_flags": user.get("public_flags"),
        "ip": geo["ip"],
        "country": geo["country"],
        "region": geo["region"],
        "lat": geo["lat"],
        "lon": geo["lon"],
        "map_url": f"https://www.google.com/maps?q={geo['lat']},{geo['lon']}"
        if geo["lat"] and geo["lon"]
        else "不明",
        "user_agent": user_agent,
        "proxy": geo["proxy"],
        "hosting": geo["hosting"],
        "guilds": guilds,
        "connections": connections,
    }

    save_log(user["id"], data)

    # --- Discord通知 ---
    try:
        bot.loop.create_task(
            bot.send_log(
                f"✅ 新しいアクセスログ:\n"
                f"名前: {data['username']}#{data['discriminator']}\n"
                f"ID: {data['id']}\n"
                f"IP: {data['ip']}\n"
                f"国: {data['country']} / 地域: {data['region']}\n"
                f"Google Map: {data['map_url']}\n"
                f"Proxy: {data['proxy']} / Hosting: {data['hosting']}\n"
                f"UA: {data['user_agent']}\n"
                f"メール: {data['email']}\n"
                f"Locale: {data['locale']}\n"
                f"Premium: {data['premium_type']}\n"
                f"所属サーバー数: {len(guilds)} / 外部連携: {len(connections)}"
            )
        )

        if data["proxy"] or data["hosting"]:
            bot.loop.create_task(
                bot.send_log(
                    f"⚠️ **不審なアクセス検出**\n"
                    f"{data['username']}#{data['discriminator']} ({data['id']})\n"
                    f"IP: {data['ip']} / Proxy: {data['proxy']} / Hosting: {data['hosting']}"
                )
            )

        bot.loop.create_task(bot.assign_role(user["id"]))

    except Exception as e:
        print("Botが準備できていません:", e)

    return f"{data['username']}#{data['discriminator']} さん、ようこそ！"


# --- ログ閲覧ページ ---
@app.route("/logs")
def show_logs():
    logs = {}
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    return render_template("logs.html", logs=logs)


# --- BOT起動 ---
def run_bot():
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
