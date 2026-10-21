---
description: Sign in to Zoho Projects in your browser
allowed-tools: Bash(zoho-projects-axi auth login:*), Bash(zoho-projects-axi auth status:*)
---

Sign the user in to Zoho Projects.

1. Run `zoho-projects-axi auth login` with the Bash tool and a timeout of 300000 ms. It opens Zoho's consent page in the
   user's browser, waits up to five minutes for them to approve, and saves the token itself. Tell the user to
   approve the request in the browser tab that opens. If the output shows a URL and no tab opened, give them the URL.
2. Run `zoho-projects-axi auth status` and report whether the token is `valid`.
3. Never ask the user to paste secrets, codes or tokens into the chat.
4. If login says no Zoho client is configured, run `zoho-projects-axi auth login-help` and relay the steps.
