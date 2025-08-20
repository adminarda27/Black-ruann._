# main.py
from flask import Flask, request, render_template
import requests, json, os, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot  # Discord Bot インスタンスを定義済み
from user_agents import parse

# -------------------
# 環境変数読み込み
# -------------------
load_dotenv()  # .env ファイルを読み込む

DISCORD_CLIENT_ID     = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN     = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_WEBHOOK_URL   = os.getenv("DISCORD_WEBHOOK_URL")
IPDATA_API_KEY        = os.getenv("IPDATA_API_KEY")

# -------------------
# Flaskアプリ初期化
# -------------------
app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

# -------------------
# IP / geolocation
# -------------------
def get_client_ip():
    """X-Forwarded-For対応"""
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

def get_ipdata_info(ip):
    """ipdataから位置情報・VPN検知などを取得"""
    url = f"https://api.ipdata.co/{ip}?api-key={IPDATA_API_KEY}"
    try:
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        return res.json()
    except:
        return {
            "ip": ip,
            "country_name": "不明",
            "region": "不明",
            "city": "不明",
            "asn": {"name": "不明"},
            "threat": {"is_vpn": False, "is_proxy": False, "is_tor": False}
        }

# -------------------
# ログ保存
# -------------------
def save_log(data):
    try:
        if not os.path.exists(ACCESS_LOG_FILE):
            with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, ensure_ascii=False, indent=2)

        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)

        logs.append(data)

        with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("ログ保存エラー:", e)

# -------------------
# Discord Embed送信
# -------------------
def send_to_discord(structured_data):
    ip_info = structured_data["ip_info"]
    ua_info = structured_data["user_agent"]

    is_vpn = ip_info.get("threat", {}).get("is_vpn", False) \
             or ip_info.get("threat", {}).get("is_proxy", False) \
             or ip_info.get("threat", {}).get("is_tor", False)

    embed = {
        "title": "🌐 新しいアクセスログ",
        "color": 0xE74C3C if is_vpn else 0x3498db,
        "fields": [
            {
                "name": "📍 位置情報",
                "value": f"{ip_info.get('city','不明')} - {ip_info.get('region','不明')} - {ip_info.get('country_name','不明')}",
                "inline": False
            },
            {
                "name": "🌐 ネットワーク",
                "value": f"IP: `{ip_info.get('ip','不明')}`\nISP: {ip_info.get('asn',{}).get('name','不明')}",
                "inline": False
            },
            {
                "name": "💻 ユーザーエージェント",
                "value": f"OS: {ua_info['os']} / ブラウザ: {ua_info['browser']} / デバイス: {ua_info['device']}",
                "inline": False
            }
        ]
    }

    if is_vpn:
        embed["fields"].append({
            "name": "⚠️ セキュリティ警告",
            "value": "**VPN / Proxy / Tor 検出**\nアクセスをブロックしてください。",
            "inline": False
        })

    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})
    except Exception as e:
        print("Discord送信エラー:", e)

# -------------------
# Flaskルート
# -------------------
@app.route("/")
def index():
    user_ip = get_client_ip()
    ua = parse(request.headers.get("User-Agent",""))

    ip_info = get_ipdata_info(user_ip)

    structured_data = {
        "ip_info": ip_info,
        "user_agent": {
            "os": ua.os.family,
            "browser": ua.browser.family,
            "device": "Mobile" if ua.is_mobile else "Tablet" if ua.is_tablet else "PC" if ua.is_pc else "Other"
        },
        "timestamp": datetime.utcnow().isoformat()
    }

    threading.Thread(target=save_log, args=(structured_data,)).start()
    threading.Thread(target=send_to_discord, args=(structured_data,)).start()

    return render_template("index.html", ip=structured_data["ip_info"], ua=structured_data["user_agent"])

@app.route("/logs")
def show_logs():
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE,"r",encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = []
    return render_template("logs.html", logs=logs)

# -------------------
# Discord Bot同時起動
# -------------------
def run_bot():
    bot.run(DISCORD_BOT_TOKEN)

# -------------------
# メイン起動
# -------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))  # Render対応ポート
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=port)
