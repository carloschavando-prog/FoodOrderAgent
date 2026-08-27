"""Keep rotating vendor refresh grants alive without reading or placing orders."""

import sys

from api import place_order_pfg as pfg
from api import place_order_usfoods as usfoods
from api.vendor_auth import VendorAuthClient


VENDORS = {
    "usfoods": (1, "US Foods"),
    "pfg": (2, "PFG"),
}


def refresh_vendor(selected):
    if selected not in VENDORS:
        raise ValueError("Vendor must be usfoods or pfg")
    vendor_id, label = VENDORS[selected]
    lease = VendorAuthClient.from_env().claim(vendor_id)
    config = lease.credentials
    try:
        if selected == "usfoods":
            usfoods.refresh_bearer(config, persist=False)
        else:
            pfg.refresh_bearer(config, persist=False)
        lease.commit(config, verified=True)
    except Exception as ex:
        lease.fail(ex)
        raise
    print(f"✅ {label} shared sign-on refreshed")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 refresh_vendor_auth_ci.py usfoods|pfg")
    refresh_vendor(sys.argv[1].strip().lower())


if __name__ == "__main__":
    main()
