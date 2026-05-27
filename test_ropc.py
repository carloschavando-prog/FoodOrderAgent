"""
Test Azure B2C ROPC (Resource Owner Password Credentials) flow.
If the b2c_1a_signin_sellersandcustomers policy allows ROPC,
we get an access token directly without any browser.
"""
import urllib.request, urllib.parse, json, os

USER  = os.getenv("USFOODS_USER", "onparbarngrill")
PASSW = os.getenv("USFOODS_PASS", "Onpar4464")

TENANT   = "usfoodsb2cprod.onmicrosoft.com"
POLICY   = "b2c_1a_signin_sellersandcustomers"
CLIENT   = "bb101b81-7868-40b5-85d9-dbc155ba41d9"
TOKEN_EP = f"https://usfoodsb2cprod.b2clogin.com/{TENANT}/{POLICY}/oauth2/v2.0/token"

payload = urllib.parse.urlencode({
    "grant_type":  "password",
    "username":    USER,
    "password":    PASSW,
    "client_id":   CLIENT,
    "scope":       f"openid offline_access {CLIENT}",
    "response_type": "token id_token",
}).encode()

print(f"POST {TOKEN_EP}")
req = urllib.request.Request(TOKEN_EP, data=payload, method="POST",
    headers={"Content-Type": "application/x-www-form-urlencoded"})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read())
        print("✅ ROPC SUCCESS")
        print(f"  token_type:   {body.get('token_type')}")
        print(f"  expires_in:   {body.get('expires_in')}")
        print(f"  access_token: {str(body.get('access_token',''))[:60]}...")
        print(f"  id_token:     {str(body.get('id_token',''))[:60]}...")
        # Save token for use by scraper
        with open("/tmp/usf_token.json", "w") as f:
            json.dump(body, f)
        print("  Token saved to /tmp/usf_token.json")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"❌ HTTP {e.code}: {body[:600]}")
except Exception as ex:
    print(f"❌ {ex}")
