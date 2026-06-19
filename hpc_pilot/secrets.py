"""Secrets management -- in-memory cache with optional Vault backend."""
from __future__ import annotations

import os
import time
from typing import Any


class SecretsManager:
    """Lazy-fetch, in-memory-cached secrets loader.

    Defaults to ``env`` backend (reads from ``os.environ``).  Optionally
    supports a ``vault`` backend that lazy-imports ``hvac`` and fetches from
    HashiCorp Vault.

    Values are cached in-memory for 1 hour.  Secrets are never written to
    disk.
    """

    _CACHE_TTL: float = 3600.0  # 1 hour

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, float]] = {}  # key -> (value, expiry_ts)
        self._backend: str = "env"
        self._vault_client: Any = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        """Get a secret.  Checks env first, then in-memory cache, then backend.

        Args:
            key: Secret name (case-insensitive; stored uppercase).

        Returns:
            The secret value, or ``None`` if not found.
        """
        normalized = key.upper()

        # 1. Check env (fast path)
        env_val = os.environ.get(normalized)
        if env_val is not None:
            return env_val

        # 2. Check in-memory cache (1-hour TTL)
        cached = self._cache.get(normalized)
        if cached is not None:
            value, expiry = cached
            if time.time() < expiry:
                return value
            else:
                # Expired entry -- remove it so the next lookup goes to backend
                del self._cache[normalized]

        # 3. Try backend (currently only Vault)
        if self._backend == "vault":
            return self._fetch_vault(normalized)

        return None

    def configure_from_config(self, config: dict[str, Any]) -> None:
        """Configure the secrets manager from a configuration dict.

        Expected keys under ``secrets``::

            secrets:
              backend: vault          # "env" | "vault"
              vault_addr: https://vault.example.com:8200
              vault_token: <token>

        When ``backend`` is ``"vault"``, the Vault client is initialised
        (lazily -- hvac is imported only on first ``get()``).
        """
        secrets_cfg = config.get("secrets", {}) or {}
        backend: str = str(secrets_cfg.get("backend", "env"))
        self._backend = backend

        if backend == "vault":
            addr = str(secrets_cfg.get("vault_addr", ""))
            token = str(secrets_cfg.get("vault_token", ""))
            if addr and token:
                self._init_vault(addr, token)

    def clear_cache(self) -> None:
        """Clear the in-memory cache (useful during testing)."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Vault support
    # ------------------------------------------------------------------

    def _init_vault(self, addr: str, token: str) -> None:
        """Lazy-init the Vault client.

        The ``hvac`` library is imported only when this method is called,
        so it remains an optional dependency.
        """
        try:
            import hvac
        except ImportError:
            raise ImportError(
                "hvac is required for the Vault backend. "
                "Install with: pip install hvac"
            ) from None
        self._vault_client = hvac.Client(url=addr, token=token)

    def _fetch_vault(self, key: str) -> str | None:
        """Fetch a secret from Vault and cache it for 1 hour.

        Args:
            key: The upper-cased secret key (e.g. ``ANTHROPIC_API_KEY``).

        Returns:
            The secret value, or ``None`` if not found in Vault.
        """
        if self._vault_client is None:
            return None

        try:
            # Strip suffix that some users include in env-var names
            secret_key = key.lower()
            # Try to read from the KV v2 engine at the default mount path
            response = self._vault_client.secrets.kv.v2.read_secret_version(
                path=secret_key, mount_point="secret"
            )
            data: dict[str, Any] = response.get("data", {})
            raw_value: str | None = None
            for candidate in (key, key.lower(), key.upper()):
                val = data.get(candidate)
                if val is not None and isinstance(val, str):
                    raw_value = val
                    break

            if raw_value is not None:
                self._cache[key] = (raw_value, time.time() + self._CACHE_TTL)
                return raw_value
        except Exception:
            # Vault errors must not break the application
            pass

        return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_secrets_manager: SecretsManager | None = None


def get_secrets_manager() -> SecretsManager:
    """Return the module-level SecretsManager singleton."""
    global _secrets_manager
    if _secrets_manager is None:
        _secrets_manager = SecretsManager()
    return _secrets_manager


def configure_secrets(config: dict[str, Any] | None = None) -> None:
    """Configure the global secrets manager from ``config.yaml`` contents.

    Call once during application startup.
    """
    mgr = get_secrets_manager()
    if config:
        mgr.configure_from_config(config)
