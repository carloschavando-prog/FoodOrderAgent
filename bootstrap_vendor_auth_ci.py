"""Copy one CI-held bootstrap credential into the shared vendor-auth store."""

import json
import os

from api.vendor_auth import VendorAuthClient


VENDORS = {
    "usfoods": (1, "USF_CONFIG", "USF_REFRESH_TOKEN", "US Foods"),
    "pfg": (2, "PFG_CONFIG", "PFG_REFRESH_TOKEN", "PFG"),
}


def main():
    selected = os.getenv("BOOTSTRAP_VENDOR", "").strip().lower()
    if selected not in VENDORS:
        raise SystemExit("BOOTSTRAP_VENDOR must be usfoods or pfg")
    vendor_id, config_name, token_name, label = VENDORS[selected]
    config = json.loads(os.environ[config_name])
    token = os.environ.get(token_name, "").strip()
    if not token:
        raise SystemExit(f"{token_name} is empty")
    config["refresh_token"] = token
    VendorAuthClient.from_env().replace(vendor_id, config)
    print(f"✅ {label} shared sign-on initialized")


if __name__ == "__main__":
    main()
