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
