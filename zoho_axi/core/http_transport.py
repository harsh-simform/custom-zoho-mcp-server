"""The real-HTTP adapter behind the transport seam. This is the ONLY module allowed to import
`requests` (a test enforces it); everything else talks to the `Transport` protocol."""
from __future__ import annotations

from typing import Mapping

import requests

from .transport import PayloadTooLarge, TransportError, TransportResponse

_CHUNK_BYTES = 65536


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
