# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal Ansible repository that configures the user's homelab/infrastructure: two Proxmox
hypervisors (`ceres`, `luna`) and their guests, workstations (`serenity`), a DigitalOcean VPS
(`do-ams3-01`), and a couple of standalone hosts. Hosts are reached over Tailscale
(`*.hedgehog-skate.ts.net`) or internal DNS (`*.internal.<site>.lwatson.dev`).

## Commands

Dependencies are managed with `uv`, and the venv lives at `.venv` (Python 3.14, `ansible-core==2.21.1`).

```sh
uv sync                                   # install the venv
uv sync --upgrade --all-groups            # update it

# Run the full site playbook
ANSIBLE_HOST_KEY_CHECKING=False uv run ansible-playbook playbooks/main.yml

# Target specific hosts/groups and/or roles
uv run ansible-playbook playbooks/main.yml --limit ceres_guests
uv run ansible-playbook playbooks/main.yml --tags "systemd-network"
uv run ansible-playbook playbooks/main.yml --check --diff   # dry run

# Ad-hoc commands against a group
ANSIBLE_HOST_KEY_CHECKING=False uv run ansible ceres_guests -m ping

# Linting (also run via prek/pre-commit hooks)
uv run yamllint --strict .
uv run ansible-lint
uv run ruff check --fix .
uv run ruff format .
uv run shellcheck <file>
uv run prek run --all-files              # run all configured hooks

# Vault
vault/pass.sh                             # prints the vault password (gpg-decrypts vault/pass.asc)
uv run ansible-vault encrypt_string 'value' --name '_vault_some_var'
```

`ansible.cfg` sets `inventory = ./inventory/inventory.yml`, `roles_path = ./roles`, and
`vault_password_file = vault/pass.sh`, so plain `ansible-playbook`/`ansible` invocations pick these
up automatically without extra flags. `ANSIBLE_HOST_KEY_CHECKING=False` (or `-o
StrictHostKeyChecking=no` for raw `ssh`) is needed for hosts not already in `known_hosts`.

There is no test suite. Correctness is enforced by `ansible-lint`, `yamllint --strict`, and
`ansible-playbook --check`. `nova` being unreachable (exit code 4) from `playbooks/main.yml` is
pre-existing and expected.

## Architecture

### Inventory layout

- `inventory/inventory.yml`: a single static inventory. Hosts are grouped by role/site, and groups
  are layered rather than exclusive: e.g. a host like `ceres-caddy` is simultaneously in `vms`,
  `lxc`, `ceres_guests`, `guests`, `debian`, `borgmatic`, `docker`. `playbooks/main.yml` applies
  each role to whichever group combination is relevant (e.g. `hosts: "all,!hypervisors"`,
  `hosts: "borgmatic:zfs:smartmontools"`).
- `inventory/group_vars/<group>/` and `inventory/host_vars/<host>/` each hold up to three files:
  - `main.yml`: plain variables consumed directly by roles.
  - `internal.yml`: `_`-prefixed variables that exist only to be referenced by other var files
    within the same scope (not consumed by roles directly), e.g. `_guest_iface`, `_site_gateway`.
  - `vault.yml`: also `_`-prefixed, but each value is individually vault-encrypted with
    `!vault |` rather than the whole file. These files are plaintext YAML with inline encrypted
    scalars. Read/grep them directly, and do not run `ansible-vault view` on them.
  - The convention throughout is: `vault.yml`/`internal.yml` define `_vault_x` / `_x`, and
    `main.yml` (at the same or a broader scope) maps that onto the real variable ansible/roles
    expect, e.g. `telegram_chat_id: "{{ _vault_telegram_chat_id }}"` in `group_vars/all/main.yml`.
- Key groups: `hypervisors` (ceres, luna), `vms` / `lxc` (Proxmox guest type, used for
  virtualization-specific settings like the guest network interface name, `ens18` vs `eth0`),
  `ceres_guests` / `luna_guests` (per-site guest membership), `guests` (union of both),
  `servers` / `workstations`, `zfs`, `docker`, `borgmatic`, `smartmontools`.

