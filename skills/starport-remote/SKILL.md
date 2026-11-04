---
name: starport-remote
description: Expose an already working Starport console to a phone through a private VPN or Cloudflare Access and Tunnel, with per-host identity policies and verified origin settings.
---

# Add phone access

Read `docs/REMOTE.md`. First verify the console and Starship RPC locally. Ask only
for the missing access choice: private VPN, or an HTTPS hostname and authorized
identities. Tokens stay in local protected files or official login dialogs.

For VPN access, bind only the chosen private address and allow the intended private
subnet. Preserve existing listeners and SSH paths. Test the phone on its real
network; a host-local test does not establish phone connectivity.

For Cloudflare, open A's management panel → 远程域名. Follow `docs/REMOTE.md`:
install current cloudflared on A, supply the scoped API token, zone, prefix and
explicit allowed emails, then inspect the generated hostname before publishing.
The integrated publication flow creates its own reusable Access policy, app,
tunnel and DNS record. An occupied hostname receives a new suffix. The dedicated
loopback gateway verifies Access JWTs and forwards only console headers to the
configured local console; do not expose the management port as a workaround.

Ask whether the entrance should resume with the management panel and preserve
that choice. Tokens remain on A. Lost acknowledgements require reconciliation
through 核对云端状态; do not delete receipts or repeat raw API creations. Stop or
remove only the owned publication. Existing manually configured tunnels remain
separate. Do not restart the client's Claude service to publish a hostname.

Separate users should get separate console deployments/state and per-host Access
applications. An email allowlist does not create per-session authorization within
one shared console. Do not promise multi-tenant separation from one Tunnel rule.

Verify authorized login, unauthorized denial, exact origin behavior, session read,
one explicitly authorized send, and attachment download. Do not send repeated
messages after an uncertain result; inspect the conversation first. Confirm the
connector's user-session or system-service lifecycle separately from its current
connected state. Report the actual resource and connector state; a running process alone does not
prove public access or authorized/unauthorized login behavior.
