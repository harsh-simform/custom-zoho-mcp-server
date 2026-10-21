"""The OAuth client that ships with zoho-projects-axi, so `zoho-projects-axi auth login` works without registering one.

Zoho has no dynamic client registration, so the project registers ONE "Server-based Applications" client in
the Zoho API console (with "Use the same OAuth credentials for multiple data centers" on and the redirect URI
http://localhost:8765/callback) and puts its id and secret here. With PKCE and a loopback redirect, a desktop
CLI cannot keep this secret confidential; what protects a user is their own consent, so the client asks for
read scopes only. Anyone who prefers their own client sets client_id and client_secret in the config file or
ZOHO_PROJECTS_AXI_CLIENT_ID / ZOHO_PROJECTS_AXI_CLIENT_SECRET, which win over this.

Empty means "none shipped": login then asks for a client to be configured.
"""

CLIENT_ID = ""
CLIENT_SECRET = ""