### Roles (`roles/*`)

Standard `tasks/`, `defaults/`, `vars/`, `templates/`, `handlers/` layout per role. Conventions to
follow when adding or editing tasks:

- Task `name:` is always `"<role>:<subresource>:<action>"` (e.g.
  `"systemd-network:resolved:configure"`), and every task carries `tags: ["<role>"]`.
- Prefer guarding tasks with `when: ansible_facts['os_family'] == 'Debian'` or
  `ansible_facts['virtualization_type'] == 'lxc'` over relying on inventory group exclusion -
  facts describe what's actually true of the host.
- Handler names follow `"<role>:<service>|<action>"` (e.g. `"systemd-network:resolved|restart"`).
- Templates that produce a managed config file start with:
  ```
  #
  # {{ ansible_managed }}
  #
  ```
  (after any shebang or similar leading comments, if the file needs them, separated from them by
  a blank line).
- `notifications` role installs `uv` on the target host itself (not just locally) to run
  Python notification scripts (`*-notify.py` under `roles/notifications/templates/`) via
  `apprise`. These are the one place `ruff` per-file-ignores apply (`ERA001`, `S603`, `S607`,
  `TRY003`). Delivery failures log and exit 0 (the heartbeat timeout is the alarm), while
  unknown-event/usage errors exit 1.
- The `apt` role deploys a no-recommends/no-suggests conf repo-wide, so a plain package task is
  fine here. Set `install_recommends: false` explicitly only outside this repo.
- LXC guests configured via `systemd-network` must use `eth0`, not `en*` predictable names. The
  latter silently fails to come up on LXC.
- Don't set `IPForward=` in systemd-network drop-ins. It's deprecated and silently ignored by
  current systemd.

### Playbook structure

`playbooks/main.yml` is the single entry point: an ordered list of plays, each applying one role
to a specific host pattern (see file for the exact ordering: users -> common/systemd -> sysctl ->
networking -> docker -> zram -> apt -> needrestart -> msmtp -> smartmontools -> notifications ->
zfs-monitoring -> ssh -> borgmatic). `playbooks/speedtest.yml` is a standalone playbook for the
`speedtest` role.

### Linting configuration

- `ansible-lint` (`.ansible-lint.yml`) skips `name[casing]`, `yaml[line-length]`, `role-name`,
  `package-latest`.
- `yamllint` (`.yamllint.yml`) disables line-length, enables quoted-strings.
- `ruff` (`pyproject.toml`) targets the notification scripts. The shared Python conventions apply
  (f-strings only including logging, `from module import Name` imports, no single-character
  variable names, `exc` for caught exceptions, import ordering left to ruff).
- `prek.toml` wires all of the above plus `shellcheck` and end-of-file/trailing-whitespace/shebang
  checks into pre-commit hooks (via `prek`, a `pre-commit` reimplementation).

## Operations

- PVE guests: `pct reboot`/`pct exec` for LXCs, `qm reboot` for VMs. Never mix them up. After
  editing `cluster.fw`, run `pve-firewall restart` to push rules live.
- `systemctl reload nftables`, never restart. Restarting `tailscaled` clobbers nftables, so chain
  `&& systemctl reload nftables`. Use `networkctl reconfigure` (not `renew`) for a full DHCP
  cycle, or just reboot the guest.
- Never restart or reconfigure a connectivity service over the path it provides. When working on
  a host behind a jumphost, don't touch the jumphost itself until every operation on the hosts
  behind it is finished. If a breakage is truly unavoidable, log in to the hypervisor over the
  non-Tailscale, non-jumphost path (its public interface or `192.168.*` LAN interface) and drive
  the guest from there with `qm`/`pct` commands (e.g. `qm guest exec`).
- dnsmasq on both sites' firewalls is not Ansible-managed. SSH in and edit directly, and always
  `systemctl restart dnsmasq` (reload does not pick up config changes).
- SSH: always `-o StrictHostKeyChecking=no`, never add `ConnectTimeout`.
