# homebase System Overview

## Purpose

`homebase` is a personal home server system made up of three primary nodes:

- `control`
- `workstation`
- `host`

Each node has a distinct responsibility in the overall system:

- `control`: the always-on control plane used for orchestration, health visibility, and controlled remote operations
- `workstation`: the primary user-facing machine used for daily personal computing and Sunshine-based game and desktop streaming
- `host`: the main infrastructure host running Proxmox and the core always-on server workloads through virtual machines

Together, these nodes form a system where user interaction, infrastructure hosting, and control-plane operations are intentionally separated.

## Naming Model

The system uses two naming layers with a strict purpose for each.

### 1. CLI resource path

These names are used inside the future `homebase` CLI.

- `control`
- `workstation`
- `host`
- `host.storage`
- `host.app`
- `host.llm-agents`

The dot (`.`) is only an internal hierarchy separator for the CLI and documentation model. It is not intended to be used directly as the OS hostname format.

### 2. Runtime host name

These names are the actual machine or VM identifiers intended for hostnames, SSH aliases, inventory entries, monitoring targets, and related runtime use.

Current VM naming on the Proxmox host is:

- `storage`
- `app`
- `llm-agents`

The future `homebase` CLI should map those VMs to:

- `host.storage`
- `host.app`
- `host.llm-agents`

Naming rules:

- use short role-based VM names for the current Proxmox guest names
- use hyphens (`-`) only where they improve readability, such as `llm-agents`
- use dots (`.`) only for CLI resource hierarchy
- do not include `vm` in names

## Current Implementation Status

Current observed build stage:

- the `host` node is mostly built
- the three planned VMs have been installed
- basic setup on `storage`, `app`, and `llm-agents` is nearly complete
- the next infrastructure workflow step is to move the `homebase` working directory onto NAS-backed storage

## High-Level Topology

`homebase` consists of three nodes.

### `control`

- Hardware: Raspberry Pi 4 Model B Rev 1.2
- CPU: 4-core ARM Cortex-A72 @ up to 1.5 GHz
- Memory: 4 GB RAM (`3887864 kB` detected, about `3.7 GiB` usable)
- Storage:
  - primary boot/root device: `mmcblk0`
  - card size: `59.6G` detected, effectively a 64 GB SD card class device
  - boot partition: `512M` FAT mounted at `/boot/firmware`
  - root partition: `59.1G` ext4 mounted at `/`
- OS: Debian GNU/Linux 13 (`trixie`)
- Kernel: `6.12.47+rpt-rpi-v8`
- Role: always-on control plane for the whole system
- Main responsibilities:
  - health visibility
  - orchestration
  - status inspection
  - controlled remote operations
  - SSH jump/control path
  - Wake-on-LAN and reboot workflows
  - Ansible execution

Design intent:

- stay lightweight and recoverable on Raspberry Pi 4 hardware
- avoid persistent storage-heavy observability services on local SD storage
- remain usable even if `host.app` is unavailable

Observed platform notes:

- both `eth0` and `wlan0` interfaces are present on the node
- the system is suitable for CLI, Ansible, SSH control, and lightweight health checks
- the system is a poor fit for long-lived metrics or log storage on local removable media

### `workstation`

- Hardware:
  - AMD Ryzen 7 5700X
  - B550M mainboard
  - 48 GB DDR4 RAM
  - NVIDIA RTX 2060
  - 1 TB NVMe SSD
- OS: Windows 11
- Role: primary personal machine
- Main responsibilities:
  - documents
  - coding
  - browsing
  - gaming
  - Sunshine host for Moonlight streaming

### `host`

- Hardware:
  - Intel i5-13400
  - B760M mainboard
  - 40 GB DDR4 RAM (32 GB + 8 GB)
  - 500 GB NVMe SSD
  - 4 TB WD Red HDD x2 or x3
- Platform: Proxmox
- Role: main infrastructure host
- Main responsibilities:
  - virtualization
  - storage services
  - application hosting
  - agent execution

## `host` VM Layout

The `host` node contains three VMs.

### `host.storage`

- Current VM name: `storage`
- Planned platform: TrueNAS with ZFS
- Role:
  - personal NAS
  - private cloud data backend
  - snapshot and backup source

Storage design:

- `4 TB x2`: ZFS mirror
- `4 TB x3`: RAIDZ1

Backup plan:

- external `3 TB` HDD in DAS enclosure
- monthly ZFS pool backup
- weekly snapshot backup

### `host.app`

- Current VM name: `app`
- Planned platform: Linux VM with Docker
- Role:
  - self-hosted applications
  - Plex
  - personal web apps
  - Prometheus
  - Grafana
  - other persistent observability services such as long-lived metrics or log storage

### `host.llm-agents`

- Current VM name: `llm-agents`
- Planned platform: isolated Linux VM
- Role:
  - LLM agent workloads
  - root-level autonomous task execution within the VM boundary

Security intent:

- keep it strongly isolated through Proxmox
- treat it as a higher-risk execution environment than other VMs

## Network Structure

Current planned layout:

- router -> switch
- switch -> `workstation`
- switch -> `host`
- `control` -> Wi-Fi or Ethernet

Current preference:

- Wi-Fi is preferred for `control` if there is no meaningful technical or stability downside
- Ethernet remains an option if control-plane reliability requires it

## Access Model

The target access model is:

- all services stay internal by default
- remote access goes through Tailscale
- operational control should flow through `control`
- SSH operations should be mediated through `control`
- remote commands should be exposed through predefined `homebase` CLI actions rather than arbitrary manual SSH use where possible

Known exception:

- `workstation` hosts Sunshine for Moonlight streaming inside the LAN and for remote streaming scenarios

## Planned Control Plane Capabilities

The `control` node is expected to become the orchestration and observability entry point for `homebase`.

Planned components:

- custom `homebase` CLI
- Ansible
- health-check workflows

Observability service placement:

- `control` remains the operator entry point and runs the CLI plus lightweight health checks
- `host.app` runs Prometheus, Grafana, and other storage-heavy observability services
- `control` should continue to function for SSH, status, and recovery-oriented commands even if `host.app` is down

## Repository Location

The intended working model is:

- move the `homebase` directory onto NAS-backed storage exposed by `host.storage`
- treat the NAS copy as the canonical operator workspace
- keep progress artifacts, generated outputs, and related operational traces on NAS so work can be inspected from the shared storage location

Planned CLI behavior:

- hierarchical resource selection
- autocompletion-friendly paths
- status inspection by node or subnode
- predefined operational commands

Example resource paths:

```text
homebase status control
homebase status workstation
homebase status host
homebase status host.storage
homebase status host.app
homebase status host.llm-agents
```

Desired UX principle:

- the CLI should expose rich context and discoverable targets
- resource hierarchy should be visible through completion
- the user should not need to memorize every runtime hostname

## Open Items

These items still need confirmation or refinement:

- whether `control` should be Wi-Fi or Ethernet
- exact placement of Nextcloud
- final backup workflow details, especially capacity fit against the `3 TB` backup disk
- the final NAS mount path used for the canonical `homebase` directory
