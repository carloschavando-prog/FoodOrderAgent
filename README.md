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
  ├── scrape_sysco.py    → Sysco (TODO)
  └── scrape_gfs.py      → GFS (TODO)
          ↓
     Supabase (pricing table)
          ↓
     index.html (basket optimizer — cheapest vendor per item)
```

Each scraper:
1. Refreshes its Bearer/MSAL token (rotating refresh token chain)
2. Fetches vendor's current price list
3. Fuzzy-matches products to item master in Supabase
4. Upserts prices
5. Rotates its GitHub secret (`*_REFRESH_TOKEN`) for the next run

---

## Vendors

| # | Vendor | Status | Supabase vendor_id | Auth method |
|---|--------|--------|--------------------|-------------|
| 1 | US Foods | ✅ Live | 1 | Azure B2C OAuth2 (JSON body) |
| 2 | PFG CustomerFirst | ✅ Live | 2 | MSAL B2C (form-encoded, `client_info=1`) |
| 3 | Sysco | 🔲 TODO | 3 | TBD |
| 4 | GFS | 🔲 TODO | 4 | TBD |

---

## GitHub Secrets Required

| Secret | Description |
|--------|-------------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase publishable key |
| `GH_PAT` | GitHub PAT with repo secrets write permission |
| `USF_REFRESH_TOKEN` | US Foods refresh token (auto-rotated each run) |
| `USF_CONFIG` | US Foods static config JSON |
| `PFG_REFRESH_TOKEN` | PFG MSAL refresh token (auto-rotated each run) |
| `PFG_CONFIG` | PFG static config JSON |

---

## Local Development

### One-time setup (capture tokens via browser):
```bash
# US Foods
python3 intercept_api.py          # opens Chrome once to capture tokens

# PFG CustomerFirst
python3 intercept_pfg7.py         # opens Chrome once to capture tokens
```
Sessions saved to `~/.FoodOrderAgent/`

### Run scrapers locally:
```bash
python3 scrape_usfoods.py
python3 scrape_pfg.py
```

### Directory structure:
```
~/.FoodOrderAgent/
  pfg_session.json          # Playwright browser state
  pfg_api_config.json       # PFG tokens + config
  usf_api_config.json       # US Foods tokens + config
  api_captures/             # Raw API response captures (exploration)
```

---

## US Foods API Notes

- **Base**: `https://panamax-api.ama.usfoods.com`
- **Token refresh**: `POST auth-api/v1/oauth/token` — JSON body with `grantType: "refreshToken"`
- **Required headers on ALL calls**: `consumer-id: ecom`, `correlation-id: ecomr4-{uuid}`, `transaction-id: {ms}`, `Origin: https://order.usfoods.com`, `usflang: en`
- **Fall 2025 list ID**: `1000643297`

## PFG CustomerFirst API Notes

- **Base**: `https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api`
- **Token refresh**: MSAL B2C — `POST pfgcustomerfirst.b2clogin.com/.../token` (form-encoded, `client_info=1`, scope WITHOUT trailing slash)
- **Pricing flow**: `CreateOrderEntryHeader` → `SearchProductList` → `GetOrderEntryCustomerProductPrice` → `DeleteOrderEntryHeader`
- **Critical**: price request field is `CustomerProductPriceRequests` (not `CustomerProductPrices`); requires `BusinessUnitKey`, `OperationCompanyNumber`, `DeliveryDate`, `IgnoreRetry`
- **Fall 2025 list ID**: `13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60`
