-- ============================================================
--  FoodOrderAgent — Supabase Schema
-- ============================================================

-- VENDORS -------------------------------------------------
CREATE TABLE IF NOT EXISTS vendors (
  id          SERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  contact     TEXT,
  phone       TEXT,
  min_order   TEXT,
  order_notes TEXT,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO vendors (name, contact, phone, min_order, order_notes) VALUES
  ('US FOODS',    'Jason Brandell',  '937-901-7211', '20 cases',   'Order by Monday 2:00 for Tuesday delivery'),
  ('PFG',         'Chris Tamillo',   '937-608-6599', '20 cases',   'Order by Thursday 2:00 for Thursday delivery'),
  ('SYSCO',       'Aaron Huber',     '937-204-5659', '20 cases',   NULL),
  ('GFS',         'Mark Lasson',     '937-815-6861', '$750 min',   NULL),
  ('I SUPPLY',    NULL,              NULL,           NULL,         NULL),
  ('MARKETS DEPOT', NULL,            NULL,           NULL,         NULL),
  ('MEAT CHURCH', NULL,              NULL,           NULL,         NULL)
ON CONFLICT (name) DO NOTHING;

-- CATEGORIES ----------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
  id         SERIAL PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  sort_order INT DEFAULT 0
);

INSERT INTO categories (name, sort_order) VALUES
  ('Paper Goods',    1),
  ('Spice Shelf',    2),
  ('Tortilla Shelf', 3),
  ('Dry Stock',      4),
  ('Disposables',    5),
  ('Walk-In Cooler', 6),
  ('Freezer',        7),
  ('Chemical Room',  8),
  ('Beverage Dock',  9)
ON CONFLICT (name) DO NOTHING;

