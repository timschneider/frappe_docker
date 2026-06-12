# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Customized Frappe Docker deployment for a German ERPNext installation. Based on the official [frappe_docker](https://github.com/frappe/frappe_docker) repository (rebased onto v2.1.0), extended with Germany-specific apps (DATEV, ERPNext Germany, EU eInvoice) from ALYF. Images are built and pushed to GitHub Container Registry (ghcr.io) via GitHub Actions on the `release` branch.

## Build & Deploy

### Building the custom image locally

```bash
export APPS_JSON_BASE64=$(base64 -w 0 custom_apps.json)
docker build \
  --build-arg=FRAPPE_BRANCH=version-15 \
  --build-arg=APPS_JSON_BASE64=$APPS_JSON_BASE64 \
  --tag=custom-erpnext:latest \
  --file=images/layered/Containerfile .
```

### Running with Docker Compose

```bash
# Full stack with MariaDB and Redis
docker compose -f compose.yaml \
  -f overrides/compose.mariadb.yaml \
  -f overrides/compose.redis.yaml \
  -f overrides/compose.noproxy.yaml \
  up -d

# Quick demo (all-in-one)
docker compose -f pwd.yml up -d
```

### Site operations (run inside backend container)

```bash
docker compose exec backend bench new-site --admin-password=admin <site-name>
docker compose exec backend bench migrate
docker compose exec backend bench build
```

### Running tests

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-test.txt
pytest  # runs with -s --exitfirst by default (see setup.cfg)
```

### Pre-commit hooks

```bash
pre-commit install
pre-commit run --all-files
```

Configured hooks: black, isort (black profile), prettier, pyupgrade, shellcheck, shfmt, codespell.

## Architecture

### Docker image build pipeline

Two Containerfile approaches:

- **`images/layered/Containerfile`** (primary, used by CI) — two-stage build using pre-built `frappe/base` and `frappe/build` images. Fast. This is what GitHub Actions uses.
- **`images/production/Containerfile`** — full multi-stage build from scratch (base → build → builder → erpnext). Used by `docker-bake.hcl` targets.

Both produce a Gunicorn-based image running as user `frappe` on port 8000.

### Compose service architecture

`compose.yaml` defines the core services using YAML anchors (`x-customizable-image`, `x-backend-defaults`):

| Service          | Role                                                          |
| ---------------- | ------------------------------------------------------------- |
| **configurator** | Init container — writes `common_site_config.json`, then exits |
| **backend**      | Gunicorn WSGI server (frappe.app:application)                 |
| **frontend**     | Nginx reverse proxy (port 8080)                               |
| **websocket**    | Node.js Socket.IO server (port 9000)                          |
| **queue-short**  | Worker: short,default queues                                  |
| **queue-long**   | Worker: long,default,short queues                             |
| **scheduler**    | Cron scheduler (bench schedule)                               |

Infrastructure services (DB, Redis, proxy) are added via `overrides/compose.*.yaml` files. The modular override pattern avoids duplicating service definitions.

### Custom apps

`custom_apps.json` is the central configuration — a JSON array of `{url, branch}` objects defining which Frappe apps to install. The CI pipeline base64-encodes this file and passes it as `APPS_JSON_BASE64` build arg. Modifying this file and pushing to `release` triggers a new image build.

Current apps: ERPNext, Payments, HRMS, eCommerce Integrations, Webshop, ERPNext DATEV, ERPNext Germany, ERPNext PDF-on-Submit, EU eInvoice.

### CI/CD

GitHub Actions workflow (`.github/workflows/build_push.yml`):

- Triggers on push to `release` branch or `v*` tags
- Validates `custom_apps.json` with jq
- Builds using `images/layered/Containerfile`
- Pushes to `ghcr.io/<org>/frappe_docker`
- Generates supply chain attestation

Auto-update workflow (`.github/workflows/check_updates.yml`):

- Runs weekly (Monday 06:00 UTC) or on manual dispatch
- Checks all apps in `custom_apps.json` for newer tags
- Creates/updates a PR on `auto-update/app-versions` branch

### Key build args

| Arg                | Purpose                                       |
| ------------------ | --------------------------------------------- |
| `FRAPPE_BRANCH`    | Frappe framework version to install           |
| `FRAPPE_BUILD`     | Pre-built base image tag (for layered builds) |
| `FRAPPE_PATH`      | Frappe git repository URL                     |
| `APPS_JSON_BASE64` | Base64-encoded custom_apps.json               |

## Devstack (VS Code Dev Container)

The dev container runs a full bench under `development/frappe-bench/`. The host folder `development/` is bind-mounted into the container at `/workspace/development` (host `development/X` == container `/workspace/development/X`).

### App layout — two separate git clones (intentional)

| Path | Role | git remote | Branch |
| --- | --- | --- | --- |
| `development/apps/Automated-purchase-process` | **Dev source of truth** — edit & commit here | `origin` → github.com/Meltingplot/Automated-purchase-process | `main` |
| `development/frappe-bench/apps/procurement_ai` | **Live code in the bench** (editable-installed, what Frappe imports) | `upstream` → `/workspace/development/apps/Automated-purchase-process` (local path) | `main` |

These are **independent clones**, not symlinked/bind-linked. Editing the standalone does **not** appear in the running bench until you propagate (see below). The app's real name is **`procurement_ai`** (Python module + `apps.txt` entry + dir under `apps/`). It was historically `erpnext_procurement_ai`; that name is dead — never reintroduce it.

### Propagation: standalone → bench (the dev loop)

**Rule: propagate by running `git pull` directly inside the bench clone** (`frappe-bench/apps/procurement_ai`) — never by re-running `bench get-app` for an already-installed app, and never by editing the bench clone directly.

1. Edit + **commit** in `development/apps/Automated-purchase-process` (uncommitted changes do not transfer).
2. Pull into the bench clone from its `upstream` (the local standalone):
   ```bash
   cd /workspace/development/frappe-bench/apps/procurement_ai
   git pull upstream main          # or: git fetch upstream && git reset --hard upstream/main
   ```
3. Apply the change in the running bench:
   ```bash
   bench build --app procurement_ai                       # JS/CSS changes only
   bench restart                                          # Python changes
   bench --site development.localhost migrate             # new fixtures / patches / DocTypes
   ```

> This `git pull` does **not** appear in `bench.log` (which only records `bench` commands). To audit propagation history, use `git reflog` / `git log` in the bench clone, not `bench.log`.

### Install / update / uninstall

Run inside `/workspace/development/frappe-bench`. The active site is **`development.localhost`**.

```bash
# Install (clone into bench + editable pip install + build), then add to the site
bench get-app --resolve-deps /workspace/development/apps/Automated-purchase-process
bench --site development.localhost install-app procurement_ai
bench --site development.localhost migrate

