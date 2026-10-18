# Quick Start

`./quickstart.sh` (repository root) runs the whole stack locally with synthetic data, in about 5 minutes.

## Usage
Requirements: Docker with Compose v2 (Docker Desktop on macOS / Windows, Docker Engine on Linux), `python3` ≥ 3.9, ~4 GB free RAM, ~6 GB disk. Nothing is installed on your machine besides containers.

```bash
git clone https://github.com/<owner>/Watch2Whoop.git
cd Watch2Whoop
./quickstart.sh            # add --grafana for the Grafana dashboards
```
The script
1. generates random secrets in `quickstart/.data/` (git-ignored),
2. starts Body Engine, Open Wearables (API + workers) and SparkyFitness as one compose project (`watch2whoop-quickstart`), every port bound to `127.0.0.1`,
3. creates a demo user, generates **60 days of synthetic data** (heart rate, HRV, sleep stages, workouts, steps, weight; no real person's data) and imports it through the real Apple Health importer, adds synthetic meals / strength sessions / weigh-ins to SparkyFitness, computes all scores and reminders,
4. prints a one-time login link for the Today page.

| URL | What |
|---|---|
| `http://localhost:18100/app/#login=…` | Today page (the link logs you in; `./quickstart.sh login` prints a new one) |
| `http://localhost:18100/docs` | Body Engine API (OpenAPI) |
| `http://localhost:18300` | SparkyFitness (back end; demo account `demo@example.com`, password in `quickstart/.data/secrets/`) |
| `http://localhost:18200/docs` | Open Wearables API |
| `http://localhost:18400` | Grafana (with `--grafana`; user `admin`) |

- Ports default to 18100 / 18200 / 18300 / 18400; a busy port is replaced by the next free one, or set `W2W_BODY_PORT`, `W2W_OW_PORT`, `W2W_SF_PORT`, `W2W_GRAFANA_PORT`. `W2W_PROJECT` changes the compose project name.
- Other commands: `./quickstart.sh status | logs [service] | down | reset` (`reset` deletes the containers, volumes and `quickstart/.data/`).
- The demo user's time zone is your machine's (`--tz` to override). The UI is Chinese-first today (English UI is on the roadmap).
- For your own data, see [Self-hosting](../docs/README.md#self-hosting).

中文：需要 Docker（Compose v2）、`python3` ≥ 3.9、约 4 GB 空闲内存和 6 GB 磁盘。`./quickstart.sh` 生成随机密钥、启动全部服务（端口只绑 `127.0.0.1`）、导入 60 天合成数据、打印今天页登录链接；加 `--grafana` 同时启动 Grafana。端口被占用会自动顺延（也可用上面的 `W2W_*_PORT` 指定）；其他命令 `./quickstart.sh login | status | logs | down | reset`。

## Files

| File | Role |
|---|---|
| `run.py` | Orchestrator (standard library only): port selection, secrets, `docker compose up`, Open Wearables API key, SparkyFitness demo account + API keys, demo user, data, login link |
| `compose.yaml` | One compose project with every service. Same pinned images as production; Open Wearables runs only its API, worker and beat (no developer portal, Flower, Svix or S3) |
| `demo_data.py` | Generates a synthetic Apple Health `export.zip` (heart rate every 2–6 min, overnight HRV / respiratory rate / SpO₂ / wrist temperature, sleep stages in ~90-minute cycles, 5 workout types with heart-rate curves, steps, energy, weight, VO₂max). Training load feeds the next morning's HRV and resting heart rate, so recovery and strain react realistically |
| `demo_sparky.py` | Runs inside the Body Engine container and writes synthetic meals, strength sessions and weigh-ins through Body Engine's own SparkyFitness write path |
| `grafana/` | Grafana provisioning for the Quick Start (Body Engine + Open Wearables data sources through the read-only `grafana_ro` roles; the production dashboards from `deploy/grafana/dashboards/`) |
| `.data/` | Created on first run, git-ignored: `quickstart.env` (ports, DB passwords), `open_wearables.env`, `secrets/` (one file per secret, mode 600), `health/` (import spool), markers that make re-runs idempotent |

Re-running `./quickstart.sh` is safe: existing secrets, users and data are kept; only missing steps run. `./quickstart.sh reset` starts over.

Differences from a real deployment: no Tailscale / HTTPS (plain `http://localhost`, so the session cookie is allowed without `Secure` for `localhost` only via `BODY_WEB_INSECURE_LOCAL_COOKIE=1`), no backups or scheduled jobs (scores are computed once at start-up), SparkyFitness sign-up is opened only while the demo account is created.

## Tested
| Date | Host | Result |
|---|---|---|
| 2026-10-18 | macOS 26, Apple silicon, Docker Desktop (engine 29.1) | From `reset`: 49 s with images cached; Today page, trends, sleep, reminders, nutrition all populated |
| 2026-10-18 | Linux: Debian 13 (trixie) client container, Python 3.12, Docker CLI 29.1.3 + Compose v2.40.3, against the Docker Desktop Linux engine; fresh `git clone` of the public export, `W2W_PROBE_HOST=host.docker.internal`, ports 19100–19400 | Passed with `--grafana`: Today page (recovery, sleep, strain, monitor), both Grafana data sources OK, 5 dashboards loaded |

Not tested: a bare-metal / VM Linux host with its own Docker Engine (e.g. Ubuntu), Windows, Intel Macs. Ubuntu 18.04's
system Python 3.6 is too old for `run.py`; install a newer `python3` first.

