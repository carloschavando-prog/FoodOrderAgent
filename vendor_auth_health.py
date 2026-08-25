"""Read-only authentication health checks for broadline connectors.

These checks authenticate and read a vendor order guide/list. They never add
items to carts, submit orders, or write pricing data.
"""

from __future__ import annotations

import argparse
import os

import scrape_gfs
import scrape_pfg
import scrape_sysco
import scrape_usfoods


class HealthCheckError(RuntimeError):
    """A vendor connector failed its read-only health check."""


def check_sysco():
    email = os.environ.get("SYSCO_EMAIL", "").strip()
    password = os.environ.get("SYSCO_PASSWORD", "")
    if not email or not password:
        raise HealthCheckError(
            "SYSCO_EMAIL and SYSCO_PASSWORD must both be configured."
        )

    # Deliberately bypass SYSCO_COOKIES. This proves the stored email/password
    # still work instead of merely validating last week's browser session.
    bearer, shop_account_id, csrf_token, vid = scrape_sysco.get_bearer_token(
        email,
        password,
        allow_cookies=False,
    )
    context = {
        "csrf_token": csrf_token,
        "vid": vid,
        "shop_account_id": shop_account_id,
        "site_id": scrape_sysco.SITE_ID,
    }
    products = scrape_sysco.fetch_order_guide(bearer, context)
    if not products:
        raise HealthCheckError(
            "Sysco authenticated but the configured order guide was empty."
        )
    print(f"✅ Sysco password login and order-guide access verified ({len(products)} items)")


def check_usf():
    config = scrape_usfoods.load_config()
    bearer = scrape_usfoods.authenticate(config)
    list_id = config.get("fall_2025_list_id", 1000643297)
    product_numbers = scrape_usfoods.get_list_items(bearer, list_id)
    if not product_numbers:
        raise HealthCheckError(
            "US Foods authenticated but the configured ordering list was empty."
        )
    print(
        "✅ US Foods authentication and ordering-list access verified "
        f"({len(product_numbers)} items)"
    )


def check_pfg():
    config = scrape_pfg.load_config()
    bearer = scrape_pfg.refresh_token(config, persist=False)
    customer_id = config.get("customer_id", "ccbddeae-bc43-4287-a4e0-8d5bee2b913c")
    list_id = config.get("fall_list_id", "13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60")
    products = scrape_pfg.get_products(bearer, customer_id, list_id)
    if not products:
        raise HealthCheckError(
            "PFG authenticated but the configured product list was empty."
        )
    scrape_pfg.save_config(config)
    print(f"✅ PFG authentication and product-list access verified ({len(products)} items)")


def check_gfs():
    cookies = scrape_gfs.load_cookies()
    guide = scrape_gfs.gfs_get("v6/lists/order-guide", cookies)
    materials = {
        material
        for category in guide.get("guideCategories", [])
        for material in category.get("materialNumbers", [])
    }
    if not materials:
        raise HealthCheckError(
            "GFS authenticated but the configured order guide was empty."
        )
    print(f"✅ GFS authentication and order-guide access verified ({len(materials)} items)")


CHECKS = {
    "gfs": check_gfs,
    "pfg": check_pfg,
    "sysco": check_sysco,
    "usf": check_usf,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vendor", choices=sorted(CHECKS))
    args = parser.parse_args(argv)
    CHECKS[args.vendor]()


if __name__ == "__main__":
    main()
