"""The real-HTTP adapter behind the transport seam. This is the ONLY module allowed to import
`requests` (a test enforces it); everything else talks to the `Transport` protocol."""
from __future__ import annotations

from typing import Mapping

import requests

from .transport import PayloadTooLarge, TransportError, TransportResponse

_CHUNK_BYTES = 65536
_trust_store_ready = False


def _use_system_trust_store() -> None:
    """Verify TLS against the operating system's certificate store instead of `requests`' bundled list, once.
    Networks that re-sign HTTPS traffic (Zscaler, corporate proxies) install their root CA in the OS store, so
    `curl` works while a bundle-only Python fails with CERTIFICATE_VERIFY_FAILED. Without `truststore`, or on a
    platform it does not support, the bundled certificates stay in use."""
    global _trust_store_ready
    if _trust_store_ready:
        return
    _trust_store_ready = True
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass


class RequestsTransport:
    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None,
        timeout: float,
        *,
        data: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> TransportResponse:
        _use_system_trust_store()
        try:
            resp = requests.request(
                method, url, headers=dict(headers), params=params, data=data, timeout=timeout, stream=True
            )
            try:
                content_type = resp.headers.get("Content-Type", "")
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_content(chunk_size=_CHUNK_BYTES):
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise PayloadTooLarge(max_bytes)
                    chunks.append(chunk)
                return TransportResponse(resp.status_code, b"".join(chunks), content_type)
            finally:
                resp.close()
        except requests.RequestException as exc:
            # Deliberately drop the message: it embeds the URL, which can carry signed query strings.
            raise TransportError(type(exc).__name__) from None
