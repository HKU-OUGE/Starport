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

For Cloudflare, use current official Tunnel/Access tooling on A. Configure the
exact hostname as `--public-origin`, route the tunnel to `http://127.0.0.1:18883`,
and create a self-hosted Access application for that hostname with an explicit
email/group allow policy. Preserve the public Host header. Do not put B or the
management panel behind an unauthenticated public port. The existing console
also requires its access code and CSRF checks.

Separate users should get separate console deployments/state and per-host Access
applications. An email allowlist does not create per-session authorization within
one shared console. Do not promise multi-tenant separation from one Tunnel rule.

Verify authorized login, unauthorized denial, exact origin behavior, session read,
one explicitly authorized send, and attachment download. Do not send repeated
messages after an uncertain result; inspect the conversation first. Confirm the
connector's user-session or system-service lifecycle separately from its current
connected state. This repository does not automatically create Cloudflare account
resources from a token, so use the real external tool and report its outcome.
