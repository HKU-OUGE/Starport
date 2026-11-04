# Work on Starport

Starport is the control host; Starship runs on the device with Claude and project
files. Read `README.md` and use the current installers and CLI help as the source
of truth. Keep deployment configuration and generated runtime state outside Git.

For a user setting up or operating an installation, select the relevant skill:

| Request | Skill |
| --- | --- |
| Two new computers / first installation | `skills/starport-onboard/SKILL.md` |
| Client setup, especially a fresh Mac | `skills/starport-starship/SKILL.md` |
| SSH keys, fingerprints, pairing | `skills/starport-pair/SKILL.md` |
| Router, subscriptions, restricted exit | `skills/starport-network/SKILL.md` |
| Phone access / Cloudflare Tunnel and Access | `skills/starport-remote/SKILL.md` |
| Existing isolated compute nodes | `skills/starport-compute/SKILL.md` |
| Diagnosis, safe update, uninstall | `skills/starport-maintain/SKILL.md` |

When a skill is read outside this checkout, locate the Starport repository first;
all commands and `docs/…` references in the skills are relative to that root.
Read only the selected workflow and its relevant references.

For installation, establish the two device roles, operating systems, network
path, and whether the user wants login startup. Reuse choices already provided.
Use `--no-register` when login startup is declined; `--no-start` still registers
startup and only defers the immediate launch. Do not infer consent to replace
firewalls, reboot machines, stop active sessions, delete data or create paid
resources from a request to install the console. Apply already authorized steps
without repeatedly asking. Account login and OS permission dialogs stay on the
user's own device; do not request secrets in chat.

For development, use temporary test homes and fixture services. Do not run the
installers against a live home just to test them. Keep platform-specific changes
in the shared lifecycle helpers or corresponding installer, and test Linux,
macOS and Windows plans. The compute supervisor and router executor require
Linux; console/client portability does not make these components native Windows
services. A successful install does not prove the network is isolated.

Validation entry points are in README. New skills use YAML frontmatter with
`name` and `description`; keep executable examples consistent with actual CLI
arguments. Check links, tutorial controls, narrow SSH permissions and clean-home
behavior. Run `python3 audit_repository.py` before publication. Do not commit
generated keys, diagnostics, machine addresses or account-specific examples.
