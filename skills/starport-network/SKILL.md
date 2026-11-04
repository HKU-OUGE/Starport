---
name: starport-network
description: Plan and validate Starport's controlled gateway, import subscriptions and change proxy nodes while preserving independent fail-closed guards and existing workloads.
---

# Configure the controlled exit

Read `docs/NETWORK.md` and the actual router profile. Distinguish two tasks:
creating a new network boundary, and attaching Starport to an existing gateway.
The router adapter currently manages an existing Linux/OpenWrt setup; it does
not provision a VM or replace a host firewall from an empty machine.

For a new boundary, inventory physical interfaces and subnets, select the router
host/VM technology for the user's OS, and prepare a concrete topology plus recovery
access before changing interfaces. Keep the user's administrative path reachable.
On Windows/macOS, use a Linux/OpenWrt VM for routing; do not present native console
installation as native firewall isolation. The guide contains the required links,
zones, startup order and acceptance checklist for the deployment agent.

For an existing gateway:

1. Check its router SSH identity, independent guards, endpoint allowlist and
   private profile. Use the existing adapter and exact configuration paths.
2. Import a supported HTTPS subscription or pasted node list. Do not import its
   DNS, scripts, rules or arbitrary controller/listener configuration.
3. Select the three distinct supported endpoints in priority order and preview
   the plan. `python3 starport.py apply-exit PLAN_ID --check` validates it without
   activating it. Apply only the reviewed plan within the user's authorized scope.
4. Check DNS and HTTPS through B, then perform approved proxy-stop, all-node-fail,
   direct/UDP/IPv6 and gateway-stop tests. A displayed node name is not proof of
   isolation. Restore the chosen proxy and verify recovery.
5. Record which interface/gateway changes are automatically reconciled by the
   actual deployment. The generic adapter does not install a host uplink timer.

Never recover connectivity by enabling direct fallback, host Internet sharing,
or an unreviewed second interface. Network tests that interrupt existing workloads
need an appropriate window; read-only diagnosis can proceed immediately.
