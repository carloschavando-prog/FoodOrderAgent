"""Minimal, log-safe GitHub repository secret promotion."""

from __future__ import annotations

import os
import re
import subprocess


_SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SecretPromotionError(RuntimeError):
    """A secret could not be promoted without exposing its value."""


def set_repository_secret(
    name,
    value,
    repository,
    *,
    token=None,
    runner=subprocess.run,
):
    """Send a repository secret through stdin, never argv or logs."""
    if not _SECRET_NAME.fullmatch(name or ""):
        raise SecretPromotionError("Invalid GitHub secret name.")
    if not repository or "/" not in repository:
        raise SecretPromotionError("A GitHub owner/repository is required.")
    if not value:
        raise SecretPromotionError(f"Refusing to promote an empty {name} secret.")

    env = os.environ.copy()
    if token:
        env["GH_TOKEN"] = token
    result = runner(
        ["gh", "secret", "set", name, "--repo", repository],
        input=value,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise SecretPromotionError(f"Failed to update the {name} GitHub secret.")

