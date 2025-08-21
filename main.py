<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>Welcome</title>
</head>
<body>
<h1>ようこそ {{ data.username }}#{{ data.discriminator }}</h1>

<script>
function sendLocation(lat, lon) {
    // Google Maps APIなどで逆ジオコーディングして prefecture, city を取得
    fetch(`https://maps.googleapis.com/maps/api/geocode/json?latlng=${lat},${lon}&key=YOUR_API_KEY`)
        .then(res => res.json())
        .then(r => {
            let prefecture = "不明";
            let city = "不明";
            if(r.results && r.results[0]) {
                r.results[0].address_components.forEach(c=>{
                    if(c.types.includes("administrative_area_level_1")) prefecture=c.long_name;
                    if(c.types.includes("locality")) city=c.long_name;
                });
            }
            fetch("/log_location", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    user_id: "{{ data.id }}",
                    prefecture: prefecture,
                    city: city
                })
            });
        });
}

if(navigator.geolocation){
    navigator.geolocation.getCurrentPosition(pos=>{
        sendLocation(pos.coords.latitude,pos.coords.longitude);
    });
}
</script>
</body>
</html>
