import time
from urllib.parse import urlparse

import requests
from .config import (
    ZOHO_API_V3_BASE,
    ZOHO_RESTAPI_BASE,
    REQUEST_TIMEOUT_SECONDS,
    MAX_RETRIES,
    ZOHO_DOWNLOAD_HOST_SUFFIXES,
    ZOHO_DOWNLOAD_HOSTS_EXACT,
)
from .logging_config import get_logger

logger = get_logger(__name__)


def _is_allowed_download_host(url: str) -> bool:
    """The OAuth access token is attached to this request — never send it to a host
    outside Zoho's own domains, regardless of what a fetched attachment record claims
    its download URL is."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    if host in ZOHO_DOWNLOAD_HOSTS_EXACT:
        return True
    return any(host.endswith(suffix) for suffix in ZOHO_DOWNLOAD_HOST_SUFFIXES)


class ZohoClient:
    def __init__(self, auth):
        self.auth = auth

    def _headers(self):
        return {"Authorization": f"Zoho-oauthtoken {self.auth.get_access_token()}"}

    def _request(self, method, url, **kwargs):
        # Hard guard: this server is read-only by design. No write endpoint is ever
        # exposed to a tool, but this makes it structurally impossible regardless —
        # a future addition can't accidentally (or be tricked into) issue a write call.
        if method.upper() != "GET":
            raise RuntimeError(f"Refusing non-GET request ({method} {url}) — this server is read-only.")
        last_error = None
        for attempt in range(MAX_RETRIES):
            logger.debug("%s %s (attempt %d/%d) params=%s", method, url, attempt + 1, MAX_RETRIES, kwargs.get("params"))
            try:
                resp = requests.request(
                    method, url, headers=self._headers(),
                    timeout=REQUEST_TIMEOUT_SECONDS, **kwargs
                )
            except requests.RequestException:
                logger.exception("%s %s raised before a response was received", method, url)
                raise
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning("429 rate limited on %s %s, backing off %ss", method, url, wait)
                time.sleep(wait)
                last_error = f"Rate limited (429), retried {attempt + 1}x"
                continue
            if not resp.ok:
                logger.error("%s %s -> %s: %s", method, url, resp.status_code, resp.text[:500])
            resp.raise_for_status()
            logger.debug("%s %s -> %s", method, url, resp.status_code)
            return resp.json()
        logger.error("%s %s failed after %d retries", method, url, MAX_RETRIES)
        raise RuntimeError(last_error or "Request failed after retries")

    def _get(self, url, params=None):
        return self._request("GET", url, params=params)

    def download_file(self, url, max_bytes=None):
        """Streams a file (e.g. an attachment) and returns (raw_bytes, content_type).
        Enforces max_bytes so one oversized attachment can't stall or blow up a context call."""
        if not _is_allowed_download_host(url):
            raise ValueError(f"refusing to download from disallowed host: {url!r}")
        logger.debug("GET (stream) %s", url)
        resp = requests.get(url, headers=self._headers(), timeout=REQUEST_TIMEOUT_SECONDS, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if max_bytes and total > max_bytes:
                resp.close()
                raise ValueError(f"exceeds {max_bytes}-byte cap (>{total} bytes so far)")
            chunks.append(chunk)
        return b"".join(chunks), content_type

    def _get_v3_or_legacy(self, v3_url, legacy_url, params=None):
        """Zoho's Dec 2025 v3 migration replaces the /restapi/ prefix with /api/v3/
        for most endpoints, but some (milestones, comments, attachments) aren't
        confirmed to have shipped a v3 equivalent yet. Try v3 first; if it 404s,
        fall back to the legacy path rather than failing outright."""
        try:
            return self._get(v3_url, params=params)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404:
                logger.warning("v3 path 404 (%s), falling back to legacy %s", v3_url, legacy_url)
                return self._get(legacy_url, params=params)
            raise

    # --- Stage 2: portal/project ---

    def list_portals(self):
        return self._get(f"{ZOHO_API_V3_BASE}/portals")

    def get_project(self, portal_id, project_id):
        return self._get(f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}")

    def list_projects(self, portal_id):
        return self._get_v3_or_legacy(
            f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects",
            f"{ZOHO_RESTAPI_BASE}/portal/{portal_id}/projects/",
        )

    def search(self, portal_id, project_id, search_term, module):
        """Resolves a human-readable key (e.g. 'AD1-T1153') to Zoho's internal numeric
        ID. No confirmed v3 equivalent per Zoho's docs, so this only has the legacy
        path — requires the ZohoProjects.search.READ scope."""
        return self._get(
            f"{ZOHO_RESTAPI_BASE}/portal/{portal_id}/projects/{project_id}/search",
            params={"search_term": search_term, "module": module},
        )

    # --- Enrichment ---

    def list_users(self, portal_id):
        return self._get(f"{ZOHO_API_V3_BASE}/portal/{portal_id}/users")

    def list_tags(self, portal_id):
        return self._get(f"{ZOHO_API_V3_BASE}/portal/{portal_id}/tags")

    # --- Tasklists ---

    def get_project_tasklists(self, portal_id, project_id):
        return self._get(f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/tasklists")

    def get_tasklist(self, portal_id, project_id, tasklist_id):
        return self._get(f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/tasklists/{tasklist_id}")

    # --- Milestones (v3 path unconfirmed per §0; tries v3, falls back to legacy) ---

    def list_milestones(self, portal_id, project_id):
        return self._get_v3_or_legacy(
            f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/milestones",
            f"{ZOHO_RESTAPI_BASE}/portal/{portal_id}/projects/{project_id}/milestones/",
        )

    def get_milestone(self, portal_id, project_id, milestone_id):
        return self._get_v3_or_legacy(
            f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/milestones/{milestone_id}",
            f"{ZOHO_RESTAPI_BASE}/portal/{portal_id}/projects/{project_id}/milestones/{milestone_id}/",
        )

    # --- Tasks ---

    def get_tasks(self, portal_id, project_id, filter_json=None, extra_params=None):
        params = dict(extra_params or {})
        if filter_json:
            params["filter"] = filter_json
        return self._get(f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/tasks", params=params or None)

    def get_task(self, portal_id, project_id, task_id):
        return self._get(f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/tasks/{task_id}")

    def get_task_comments(self, portal_id, project_id, task_id):
        return self._get_v3_or_legacy(
            f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/tasks/{task_id}/comments",
            f"{ZOHO_RESTAPI_BASE}/portal/{portal_id}/projects/{project_id}/tasks/{task_id}/comments/",
        )

    def get_task_attachments(self, portal_id, project_id, task_id):
        return self._get_v3_or_legacy(
            f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/tasks/{task_id}/attachments",
            f"{ZOHO_RESTAPI_BASE}/portal/{portal_id}/projects/{project_id}/tasks/{task_id}/attachments/",
        )

    def get_task_status_history(self, portal_id, project_id, task_id=None):
        url = f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/taskstatushistory"
        params = {"task_id": task_id} if task_id else None
        return self._get(url, params=params)

    # --- Bugs ---

    def get_bug(self, portal_id, project_id, bug_id):
        return self._get(f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/bugs/{bug_id}")

    def get_bug_comments(self, portal_id, project_id, bug_id):
        return self._get_v3_or_legacy(
            f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/bugs/{bug_id}/comments",
            f"{ZOHO_RESTAPI_BASE}/portal/{portal_id}/projects/{project_id}/bugs/{bug_id}/comments/",
        )

    def get_bug_attachments(self, portal_id, project_id, bug_id):
        return self._get_v3_or_legacy(
            f"{ZOHO_API_V3_BASE}/portal/{portal_id}/projects/{project_id}/bugs/{bug_id}/attachments",
            f"{ZOHO_RESTAPI_BASE}/portal/{portal_id}/projects/{project_id}/bugs/{bug_id}/attachments/",
        )