-- ITEMS (master list) -------------------------------------
CREATE TABLE IF NOT EXISTS items (
  id                  SERIAL PRIMARY KEY,
  name                TEXT NOT NULL,
  category_id         INT  REFERENCES categories(id),
  pack_size           TEXT,
  par_level           NUMERIC,
  preferred_vendor_id INT  REFERENCES vendors(id),
  created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- PRICE LISTS (one row per vendor import session) ---------
CREATE TABLE IF NOT EXISTS price_lists (
  id          SERIAL PRIMARY KEY,
  vendor_id   INT  NOT NULL REFERENCES vendors(id),
  season      TEXT NOT NULL,           -- e.g. 'Fall 2025'
  pulled_at   TIMESTAMPTZ DEFAULT NOW(),
  notes       TEXT
);

-- PRICING (vendor prices per item per list) ---------------
CREATE TABLE IF NOT EXISTS pricing (
  id            SERIAL PRIMARY KEY,
  item_id       INT     NOT NULL REFERENCES items(id),
  vendor_id     INT     NOT NULL REFERENCES vendors(id),
  price_list_id INT     REFERENCES price_lists(id),
  apn           TEXT,                  -- vendor item number
  price         NUMERIC(10,2) NOT NULL,
  pack_size     TEXT,
  effective_date DATE,
  pulled_at     TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE (item_id, vendor_id, price_list_id)
);

-- BASKETS (saved optimal order combinations) ---------------
CREATE TABLE IF NOT EXISTS baskets (
  id           SERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  created_at   TIMESTAMPTZ DEFAULT NOW(),
  total_cost   NUMERIC(10,2),
  notes        TEXT
);

CREATE TABLE IF NOT EXISTS basket_items (
  id          SERIAL PRIMARY KEY,
  basket_id   INT     NOT NULL REFERENCES baskets(id) ON DELETE CASCADE,
  item_id     INT     NOT NULL REFERENCES items(id),
  vendor_id   INT     NOT NULL REFERENCES vendors(id),
  quantity    NUMERIC NOT NULL DEFAULT 1,
  unit_price  NUMERIC(10,2),
  line_total  NUMERIC(10,2) GENERATED ALWAYS AS (quantity * unit_price) STORED,
  UNIQUE (basket_id, item_id)
);

-- INVENTORY SNAPSHOTS ------------------------------------
-- One header row per physical count, with line items below.
-- Used by the Integrator app for food/beverage cost:
-- starting inventory + purchases - ending inventory / sales.
CREATE TABLE IF NOT EXISTS inventory_snapshots (
  id        SERIAL PRIMARY KEY,
  taken_at  TIMESTAMPTZ DEFAULT NOW(),
  taken_by  TEXT,
  notes     TEXT,
  order_overrides JSONB NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE inventory_snapshots
  ADD COLUMN IF NOT EXISTS order_overrides JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE TABLE IF NOT EXISTS inventory_snapshot_items (
  id           SERIAL PRIMARY KEY,
  snapshot_id  INT NOT NULL REFERENCES inventory_snapshots(id) ON DELETE CASCADE,
  item_id      INT REFERENCES items(id),
  item_name    TEXT NOT NULL,
  on_hand_qty  NUMERIC NOT NULL,
  unit         TEXT DEFAULT 'case'
);

-- PARTY DEMAND SNAPSHOTS --------------------------------
-- Server-owned, replacement snapshots of one delivery coverage window. JSONB
-- retains the source/audit detail without exposing Event Kitchen credentials.
CREATE TABLE IF NOT EXISTS party_demand_snapshots (
  id                       BIGSERIAL PRIMARY KEY,
  delivery_cycle           TEXT NOT NULL
                           CHECK (delivery_cycle IN ('tuesday', 'friday')),
  delivery_date            DATE NOT NULL,
  coverage_start           DATE NOT NULL,
  coverage_end             DATE NOT NULL,
  source_event_ids         JSONB NOT NULL DEFAULT '[]',
  event_audit              JSONB NOT NULL DEFAULT '[]',
  raw_requirements         JSONB NOT NULL DEFAULT '[]',
  aggregated_raw           JSONB NOT NULL DEFAULT '[]',
  item_totals              JSONB NOT NULL DEFAULT '[]',
  last_successful_sync     TIMESTAMPTZ,
  source_status            TEXT NOT NULL DEFAULT 'error'
                           CHECK (source_status IN ('ok', 'warning', 'error', 'unconfigured')),
  source_mode              TEXT,
  source_warnings          JSONB NOT NULL DEFAULT '[]',
  blocking_warnings        JSONB NOT NULL DEFAULT '[]',
  stale                    BOOLEAN NOT NULL DEFAULT FALSE,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (coverage_end >= coverage_start)
);

ALTER TABLE inventory_snapshots
  ADD COLUMN IF NOT EXISTS party_demand_snapshot_id BIGINT
  REFERENCES party_demand_snapshots(id);

CREATE TABLE IF NOT EXISTS party_demand_overrides (
  id                       BIGSERIAL PRIMARY KEY,
  party_demand_snapshot_id BIGINT NOT NULL
                           REFERENCES party_demand_snapshots(id),
  inventory_snapshot_id    INT REFERENCES inventory_snapshots(id),
  reason                   TEXT NOT NULL CHECK (length(trim(reason)) > 0),
  overridden_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS party_demand_window_idx
  ON party_demand_snapshots
  (delivery_cycle, delivery_date, created_at DESC);
CREATE INDEX IF NOT EXISTS party_demand_override_snapshot_idx
  ON party_demand_overrides (party_demand_snapshot_id, overridden_at DESC);
CREATE INDEX IF NOT EXISTS party_demand_override_inventory_idx
  ON party_demand_overrides (inventory_snapshot_id);
CREATE INDEX IF NOT EXISTS inventory_snapshot_party_demand_idx
  ON inventory_snapshots (party_demand_snapshot_id);

ALTER TABLE party_demand_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE party_demand_overrides ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON party_demand_snapshots, party_demand_overrides
  FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON party_demand_snapshots, party_demand_overrides TO service_role;
REVOKE ALL ON SEQUENCE party_demand_snapshots_id_seq,
  party_demand_overrides_id_seq FROM anon, authenticated;
GRANT USAGE, SELECT ON SEQUENCE party_demand_snapshots_id_seq,
  party_demand_overrides_id_seq TO service_role;

-- PRICE COMPARISON VIEW -----------------------------------
-- Shows cheapest vendor for each item across all price lists
CREATE OR REPLACE VIEW cheapest_prices AS
SELECT
  i.id          AS item_id,
  i.name        AS item_name,
  c.name        AS category,
  p.apn,
  p.pack_size,
  v.name        AS vendor,
  p.price,
  pl.season,
  p.pulled_at,
  RANK() OVER (
    PARTITION BY i.id
    ORDER BY p.price ASC
  ) AS price_rank
FROM pricing p
JOIN items    i  ON i.id = p.item_id
JOIN vendors  v  ON v.id = p.vendor_id
JOIN categories c ON c.id = i.category_id
LEFT JOIN price_lists pl ON pl.id = p.price_list_id;

-- BASKET SAVINGS VIEW -------------------------------------
-- Shows how much each basket saves vs always buying from most expensive vendor
CREATE OR REPLACE VIEW basket_savings AS
SELECT
  b.id          AS basket_id,
  b.name        AS basket_name,
  b.total_cost,
  SUM(bi.unit_price * bi.quantity) AS basket_total,
  SUM(cp_max.price * bi.quantity)  AS worst_case_total,
  SUM(cp_max.price * bi.quantity) - SUM(bi.unit_price * bi.quantity) AS savings
FROM baskets b
JOIN basket_items bi ON bi.basket_id = b.id
JOIN (
  SELECT item_id, MAX(price) AS price
  FROM pricing
  GROUP BY item_id
) cp_max ON cp_max.item_id = bi.item_id
GROUP BY b.id, b.name, b.total_cost;

-- VENDOR TOKENS (rotating API credentials per vendor) ----
-- Used for vendors whose portals expose an internal REST API
-- (US Foods panamax-api, and others discovered via intercept_api.py).
-- In GitHub Actions the refresh token is stored as the USF_REFRESH_TOKEN
-- secret and rotated after each run. This table is for local / dashboard use.
CREATE TABLE IF NOT EXISTS vendor_tokens (
  id            SERIAL PRIMARY KEY,
  vendor_id     INT  NOT NULL REFERENCES vendors(id) UNIQUE,
  refresh_token TEXT,
  config_json   JSONB NOT NULL DEFAULT '{}',
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- RLS: enable row-level security on sensitive tables ------
ALTER TABLE pricing     ENABLE ROW LEVEL SECURITY;
ALTER TABLE baskets     ENABLE ROW LEVEL SECURITY;
ALTER TABLE basket_items ENABLE ROW LEVEL SECURITY;

-- FINAL FOOD ORDERS + PRICING FEEDBACK ------------------
-- A feedback preview is prepared only after the header is finalized and every
-- expected vendor submission has a successful confirmation in
-- vendor_confirmations.  Decision rows retain the optimizer's machine-readable
-- reason; email rendering only reads rows whose reason is lost_on_price.
CREATE TABLE IF NOT EXISTS food_orders (
  id                    UUID PRIMARY KEY,
  order_date            DATE NOT NULL,
  status                TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'finalized')),
  inventory_snapshot_id INT REFERENCES inventory_snapshots(id),
  expected_supplier_ids INT[] NOT NULL DEFAULT '{}',
  vendor_confirmations  JSONB NOT NULL DEFAULT '{}',
  item_total            INT NOT NULL DEFAULT 0 CHECK (item_total >= 0),
  case_total            NUMERIC NOT NULL DEFAULT 0 CHECK (case_total >= 0),
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finalized_at          TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS food_order_decisions (
  id                           BIGSERIAL PRIMARY KEY,
  order_id                     UUID NOT NULL REFERENCES food_orders(id) ON DELETE CASCADE,
  canonical_product_key        TEXT NOT NULL,
  item_id                      INT REFERENCES items(id),
  item_name                    TEXT NOT NULL,
  supplier_id                  INT NOT NULL REFERENCES vendors(id),
  selected_supplier_id         INT NOT NULL REFERENCES vendors(id),
  decision_reason              TEXT NOT NULL,
  ordered_quantity             NUMERIC NOT NULL DEFAULT 0,
  ordered_quantity_unit        TEXT NOT NULL DEFAULT 'case',
  allocated_elsewhere_cases    NUMERIC NOT NULL DEFAULT 0,
  recipient_item_number        TEXT,
  recipient_description        TEXT NOT NULL,
  quote_eligible               BOOLEAN NOT NULL DEFAULT FALSE,
  quote_available              BOOLEAN NOT NULL DEFAULT FALSE,
  quote_comparable             BOOLEAN NOT NULL DEFAULT FALSE,
  candidate_normalized_net_cost NUMERIC,
  selected_normalized_net_cost NUMERIC,
  normalization_method         TEXT,
  feedback_eligible            BOOLEAN NOT NULL DEFAULT FALSE,
  feedback_omission_reason     TEXT,
  created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (order_id, canonical_product_key, supplier_id)
);

CREATE TABLE IF NOT EXISTS food_order_lines (
  id                    BIGSERIAL PRIMARY KEY,
  order_id              UUID NOT NULL REFERENCES food_orders(id) ON DELETE CASCADE,
  canonical_product_key TEXT NOT NULL,
  item_id               INT REFERENCES items(id),
  supplier_id           INT NOT NULL REFERENCES vendors(id),
  item_name             TEXT NOT NULL,
  supplier_item_number  TEXT,
  description           TEXT NOT NULL DEFAULT '',
  cases_ordered         NUMERIC NOT NULL CHECK (cases_ordered > 0),
  base_cases            NUMERIC NOT NULL DEFAULT 0 CHECK (base_cases >= 0),
  filler_cases          NUMERIC NOT NULL DEFAULT 0 CHECK (filler_cases >= 0),
  unit_price            NUMERIC,
  line_total            NUMERIC,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (order_id, supplier_id, canonical_product_key)
);

CREATE TABLE IF NOT EXISTS order_feedback_sends (
  id                         BIGSERIAL PRIMARY KEY,
  order_id                   UUID NOT NULL REFERENCES food_orders(id) ON DELETE CASCADE,
  supplier_id                INT NOT NULL REFERENCES vendors(id),
  template_version           TEXT NOT NULL,
  idempotency_key            TEXT NOT NULL UNIQUE,
  status                     TEXT NOT NULL
                             CHECK (status IN ('pending', 'dry-run', 'sent', 'failed', 'skipped')),
  intended_recipient         TEXT,
  subject                    TEXT,
  preview_html               TEXT,
  preview_text               TEXT,
  resend_email_id            TEXT,
  last_test_resend_email_id  TEXT,
  item_total                 INT NOT NULL DEFAULT 0 CHECK (item_total >= 0),
  case_total                 NUMERIC NOT NULL DEFAULT 0 CHECK (case_total >= 0),
  attempt_count              INT NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
  omission_summary           JSONB NOT NULL DEFAULT '{}',
  sanitized_error            TEXT,
  created_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  attempted_at               TIMESTAMPTZ,
  sent_at                    TIMESTAMPTZ,
  UNIQUE (order_id, supplier_id, template_version)
);

CREATE INDEX IF NOT EXISTS food_order_decisions_feedback_idx
  ON food_order_decisions (order_id, supplier_id, decision_reason);
CREATE INDEX IF NOT EXISTS order_feedback_sends_status_idx
  ON order_feedback_sends (status, attempted_at);
CREATE INDEX IF NOT EXISTS food_orders_inventory_snapshot_idx
  ON food_orders (inventory_snapshot_id);
CREATE INDEX IF NOT EXISTS food_order_decisions_item_idx
  ON food_order_decisions (item_id);
CREATE INDEX IF NOT EXISTS food_order_decisions_supplier_idx
  ON food_order_decisions (supplier_id);
CREATE INDEX IF NOT EXISTS food_order_decisions_selected_supplier_idx
  ON food_order_decisions (selected_supplier_id);
CREATE INDEX IF NOT EXISTS order_feedback_sends_supplier_idx
  ON order_feedback_sends (supplier_id);
CREATE INDEX IF NOT EXISTS food_order_lines_order_supplier_idx
  ON food_order_lines (order_id, supplier_id);
CREATE INDEX IF NOT EXISTS food_order_lines_item_idx
  ON food_order_lines (item_id);
CREATE INDEX IF NOT EXISTS food_order_lines_supplier_idx
  ON food_order_lines (supplier_id);

-- These tables are server-only.  Explicit grants are required because newer
-- Supabase projects no longer expose new public tables to the Data API by
-- default, while older projects may still have permissive default grants.
ALTER TABLE food_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE food_order_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE food_order_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE order_feedback_sends ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON food_orders, food_order_lines, food_order_decisions, order_feedback_sends
  FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON food_orders, food_order_lines, food_order_decisions, order_feedback_sends
  TO service_role;
REVOKE ALL ON SEQUENCE food_order_lines_id_seq, food_order_decisions_id_seq,
  order_feedback_sends_id_seq FROM anon, authenticated;
GRANT USAGE, SELECT ON SEQUENCE food_order_lines_id_seq TO service_role;
GRANT USAGE, SELECT ON SEQUENCE food_order_decisions_id_seq TO service_role;
GRANT USAGE, SELECT ON SEQUENCE order_feedback_sends_id_seq TO service_role;

-- ITEM MASTER AUDIT STATUS ------------------------------
-- Current vendor-item exceptions live here instead of in CSV exports or
-- dashboard-only constants. Historical numeric observations remain append-only
-- in pricing, grouped by price_lists, so the dashboard can show price changes.
CREATE TABLE IF NOT EXISTS item_vendor_status (
  id                 BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  item_id            INT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  vendor_id          INT NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
  apn                TEXT CHECK (apn IS NULL OR length(trim(apn)) > 0),
  status             TEXT NOT NULL
                     CHECK (status IN (
                       'verified',
                       'product_mismatch',
                       'identity_review',
                       'pending_approval',
                       'special_order',
                       'not_found'
                     )),
  note               TEXT NOT NULL CHECK (length(trim(note)) > 0),
  vendor_item_name   TEXT,
  pack_size          TEXT,
  price_available    BOOLEAN NOT NULL DEFAULT TRUE,
  blocks_ordering    BOOLEAN NOT NULL DEFAULT FALSE,
  verified_on        DATE NOT NULL,
  source             TEXT NOT NULL DEFAULT 'manual_audit',
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (item_id, vendor_id)
);

-- Status-only records may intentionally omit an obsolete vendor SKU.
ALTER TABLE item_vendor_status
  ALTER COLUMN apn DROP NOT NULL;
ALTER TABLE item_vendor_status
  DROP CONSTRAINT IF EXISTS item_vendor_status_apn_check;
ALTER TABLE item_vendor_status
  ADD CONSTRAINT item_vendor_status_apn_check
  CHECK (apn IS NULL OR length(trim(apn)) > 0);
ALTER TABLE item_vendor_status
  DROP CONSTRAINT IF EXISTS item_vendor_status_status_check;
ALTER TABLE item_vendor_status
  ADD CONSTRAINT item_vendor_status_status_check
  CHECK (status IN (
    'verified',
    'product_mismatch',
    'identity_review',
    'pending_approval',
    'special_order',
    'not_found'
  ));

CREATE INDEX IF NOT EXISTS item_vendor_status_apn_idx
  ON item_vendor_status (vendor_id, apn);
CREATE INDEX IF NOT EXISTS item_vendor_status_attention_idx
  ON item_vendor_status (status, verified_on DESC)
  WHERE status <> 'verified';

ALTER TABLE item_vendor_status ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON item_vendor_status FROM anon, authenticated;
GRANT SELECT ON item_vendor_status TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON item_vendor_status TO service_role;
REVOKE ALL ON SEQUENCE item_vendor_status_id_seq FROM anon, authenticated;
GRANT USAGE, SELECT ON SEQUENCE item_vendor_status_id_seq TO service_role;

DROP POLICY IF EXISTS "Public item-master status is readable"
  ON item_vendor_status;
CREATE POLICY "Public item-master status is readable"
  ON item_vendor_status
  FOR SELECT
  TO anon, authenticated
  USING (TRUE);

WITH audit_rows (
  item_name,
  apn,
  status,
  note,
  vendor_item_name,
  pack_size,
  price_available,
  blocks_ordering
) AS (
  VALUES
    (
      'Pecorino Romano Blend',
      NULL,
      'pending_approval',
      'Pending approval while we wait for our US Foods representative to get back to us.',
      NULL,
      '4/5 LB',
      FALSE,
      TRUE
    )
)
INSERT INTO item_vendor_status (
  item_id,
  vendor_id,
  apn,
  status,
  note,
  vendor_item_name,
  pack_size,
  price_available,
  blocks_ordering,
  verified_on,
  source,
  updated_at
)
SELECT
  item.id,
  vendor.id,
  audit_rows.apn,
  audit_rows.status,
  audit_rows.note,
  audit_rows.vendor_item_name,
  audit_rows.pack_size,
  audit_rows.price_available,
  audit_rows.blocks_ordering,
  DATE '2026-08-26',
  'us_foods_audit_2026_08_26',
  NOW()
FROM audit_rows
CROSS JOIN LATERAL (
  SELECT id
  FROM items
  WHERE lower(trim(name)) = lower(audit_rows.item_name)
  ORDER BY id
  LIMIT 1
) AS item
JOIN vendors AS vendor ON vendor.name = 'US FOODS'
ON CONFLICT (item_id, vendor_id) DO UPDATE SET
  apn = EXCLUDED.apn,
  status = EXCLUDED.status,
  note = EXCLUDED.note,
  vendor_item_name = EXCLUDED.vendor_item_name,
  pack_size = EXCLUDED.pack_size,
  price_available = EXCLUDED.price_available,
  blocks_ordering = EXCLUDED.blocks_ordering,
  verified_on = EXCLUDED.verified_on,
  source = EXCLUDED.source,
  updated_at = NOW();

-- Apply the approved item-master decisions from the 2026-08-26 review.
UPDATE items
SET name = 'Chafing Fuel Can 4 Hour'
WHERE lower(trim(name)) = 'chafing fuel can 6 hour';

WITH canonical_limes AS (
  SELECT min(id) AS item_id
  FROM items
  WHERE lower(trim(name)) = 'limes'
)
UPDATE pricing
SET item_id = canonical_limes.item_id
FROM canonical_limes
WHERE vendor_id = 1
  AND apn = '4667994'
  AND pricing.item_id <> canonical_limes.item_id;

DELETE FROM item_vendor_status AS status
USING items AS item
WHERE status.item_id = item.id
  AND status.vendor_id = 1
  AND lower(trim(item.name)) IN ('chafing fuel can 4 hour', 'limes');
