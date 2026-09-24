# NixOS Base Architecture

## Status

Implemented as an additive desired-state layer on the `nixos/base-pivot` branch.

This does **not** replace the SUBSTRATE Rust control plane. NixOS owns slow-changing
host desired state; the Rust daemons continue to own the fast runtime loop.

## Two-plane model

### Desired-state plane — NixOS

NixOS owns:

- kernel and boot configuration
- installed packages and systemd units
- firewall and host security defaults
- mount layout and zram baseline
- static hardware profile selection
- generation construction, test activation, promotion, and rollback

A host change should be expressed as source, built as a candidate generation,
qualified, activated with a reversible test step, observed, and only then promoted.

### Runtime plane — SUBSTRATE

The existing Rust control plane owns:

- PSI observation
- cgroup v2 memory/resource enforcement
- NUMA-aware placement
- inference admission and lifecycle
- RAM workspace policy
- bounded runtime recovery
- RESIDUAL/MCP requests and receipts

Nix evaluation/rebuild is intentionally **not** part of the sub-second control loop.

## Recovery boundary

Nix generations recover host software/configuration. Btrfs remains responsible for
mutable data that Nix cannot roll back, including application state, durable agent
state, datasets, receipts, and database contents.

A future release receipt should bind at least:

1. source commit
2. flake lock
3. Nix system derivation / generation
4. mutable-data snapshot identity
5. hardware qualification receipt
6. runtime qualification receipt

## iDRAC boundary

The current architecture is preserved: iDRAC7 is out-of-band recovery infrastructure
and is not exposed to the normal agent authority domain. Any future automated iDRAC
controller must be a separately authenticated recovery plane.

## Current limitation: Rust package closure

The NixOS module accepts `services.substrate.package`, but the repository does not
yet contain a committed Cargo lock/vendor closure suitable for reproducible Nix
packaging. Until that is added, the NixOS host profile evaluates and builds without
starting `omarchy-configd` or `omarchy-cgroupd`.

This is fail-safe by design: absence of a verified runtime package does not silently
fall back to an impure network build.

## Build/evaluation

With Nix installed and flakes enabled:

```bash
nix flake check --no-build
nix eval .#nixosConfigurations.substrate-r720-eval.config.system.build.toplevel.drvPath
nix build .#packages.x86_64-linux.installer
```

The `substrate-r720-eval` configuration is intentionally non-bootable/containerized for CI evaluation; a physical host must add its generated disk, root filesystem, encryption, and boot-loader configuration.\n\nThe resulting installer is a minimal NixOS ISO with SUBSTRATE host scaffolding and
R720 qualification tools. Device-specific disk layout, encryption, boot loader, and
PERC policy remain owner/hardware qualification inputs.
