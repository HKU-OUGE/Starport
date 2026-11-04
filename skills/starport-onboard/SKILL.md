---
name: starport-onboard
description: Guide a first Starport deployment across two new computers, choosing roles and operating systems, preparing prerequisites, and validating the first usable session.
---

# First deployment

Work from the Starport checkout. Read `docs/QUICKSTART.md` and `docs/INSTALL.md`.
Use `docs/quickstart/index.html` as the human-facing tutorial.

Establish only missing choices: which computer is A (Starport control host),
which is B (Starship/Claude/projects), each OS, the network path between them,
and whether each role should start after user login. Do not assume both computers
share an OS. The recommended starting pair is a Linux control host and a Mac
client; other console/client OS pairs remain supported.

1. Inventory existing software and active services before changing anything.
   Run `python3 tools/preflight.py server --json` on A and the `client` variant
   on B. In Windows PowerShell use `py -3`. Explain the next action for failed
   required checks. Do not call a prerequisite check a network acceptance test.
2. Follow `docs/NETWORK.md` for a controlled network. If the user requires a fully
   isolated exit and no gateway exists, finish that boundary before Claude login
   or use. Console installation alone does not create a router VM or firewall.
   Offline packages can be staged from A while B is disconnected.
3. Use the Starship skill for B, then the pairing skill. Verify the device host
   fingerprint through a trusted local screen before pinning it. Transfer only
   the dedicated public key; its private key stays on A.
4. On A, run `python3 setup.py server --check`, then the chosen install command.
   Add `--client-platform windows` when B is Windows. With declined login startup,
   use `--no-register` and `python3 tools/run_services.py server`; this does not
   unregister an older installation. Use `--yes` only after the choice is known.
5. Show the locally obtained URLs and access-code retrieval command. Verify SSH
   RPC, client authentication, session listing, one user-authorized test message,
   and a small attachment round trip. Existing Desktop Remote Control and new
   background sessions are different entry points.
6. Verify lock-screen operation and user-login recovery on the actual devices.
   Record remaining manual checks and the exact working entry points. Add phone
   access and compute only when requested; they are not first-session prerequisites.

Use `skills/starport-maintain/SKILL.md` for existing installations. Preserve
projects, account credentials, active tasks and intentional manual pauses.
