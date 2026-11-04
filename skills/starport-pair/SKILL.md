---
name: starport-pair
description: Pair Starport with Starship using a dedicated SSH key, independently verified host fingerprint and a restricted RPC command, without enabling a general shell for the control key.
---

# Pair A with B

Locate the checkout and read `docs/PAIRING.md`. A holds the private key; B receives
only the public key. Do not use a user's general-purpose or compute-server key.

1. Confirm Starship is installed and B's SSH service is reachable on the intended
   private path. Inspect an existing pairing before generating or replacing keys.
2. Generate the dedicated key with `ssh-keygen` on A as shown in the guide. Do not
   overwrite an existing key or print a private key. Decide how the unattended
   service can unlock it: a service-session agent or a dedicated restricted key.
3. On B, preview `python3 tools/pair.py authorize --public-key starport_starship.pub`.
   Apply it once the dedicated key is confirmed. The helper adds only a restricted
   fixed-command rule and preserves other entries; it refuses conflicting rules.
   On Windows use a non-administrator local Starship account; do not repurpose the
   shared administrators key file.
4. Obtain B's Ed25519 SSH **host** fingerprint locally. This is a different key
   from A's login key. On A, run `tools/pair.py pin` with that fingerprint, inspect
   the match and add `--apply`. A mismatched fingerprint must be investigated,
   never bypassed with disabled host-key checks.
5. Generate the host block with `tools/pair.py config`. Review and merge it into
   A's SSH config without duplicating `Host starship` or replacing unrelated hosts.
   It disables agent/environment forwarding and pins a dedicated known-hosts file.
6. Run `python3 tools/pair.py verify`, adding `--client-platform windows` for a
   Windows B. Then test a disallowed command such as `ssh starship whoami`: it
   must return `ssh_command_blocked`. A pairing key is intentionally not a shell.

The helper's `pin` probes SSH but changes nothing without `--apply`. Its config
output is local operator data, not material to commit or send to Claude.
