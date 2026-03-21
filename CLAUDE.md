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

| Service | Role |
|---|---|
| **configurator** | Init container — writes `common_site_config.json`, then exits |
| **backend** | Gunicorn WSGI server (frappe.app:application) |
| **frontend** | Nginx reverse proxy (port 8080) |
| **websocket** | Node.js Socket.IO server (port 9000) |
| **queue-short** | Worker: short,default queues |
| **queue-long** | Worker: long,default,short queues |
| **scheduler** | Cron scheduler (bench schedule) |

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

| Arg | Purpose |
|---|---|
| `FRAPPE_BRANCH` | Frappe framework version to install |
| `FRAPPE_BUILD` | Pre-built base image tag (for layered builds) |
| `FRAPPE_PATH` | Frappe git repository URL |
| `APPS_JSON_BASE64` | Base64-encoded custom_apps.json |

## File Conventions

- Shell scripts: formatted with shfmt, linted with shellcheck (`-x` flag for sourced files)
- Python: formatted with black, imports sorted with isort (black profile), minimum Python 3.7+
- YAML/JSON: formatted with prettier
- Environment variables: see `example.env` for all available options
- Documentation lives in `docs/` — update relevant docs when changing deployment behavior
