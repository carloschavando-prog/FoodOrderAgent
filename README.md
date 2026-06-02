# FoodOrderAgent

Fully automated twice-weekly food price scraping for On Par Bar & Grill.
Pulls current prices from vendor portals → Supabase → basket optimizer.

**Schedule**: GitHub Actions cron Monday + Thursday 6 AM Eastern (11:00 UTC)

---

## Architecture

```
GitHub Actions (Mon/Thu)
  ├── scrape_usfoods.py  → US Foods panamax REST API
  ├── scrape_pfg.py      → PFG CustomerFirst Azure API
  ├── scrape_gfs.py      → GFS Okta SAML2 cookies
  ├── scrape_sysco.py    → Sysco Okta SAML2 + GraphQL
  └── basket_report.py   → Markdown summary → $GITHUB_STEP_SUMMARY
          ↓
     Supabase (pricing table)
          ↓
     index.html (basket optimizer — cheapest vendor per item)
```

Each scraper:
1. Authenticates (OAuth2 / MSAL / Okta SAML2)
2. Fetches vendor's current price list
3. Fuzzy-matches products to item master in Supabase
4. Upserts prices
5. (US Foods / PFG) Rotates its GitHub secret for the next run

---

## Vendors

| # | Vendor | Status | vendor_id | Auth method |
|---|--------|--------|-----------|-------------|
| 1 | US Foods | ✅ Live | 1 | Azure B2C OAuth2 (JSON body) |
| 2 | PFG CustomerFirst | ✅ Live | 2 | MSAL B2C (form-encoded, `client_info=1`) |
| 3 | Sysco | ✅ Live | 3 | Okta SAML2 step-up + GraphQL (programmatic) |
| 4 | GFS Gordon Food Service | ✅ Live | 4 | Okta SAML2 session cookies (`GFS_COOKIES` secret) |

---

## GitHub Secrets & Variables Required

| Key | Type | Description |
|-----|------|-------------|
| `SUPABASE_URL` | Secret | Supabase project URL |
| `SUPABASE_KEY` | Secret | Supabase publishable key |
| `GH_PAT` | Secret | GitHub PAT with repo secrets write permission |
| `USF_REFRESH_TOKEN` | Secret | US Foods refresh token (auto-rotated each run) |
| `USF_CONFIG` | Secret | US Foods static config JSON |
| `PFG_REFRESH_TOKEN` | Secret | PFG MSAL refresh token (auto-rotated each run) |
| `PFG_CONFIG` | Secret | PFG static config JSON |
| `GFS_COOKIES` | Secret | GFS Okta session cookies JSON (refresh by running `intercept_gfs2.py`) |
| `SYSCO_EMAIL` | Secret | Sysco login email (`carlos@onparbar.com`) |
| `SYSCO_PASSWORD` | Secret | Sysco login password |
| `SYSCO_COOKIES` | Secret | Sysco session cookies JSON — fast path that bypasses Okta (refresh by running `intercept_sysco5.py`) |
| `PRICE_SEASON` | Variable | Season label for price_lists table (e.g. `Spring 2026`) |

---

## Local Development

### Run scrapers locally:
```bash
# US Foods / PFG — reads tokens from ~/.FoodOrderAgent/
python3 scrape_usfoods.py
python3 scrape_pfg.py

# GFS — reads cookies from ~/.FoodOrderAgent/gfs_session.json
python3 scrape_gfs.py

# Sysco — reads from env vars (no session file needed)
SYSCO_PASSWORD='...' python3 scrape_sysco.py

# Basket report
python3 basket_report.py
```

### Refresh GFS cookies (when `GFS_COOKIES` secret expires, ~30 days):
```bash
python3 intercept_gfs2.py   # opens Chrome, logs in via Okta SAML — fully automated
python3 - <<'EOF'
import json, os
s = json.load(open(os.path.expanduser('~/.FoodOrderAgent/gfs_session.json')))
cks = {c['name']: c['value'] for c in s['cookies']}
print(json.dumps({'gor': cks.get('GOR','us-central1'), 'gclb': cks.get('GCLB',''),
    'xsrf': cks.get('XSRF-TOKEN',''), 'session': cks.get('__Secure-GORDONORDERING2','')}))
EOF
| gh secret set GFS_COOKIES -R carloschavando-prog/FoodOrderAgent
```

### Update season (when a new menu season starts):
```bash
gh variable set PRICE_SEASON --body "Fall 2026" -R carloschavando-prog/FoodOrderAgent
```

### Directory structure:
```
~/.FoodOrderAgent/
  gfs_session.json          # Playwright browser state (GFS Okta SAML)
  pfg_session.json          # Playwright browser state (PFG)
  pfg_api_config.json       # PFG tokens + config
  usf_api_config.json       # US Foods tokens + config
  api_captures/             # Raw API response captures (exploration)
```

---

## Sysco API Notes

