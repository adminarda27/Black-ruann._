# main.py
from flask import Flask, request, render_template
import requests, json, os, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot
from user_agents import parse

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
IPDATA_API_KEY = os.getenv("IPDATA_API_KEY")

def get_ipdata_info(ip):
    url = f"https://api.ipdata.co/{ip}?api-key={IPDATA_API_KEY}"
    try:
        res = requests.get(url, timeout=5)
        return res.json()
    except:
        return {}

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

def send_to_discord(structured_data):
    import requests

    ip_info = structured_data["ip_info"]
    ua_info = structured_data["user_agent"]

    is_vpn = ip_info.get("threat", {}).get("is_vpn", False)

    embed = {
        "title": "🌐 新しいアクセスログ",
        "color": 0x3498db if not is_vpn else 0xE74C3C,  # VPNなら赤
        "fields": [
            {
                "name": "📍 位置情報",
                "value": f"{ip_info.get('city', '不明')} - {ip_info.get('region', '不明')} - {ip_info.get('country_name', '不明')}",
                "inline": False
            },
            {
                "name": "🌐 ネットワーク",
                "value": f"IP: `{ip_info.get('ip', '不明')}`\nISP: {ip_info.get('asn', {}).get('name', '不明')}",
                "inline": False
            },
            {
                "name": "💻 ユーザーエージェント",
                "value": f"OS: {ua_info['os']} / ブラウザ: {ua_info['browser']}",
                "inline": False
            }
        ]
    }

    # VPN警告を追加
    if is_vpn:
        embed["fields"].append({
            "name": "⚠️ セキュリティ警告",
            "value": "**VPN または Proxy が検知されました！**\nアクセスをブロックしてください。",
            "inline": False
        })

    requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})

@app.route("/")
def index():
    user_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    ua = parse(request.headers.get("User-Agent"))

    ip_info = get_ipdata_info(user_ip)

    structured_data = {
        "ip_info": ip_info,
        "user_agent": {
            "os": ua.os.family,
            "browser": ua.browser.family,
            "device": ua.device.family
        },
        "timestamp": datetime.utcnow().isoformat()
    }

    # 保存 & Discord送信
    threading.Thread(target=save_log, args=(structured_data,)).start()
    threading.Thread(target=send_to_discord, args=(structured_data,)).start()

    return render_template("index.html", ip=structured_data["ip_info"], ua=structured_data["user_agent"])

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
