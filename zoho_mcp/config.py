import os
from pathlib import Path

from dotenv import load_dotenv

# Anchored to the repo root (not cwd) so secrets load the same way regardless of the
# directory the server was launched from (matters for a globally-registered server,
# since Claude can spawn it from any project's directory).
_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = os.environ.get("ZOHO_MCP_ENV_FILE", str(_REPO_ROOT / ".env"))
load_dotenv(_ENV_FILE, override=False)

# OAuth secrets — set these in .env (see .env.example), never hardcode them. Nothing
# in this server persists secrets to disk. Real environment variables (e.g. set
# directly in an MCP server's `env` block) take priority over .env, since
# load_dotenv(override=False) won't clobber an already-set variable.
ZOHO_CLIENT_ID = os.environ.get("ZOHO_CLIENT_ID")
ZOHO_CLIENT_SECRET = os.environ.get("ZOHO_CLIENT_SECRET")
ZOHO_REFRESH_TOKEN = os.environ.get("ZOHO_REFRESH_TOKEN")

# Optional: default portal/project, auto-selected at startup so
# select_portal_and_project doesn't need to be called manually every session.
ZOHO_PORTAL_ID = os.environ.get("ZOHO_PORTAL_ID")
ZOHO_PROJECT_ID = os.environ.get("ZOHO_PROJECT_ID")

# Zoho data center — change based on your account's region: com, eu, in, com.au, jp
ZOHO_DC = os.environ.get("ZOHO_DC", "com")

ZOHO_ACCOUNTS_BASE = f"https://accounts.zoho.{ZOHO_DC}"
# Verified: https://projectsapi.zoho.<dc>/api/v3, header Authorization: Zoho-oauthtoken {token}
ZOHO_API_V3_BASE = f"https://projectsapi.zoho.{ZOHO_DC}/api/v3"
# Legacy — deprecated per Zoho's Dec 31 2025 notice. client.py tries the v3 mirror path
# first (milestones/comments/attachments) and falls back to this on 404, since Zoho's
# public docs still show only the legacy path for milestones as of this writing.
ZOHO_RESTAPI_BASE = f"https://projectsapi.zoho.{ZOHO_DC}/restapi"

# Hosts a downloaded attachment URL is allowed to resolve to. The OAuth access token
# is sent as an Authorization header on that request (see client.py:download_file) —
# without this allowlist, a task/bug attachment record whose download-url-shaped field
# pointed off-domain would leak the live token to an arbitrary host.
ZOHO_DOWNLOAD_HOST_SUFFIXES = (f".zoho.{ZOHO_DC}", f".zohopublic.{ZOHO_DC}")
ZOHO_DOWNLOAD_HOSTS_EXACT = (f"zoho.{ZOHO_DC}", f"zohopublic.{ZOHO_DC}")

REQUEST_TIMEOUT_SECONDS = 20
MAX_RETRIES = 3

# Image attachments on a task/bug are downloaded and returned inline (not just as
# filenames) so Claude can visually inspect screenshots in the same context-fetch call.
MAX_CONTEXT_IMAGES = int(os.environ.get("ZOHO_MCP_MAX_CONTEXT_IMAGES", "5"))
MAX_ATTACHMENT_BYTES = int(os.environ.get("ZOHO_MCP_MAX_ATTACHMENT_BYTES", str(8 * 1024 * 1024)))

LOG_LEVEL = os.environ.get("ZOHO_MCP_LOG_LEVEL", "INFO")
LOG_FILE = os.environ.get("ZOHO_MCP_LOG_FILE")  # optional; stderr is always on
