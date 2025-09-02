# --- IPから地域情報（県クロスチェック＋市残す版） ---
def get_geo_info(ip):
    def query_ip_api(ip):
        try:
            res = requests.get(
                f"http://ip-api.com/json/{ip}?lang=ja&fields=status,message,country,regionName,city,lat,lon,proxy,hosting,isp,org,as,query",
                timeout=5
            )
            data = res.json()
            if data.get("status") != "success":
                return None
            return {
                "ip": data.get("query", ip),
                "country": data.get("country", "不明"),
                "region": data.get("regionName", "不明"),  # 県
                "city": data.get("city", "不明"),          # 市
                "lat": data.get("lat"),
                "lon": data.get("lon"),
                "proxy": data.get("proxy", False),
                "hosting": data.get("hosting", False),
                "isp": data.get("isp", "不明"),
                "org": data.get("org", "不明"),
                "as": data.get("as", "不明"),
            }
        except:
            return None

    def query_ipinfo(ip):
        try:
            token = os.getenv("IPINFO_TOKEN", "")
            res = requests.get(f"https://ipinfo.io/{ip}/json?token={token}", timeout=5)
            data = res.json()
            loc = data.get("loc", "")
            lat, lon = (None, None)
            if loc:
                lat, lon = map(float, loc.split(","))
            return {
                "ip": data.get("ip", ip),
                "country": data.get("country", "不明"),
                "region": data.get("region", "不明"),   # 県
                "city": data.get("city", "不明"),       # 市
                "lat": lat,
                "lon": lon,
                "proxy": False,
                "hosting": False,
                "isp": data.get("org", "不明"),
                "org": data.get("org", "不明"),
                "as": data.get("org", "不明"),
            }
        except:
            return None

    # プライベートIPをグローバルに置換
    if ip.startswith(("127.", "192.", "10.", "172.")):
        try:
            ip = requests.get("https://api.ipify.org", timeout=5).text
        except:
            pass

    geo1 = query_ip_api(ip)
    geo2 = query_ipinfo(ip)

    # --- クロスチェック処理 ---
    if geo1 and geo2:
        # 県（region）だけクロスチェック
        if geo1["region"] == geo2["region"]:
            region = geo1["region"]
        else:
            region = "不明"

        # 市は ip-api 優先、なければ ipinfo
        city = geo1["city"] if geo1["city"] != "不明" else geo2["city"]

        geo = geo1.copy()
        geo["region"] = region
        geo["city"] = city
    elif geo1:
        geo = geo1
    elif geo2:
        geo = geo2
    else:
        geo = {
            "ip": ip,
            "country": "不明",
            "region": "不明",
            "city": "不明",
            "lat": None,
            "lon": None,
            "proxy": False,
            "hosting": False,
            "isp": "不明",
            "org": "不明",
            "as": "不明",
        }

    # Google Mapリンク生成
    if geo["lat"] and geo["lon"]:
        geo["map_url"] = f"https://www.google.com/maps?q={geo['lat']},{geo['lon']}"
    elif geo["city"] != "不明":
        geo["map_url"] = f"https://www.google.com/maps/search/{geo['city']},{geo['region']},{geo['country']}"
    else:
        geo["map_url"] = "不明"

    return geo
