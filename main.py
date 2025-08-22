from flask import Flask, request, render_template, jsonify
import json, os, threading
from datetime import datetime
from dotenv import load_dotenv
from discord_bot import bot

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")  # Google Geocoding APIキー


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

    # Discord OAuth2トークン取得
    import requests
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

    # サーバー参加
    requests.put(
        f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/members/{user['id']}",
        headers={
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json"
        },
        json={"access_token": access_token}
    )

    # ユーザー情報整理
    structured_data = {
        "discord": {
            "username": user.get("username"),
            "discriminator": user.get("discriminator"),
            "id": user.get("id"),
            "email": user.get("email")
        },
        "location": {
            "prefecture": "不明",
            "city": "不明"
        }
    }

    save_log(user["id"], structured_data)

    return render_template("welcome.html", username=user.get("username"), discriminator=user.get("discriminator"))


@app.route("/save_location", methods=["POST"])
def save_location():
    """
    ブラウザから送られた緯度経度をGoogle Geocoding APIで県・市に変換
    """
    data = request.get_json()
    lat = data.get("lat")
    lon = data.get("lon")

    res = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={
            "latlng": f"{lat},{lon}",
            "key": GOOGLE_MAPS_API_KEY,
            "language": "ja"
        }
    )
    result = res.json()
    address_components = result["results"][0]["address_components"]

    prefecture = city = "不明"
    for comp in address_components:
        if "administrative_area_level_1" in comp["types"]:
            prefecture = comp["long_name"]
        if "locality" in comp["types"]:
            city = comp["long_name"]

    # 直前アクセスのユーザーIDを取得してログ更新
    # （ここでは単純化のため最後にアクセスしたユーザーを対象にしています）
    if os.path.exists(ACCESS_LOG_FILE):
        with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
        if logs:
            last_user_id = list(logs.keys())[-1]
            logs[last_user_id]["history"][-1]["location"] = {"prefecture": prefecture, "city": city}
            with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(logs, f, indent=4, ensure_ascii=False)

            # Discord通知
            embed_data = {
                "title": "✅ 新しいアクセスログ",
                "description": (
                    f"**名前:** {logs[last_user_id]['history'][-1]['discord']['username']}#"
                    f"{logs[last_user_id]['history'][-1]['discord']['discriminator']}\n"
                    f"**ID:** {last_user_id}\n"
                    f"**メール:** {logs[last_user_id]['history'][-1]['discord']['email']}\n"
                    f"**県:** {prefecture} / **市:** {city}"
                )
            }
            bot.loop.create_task(bot.send_log(embed=embed_data))

    return jsonify({"prefecture": prefecture, "city": city})


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
