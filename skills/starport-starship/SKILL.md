---
name: starport-starship
description: Install and validate the Starship client on macOS, Windows or Linux, including Claude executable discovery, local account login, SSH access and background services.
---

# Set up the client device

Locate the checkout; read `client/README.md`, `docs/INSTALL.md`, and, for a Mac,
`docs/MAC.md`. This skill configures B, the device that owns Claude and projects.

- Run `python3 tools/preflight.py client --json` (Windows: `py -3`). Install only
  missing prerequisites. Use the official Claude installer linked in the guide;
  let the user complete account authentication locally after network acceptance.
- `python3 setup.py client --check` checks executable discovery and an inactive
  installation. An active-service warning is not permission to stop it. Finish
  or reconcile existing work before an update.
- Ask once whether to register login startup if the choice is unknown. Install
  normally for login startup, or pass `--no-register` for manual operation.
  `python3 tools/run_services.py client` starts the manual group in one terminal.
- The installer saves the chosen Claude launcher in a private `runner.json` and
  discovery also checks the native user installation. This handles a fresh Mac's
  limited launchd PATH. Keep a stable launcher path so native updates still work.
- Enable only required OS services: Remote Login/SSH for pairing. Screen Recording,
  Accessibility, VNC, SMB and Moonlight are optional, separate capabilities; they
  are not required for the normal session API. Do not grant Full Disk Access merely
  to make a generic test pass. If a needed transcript directory is denied, inspect
  the exact denial and have the user grant the narrow appropriate OS permission.
- Test `python3 starship.py status`. Distinguish a working RPC service from a
  logged-in account and from a Desktop session with Remote Control enabled.
- Use a real project folder inside the device user's home. Lock the screen and
  recheck an authorized background/Remote Control session. Locking preserves a
  login session; sleeping, logging out and pre-login boot are different states.

Do not copy the control host's environment, SSH agent, browser profile or cloud
credentials onto B. The client itself is not a VM or a filesystem sandbox.