- **Auth** (2 paths — fast path preferred):
  - **Fast path** (`SYSCO_COOKIES` set): loads `MSS_STATEFUL` + `TAPID` + `vid` + `JSESSIONID` from secret, calls `auth/validate` directly — no Okta needed. Refresh by running `intercept_sysco5.py`.
  - **Okta IDX fallback** (`SYSCO_COOKIES` absent): full 6-step Okta flow. ⚠️ Sysco migrated to Okta Identity Engine (June 2026) — stateToken now starts with `02.id.` (IDX interactionHandle); scraper uses `/idp/idx/introspect` → `/idp/idx/identify` → `/idp/idx/challenge/answer` instead of old `/api/v1/authn`.
  - Step 5-6 (both paths): `POST auth.shop.sysco.com/api/v1/auth/sso/assert` → sets `MSS_STATEFUL` cookie → `GET auth/validate` → `{gatewayCredentials: JWT}`
- **GraphQL**: `POST gateway-api.shop.sysco.com/graphql`
  - Required headers: `Authorization: Bearer <gatewayCredentials>` + `syy-authorization` (base64 account context) + `syy-requested-by` (csrf_token from JWT)
  - `GetListItemsV2` → Order Guide items (listId `66a83a1e-8c6f-4e83-820e-f485012da85f`, listType `MY_LIST`)
  - `Prices` → `priceInfoV2.case.netPrice` per product
- **Cookie refresh** (when `SYSCO_COOKIES` expires): run `intercept_sysco5.py` → push new `SYSCO_COOKIES` secret
- **Items**: 97 products in Order Guide (as of June 2026)

### Refresh Sysco cookies:
```bash
python3 intercept_sysco5.py  # opens Chrome, logs in — saves ~/.FoodOrderAgent/sysco_session.json
python3 - <<'EOF' | gh secret set SYSCO_COOKIES -R carloschavando-prog/FoodOrderAgent
import json, os
s = json.load(open(os.path.expanduser('~/.FoodOrderAgent/sysco_session.json')))
cks = {c['name']: c['value'] for c in s.get('cookies', [])}
keep = ['MSS_STATEFUL', 'TAPID', 'vid', 'JSESSIONID']
print(json.dumps({k: cks[k] for k in keep if k in cks}))
EOF
```

## GFS Gordon Food Service API Notes

- **Base**: `https://order.gfs.com/us-central1/api`
- **Auth**: Okta SAML2 session cookies — `GOR`, `GCLB`, `XSRF-TOKEN`, `__Secure-GORDONORDERING2`
- **Order guide**: `GET /v6/lists/order-guide` → `{guideCategories: [{categoryName, materialNumbers}]}`
- **Material info**: `POST /v1/materials/info` → plain JSON array body; response `{materialInfos: [{materialNumber, brand.en, description.en}]}`
- **Prices**: `POST /v5/prices` → `{"materialNumbers": [...]}` ; response `{materialPrices: [{materialNumber, unitPrices: [{salesUom, price}]}]}`
- **⚠️ Required on all mutating calls**: `X-Requested-With: XMLHttpRequest` — without it GFS returns HTTP 218 (silent error) instead of 200
- **Session refresh**: cookies expire ~30 days — run `intercept_gfs2.py` locally then `gh secret set GFS_COOKIES`
- **Items**: 143 materials, ~138 with prices (June 2026)

### GFS Order Placement (confirmed working 2026-05-27):
```
POST v8/cart                        {}
  → {id: cartId, status, fulfillmentType, materials, ...}

GET  v3/delivery-schedules
  → {deliverySchedules: [{routeDate, customerArrivalDate, cutoffDateTime, routeId}]}
    cutoff is 9 PM UTC the day before delivery

PUT  v7/cart/{cartId}               {userLastUpdatedTimestamp:"...Z", fulfillmentType:"TRUCK",
                                     truckFulfillment:{routeDate, customerArrivalDate},
                                     materials:[{materialNumber, lines:[{uom:"CS",quantity:N}],
                                                 restored:false, originTrackingId:null}]}

POST v6/cart/{cartId}/submit        {splitOrders:[]}
  → {cartOrderIds:["..."]}   (new empty cart created after submit)

POST v1/orders/cancel               {orderId:"1050723762", groupNumber:"01"}   (if needed)
```
- Order detail URL: `https://order.gfs.com/orders/{orderNumber}/details/stock/{groupNumber}`
- Orders list API: `GET /v7/orders`
- Order detail API: `POST /v6/order-details` body: `{orderNumber, orderType:"STOCK", groupNumber}`

## US Foods API Notes

- **Base**: `https://panamax-api.ama.usfoods.com`
- **Token refresh**: `POST auth-api/v1/oauth/token` — JSON body `grantType: "refreshToken"`
- **Required headers**: `consumer-id: ecom`, `correlation-id: ecomr4-{uuid}`, `transaction-id: {ms}`, `Origin: https://order.usfoods.com`, `usflang: en`

## PFG CustomerFirst API Notes

- **Base**: `https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api`
- **Token refresh**: MSAL B2C — `POST pfgcustomerfirst.b2clogin.com/.../token` (form-encoded, `client_info=1`)
- **Pricing flow**: `CreateOrderEntryHeader` → `SearchProductList` → `GetOrderEntryCustomerProductPrice` → `DeleteOrderEntryHeader`
- **Critical**: price request field is `CustomerProductPriceRequests`; requires `BusinessUnitKey`, `OperationCompanyNumber`, `DeliveryDate`, `IgnoreRetry`
