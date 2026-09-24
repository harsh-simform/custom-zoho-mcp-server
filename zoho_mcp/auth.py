import time
import requests
from .config import ZOHO_ACCOUNTS_BASE, REQUEST_TIMEOUT_SECONDS
from .logging_config import get_logger

logger = get_logger(__name__)


class ZohoAuthError(Exception):
    pass


class ZohoAuth:
    """Holds OAuth credentials and produces a valid access token on demand."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._access_token = None
        self._expires_at = 0.0

    def _refresh(self):
        logger.info("Refreshing Zoho access token (client_id=%s)", self.client_id)
        resp = requests.post(
            f"{ZOHO_ACCOUNTS_BASE}/oauth/v2/token",
            data={
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        data = resp.json()
        if "access_token" not in data:
            # Zoho's error payload doesn't echo secrets back, so this is safe to log as-is.
            logger.error("OAuth refresh failed: %s", data)
            raise ZohoAuthError(f"OAuth refresh failed: {data}")
        self._access_token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 3600) - 60
        logger.info("Access token refreshed, expires in %ss", data.get("expires_in", 3600))

    def get_access_token(self) -> str:
        if not self._access_token or time.time() >= self._expires_at:
            self._refresh()
        return self._access_token

    def validate(self) -> bool:
        self._refresh()
        return True

    def to_dict(self) -> dict:
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ZohoAuth":
        return cls(data["client_id"], data["client_secret"], data["refresh_token"])
