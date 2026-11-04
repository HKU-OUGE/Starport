---
name: starport-compute
description: Register and validate existing isolated Linux compute nodes with Starport, including capacity synchronization, immutable dependencies and durable task control integration.
---

# Attach compute

Read `docs/SERVICES.md`, `compute_runtime/README.md`, and
`examples/compute-sync.example.json`. This adapter connects an existing training
service. It does not create a cloud instance, assemble gVisor or install a complete
training runtime on a blank server.

Inventory node capabilities, current tasks and mount permissions first. Keep
operator credentials and instance/network details on A. Workloads receive only
task-scoped files, selected read-only dependencies/data and the controlled proxy.
Use operator-chosen private configuration paths; do not publish live configs.

- Derive GPU, CPU and memory limits from current inventory plus explicit system
  reserves. Check all three resources and existing workloads before admission.
- Reuse immutable dependency views and datasets. Add a new version/layer for
  changes; do not mutate dependencies used by running tasks.
- Install both sides of durable control before advertising support. Follow the
  adapter contract; do not assume copying the supervisor alone creates a sandbox.
- Keep manual pauses and task identity. A disconnected controller is not proof of
  a dead task; reclaim only with remote cleanup, process and resource-lock evidence.
- Validate an approved small task, live logs, hashes, result download, cancellation
  and resource release. Test isolation on a dedicated test task. Do not fill the
  real quota merely to test rejection behavior.

For a blank remote server, produce a separate executor deployment plan based on
its available privileges and OS before registering it. If creating or restarting
paid resources is outside the user's authorization, complete read-only preparation
and request that specific action rather than changing existing instances.
