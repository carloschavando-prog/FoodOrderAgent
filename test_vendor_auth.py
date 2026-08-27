import unittest
from unittest import mock

from api import vendor_auth


class VendorAuthClientTests(unittest.TestCase):
    def test_claim_waits_for_existing_refresh_and_returns_latest_credentials(self):
        client = vendor_auth.VendorAuthClient(
            supabase_url="https://example.supabase.co",
            service_key="service-key",
        )
        with mock.patch.object(
            client,
            "_rpc",
            side_effect=[[], [{"credentials": {"refresh_token": "latest"}}]],
        ) as rpc, mock.patch.object(vendor_auth.time, "sleep"):
            lease = client.claim(1, owner="owner-1", wait_seconds=1)

        self.assertEqual("latest", lease.credentials["refresh_token"])
        self.assertEqual(2, rpc.call_count)
        self.assertEqual("owner-1", lease.owner)

    def test_commit_is_required_before_lease_finishes(self):
        client = mock.Mock()
        lease = vendor_auth.CredentialLease(
            client, 2, "owner-2", {"refresh_token": "old"}
        )
        latest = {"refresh_token": "new"}

        lease.commit(latest)

        client.commit.assert_called_once_with(
            2, "owner-2", latest, verified=True
        )
        self.assertTrue(lease.finished)
        with self.assertRaises(vendor_auth.VendorAuthError):
            lease.commit(latest)

    def test_failed_refresh_releases_lease_without_overwriting_credentials(self):
        client = mock.Mock()
        lease = vendor_auth.CredentialLease(
            client, 1, "owner-3", {"refresh_token": "still-current"}
        )

        lease.fail("vendor unavailable")

        client.fail.assert_called_once_with(1, "owner-3", "vendor unavailable")
        client.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
