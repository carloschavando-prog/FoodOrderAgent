"""Shared, concurrency-safe storage for rotating vendor credentials.

Vercel functions use the Supabase service role directly.  GitHub Actions uses
the narrow vendor-auth bridge so the database service-role key never has to be
copied into CI.  A short database lease prevents two refreshes from consuming
the same one-time refresh token.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass


DEFAULT_SUPABASE_URL = "https://gnkwdoohzspomvdshzge.supabase.co"
DEFAULT_BRIDGE_URL = (
    "https://food-order-agent-psi.vercel.app/api/vendor_auth_bridge"
)


class VendorAuthError(RuntimeError):
    """A safe, actionable error from the credential store."""


def _http_json(url, payload, headers, timeout=20):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")[:500]
        raise VendorAuthError(
            f"Vendor credential service rejected the request (HTTP {ex.code}): "
            f"{body or ex.reason}"
        ) from ex
    except urllib.error.URLError as ex:
        raise VendorAuthError(
            f"Vendor credential service is unavailable: {ex.reason}"
        ) from ex


@dataclass
class CredentialLease:
    """Credentials claimed for one refresh-token exchange."""

    client: "VendorAuthClient"
    vendor_id: int
    owner: str
    credentials: dict
    finished: bool = False

    def commit(self, credentials, *, verified=True):
        if self.finished:
            raise VendorAuthError("Vendor credential lease was already completed")
        self.client.commit(
            self.vendor_id,
            self.owner,
            credentials,
            verified=verified,
        )
        self.finished = True

    def fail(self, error):
        if self.finished:
            return
        try:
            self.client.fail(self.vendor_id, self.owner, str(error)[:500])
        finally:
            self.finished = True


class VendorAuthClient:
    """Client for the direct Supabase RPCs or the restricted CI bridge."""

    def __init__(
        self,
        *,
        supabase_url="",
        service_key="",
        bridge_url="",
        bridge_secret="",
    ):
        self.supabase_url = (supabase_url or DEFAULT_SUPABASE_URL).rstrip("/")
        self.service_key = service_key.strip()
        self.bridge_url = (bridge_url or DEFAULT_BRIDGE_URL).strip()
        self.bridge_secret = bridge_secret.strip()
        if not self.service_key and not self.bridge_secret:
            raise VendorAuthError(
                "Vendor authentication is not configured on the server"
            )

    @classmethod
    def from_env(cls, *, direct=False):
        service_key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
        if direct and not service_key:
            raise VendorAuthError("SUPABASE_SERVICE_KEY is required")
        return cls(
            supabase_url=os.getenv("SUPABASE_URL", DEFAULT_SUPABASE_URL),
            service_key=service_key,
            bridge_url=os.getenv("VENDOR_AUTH_BRIDGE_URL", DEFAULT_BRIDGE_URL),
            bridge_secret="" if direct else os.getenv(
                "VENDOR_AUTH_BRIDGE_SECRET", ""
            ),
        )

    @property
    def is_direct(self):
        return bool(self.service_key)

    def _direct_headers(self):
        if not self.service_key:
            raise VendorAuthError("SUPABASE_SERVICE_KEY is required")
        return {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
        }

    def _rpc(self, function_name, payload):
        return _http_json(
            f"{self.supabase_url}/rest/v1/rpc/{function_name}",
            payload,
            self._direct_headers(),
        )

    def _bridge(self, action, payload):
        if not self.bridge_secret:
            raise VendorAuthError("VENDOR_AUTH_BRIDGE_SECRET is required")
        result = _http_json(
            self.bridge_url,
            {"action": action, **payload},
            {"Authorization": f"Bearer {self.bridge_secret}"},
            timeout=30,
        )
        if not isinstance(result, dict) or not result.get("success"):
            message = result.get("error") if isinstance(result, dict) else ""
            raise VendorAuthError(message or "Vendor credential bridge failed")
        return result

    def claim(
        self,
        vendor_id,
        *,
        owner=None,
        lease_seconds=45,
        wait_seconds=20,
    ):
        owner = owner or str(uuid.uuid4())
        deadline = time.monotonic() + wait_seconds
        while True:
            if self.is_direct:
                rows = self._rpc(
                    "claim_vendor_auth",
                    {
                        "p_vendor_id": int(vendor_id),
                        "p_owner": owner,
                        "p_lease_seconds": int(lease_seconds),
                    },
                ) or []
                credentials = rows[0].get("credentials") if rows else None
            else:
                try:
                    result = self._bridge(
                        "claim",
                        {
                            "vendorId": int(vendor_id),
                            "owner": owner,
                            "leaseSeconds": int(lease_seconds),
                        },
                    )
                    credentials = result.get("credentials")
                except VendorAuthError as ex:
                    if (
                        "busy" not in str(ex).lower()
                        or time.monotonic() >= deadline
                    ):
                        raise
                    credentials = None

            if isinstance(credentials, dict):
                return CredentialLease(
                    self, int(vendor_id), owner, dict(credentials)
                )
            if time.monotonic() >= deadline:
                raise VendorAuthError(
                    "Vendor sign-on is busy or has not been initialized; try again"
                )
            time.sleep(0.2 + random.random() * 0.15)

    def commit(self, vendor_id, owner, credentials, *, verified=True):
        if not isinstance(credentials, dict) or not credentials:
            raise VendorAuthError("Refusing to save empty vendor credentials")
        if self.is_direct:
            saved = None
            last_error = None
            for attempt in range(3):
                try:
                    saved = self._rpc(
                        "complete_vendor_auth",
                        {
                            "p_vendor_id": int(vendor_id),
                            "p_owner": owner,
                            "p_credentials": credentials,
                            "p_verified": bool(verified),
                        },
                    )
                    break
                except VendorAuthError as ex:
                    last_error = ex
                    if attempt < 2:
                        time.sleep(0.15 * (attempt + 1))
            if saved is None and last_error is not None:
                raise last_error
            if saved is not True:
                raise VendorAuthError(
                    "Vendor sign-on changed during refresh; no order was submitted"
                )
            return
        self._bridge(
            "commit",
            {
                "vendorId": int(vendor_id),
                "owner": owner,
                "credentials": credentials,
                "verified": bool(verified),
            },
        )

    def fail(self, vendor_id, owner, error):
        if self.is_direct:
            self._rpc(
                "fail_vendor_auth",
                {
                    "p_vendor_id": int(vendor_id),
                    "p_owner": owner,
                    "p_error": str(error)[:500],
                },
            )
            return
        self._bridge(
            "fail",
            {
                "vendorId": int(vendor_id),
                "owner": owner,
                "error": str(error)[:500],
            },
        )

    def replace(self, vendor_id, credentials):
        """Authenticated bootstrap/synchronization for a vendor credential row."""
        if not isinstance(credentials, dict) or not credentials:
            raise VendorAuthError("Refusing to save empty vendor credentials")
        if not self.is_direct:
            self._bridge(
                "replace",
                {"vendorId": int(vendor_id), "credentials": credentials},
            )
            return
        headers = {
            **self._direct_headers(),
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
        _http_json(
            f"{self.supabase_url}/rest/v1/vendor_auth?on_conflict=vendor_id",
            {
                "vendor_id": int(vendor_id),
                "credentials": credentials,
                "lease_owner": None,
                "lease_expires_at": None,
                "last_error": None,
            },
            headers,
        )
