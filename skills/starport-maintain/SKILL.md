---
name: starport-maintain
description: Diagnose, update or uninstall Starport and Starship without losing active sessions, project files, credentials or unrelated services; includes safe rollback and cleanup scoping.
---

# Maintain an installation

Read `docs/MAINTENANCE.md` and use the operation the user requested. Start with
`tools/preflight.py`, current service status, exact error and current version.
Do not treat a stale UI label or nonzero historical reconnect counter as an outage.

For diagnosis, separate local web service, SSH, Starship RPC, Claude authentication,
Remote Control and controlled Internet egress. Retry read-only probes with bounds.
Do not bypass host-key checks or firewall guards, and do not resubmit a task when
its previous submission outcome is unknown.

For update, inspect uncommitted work and active jobs. Back up changed deployment
configuration outside Git. Validate the new version before switching. Stop only
the selected idle service group; Starship deliberately refuses a live update.
Keep the chosen login-start setting and recheck pairing/session/file flows. Do
not rewrite a user's branch or reset local configuration to update the product.

For uninstall, establish server/client/both and whether data should be retained.
Default to keeping credentials, projects, session history and configuration.
Show the exact owned services and paths, then perform authorized removal using
the OS-specific commands in the guide. Removing a console is not permission to
stop a router or a remote training task. Finish/reconcile managed sessions before
stopping their client. Delete state only after explicit scoped approval.

For space cleanup, inventory specific temporary candidates, ownership, active
transfers and verified final copies. Request deletion approval if not already
given for those exact targets. Never replace this with an age-only recursive
delete of all state. Report actual reclaimed bytes and what remains accessible.