# Update: see "Propagation" above (git pull upstream + build/restart/migrate)

# Uninstall — ORDER AND FLAGS MATTER:
bench --site development.localhost uninstall-app procurement_ai   # remove from site (drops its tables)
bench remove-app procurement_ai --force                          # remove from bench
```

**Uninstall gotchas (learned the hard way):**
- `remove-app` is a **bench-global** command — it takes **no `--site`** (that errors with `No such option: --site`).
- `remove-app` validates "is it installed on any site?" via a cached `list-apps` that can be **stale** even after a successful `uninstall-app`, blocking with `Cannot remove, app is installed on site`. `--force` skips that check. If still stuck: `bench --site development.localhost remove-from-installed-apps procurement_ai && bench --site development.localhost clear-cache`, then retry.
- `remove-app` moves the dir to `archived/apps/procurement_ai-<date>` (doesn't delete). Clean up with `rm -rf archived/apps/procurement_ai-*`.

### Dependencies — keep `easyocr` out

`easyocr` pulls **torch/CUDA (~4 GB)**. It is **optional and lazy-imported** (only inside `extraction/ocr_engine._extract_easyocr`), so it must **not** be in `pyproject.toml` `dependencies`. If a `bench get-app` / `pip install -e` hangs after "Built procurement-ai … Released lock", it is downloading torch — kill it and confirm `easyocr` is absent from `pyproject.toml`. `requirements.txt` already omits it; keep the two consistent.

### How Claude interacts with the dev container

The container is the compose service `frappe` (project `frappe_docker_devcontainer`). Resolve it by name (stable across rebuilds, unlike the short ID) and `docker exec` with a login shell:

```bash
CID=$(docker ps -qf name=devcontainer-frappe-1)
docker exec "$CID" bash -lc 'cd /workspace/development/frappe-bench && bench --site development.localhost <cmd>'
```

- File edits: do them on the **host** path `development/...` (bind-mounted, instantly visible in the container) — no `docker cp` needed.
- `bench`/`git pull`/`migrate`/process inspection: must run **inside** the container via `docker exec`.
- The container has no `ps`/`top` (no `procps`). List processes via `/proc`:
  `for p in /proc/[0-9]*; do printf '%s\t%s\n' "${p#/proc/}" "$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)"; done`

### Reading encrypted Settings (e.g. API keys)

`AI Procurement Settings` stores provider keys (`claude_api_key`, `openai_api_key`, …) as Password fields — encrypted with the site's `encryption_key`, so the raw DB/`__Auth` value is ciphertext. Decrypt via bench:

```bash
docker exec "$CID" bash -lc 'cd /workspace/development/frappe-bench && bench --site development.localhost console' <<<'
from frappe.utils.password import decrypt
print(decrypt(frappe.db.get_single_value("AI Procurement Settings", "claude_api_key")))'
```

## File Conventions

- Shell scripts: formatted with shfmt, linted with shellcheck (`-x` flag for sourced files)
- Python: formatted with black, imports sorted with isort (black profile), minimum Python 3.7+
- YAML/JSON: formatted with prettier
- Environment variables: see `example.env` for all available options
- Documentation lives in `docs/` — update relevant docs when changing deployment behavior
