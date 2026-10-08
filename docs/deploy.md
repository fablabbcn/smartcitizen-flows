# Deploying flows

Step by step, from a fresh Debian/Ubuntu server to a working HTTPS deployment of
flows (`https://flows.smartcitizen.me`), plus how to move the existing server from
the old setup (nginx, certbot, RabbitMQ and cron) to this one.

Everything here is `docker compose` on a single host: Postgres, Redis, the Flask
app and API (`web`), the Celery workers (`celery`) and scheduler (`beat`), Flower,
and Caddy in front. It is not a multi-node or HA setup, and nothing in it needs to be.

**The rule this document is built around:** only ports **80** and **443** are
reachable from the internet, and only Caddy listens on them. The web app, Flower,
Postgres and Redis are not published at all: they are reached through Caddy (web,
Flower) or with `docker compose exec` (Postgres, Redis). See
[section 4](#4-why-ufw-alone-is-not-enough).

**Contents**

- [Before you start](#before-you-start)
- [Sizing](#sizing)
- [What runs where](#what-runs-where)
- [1. Server preparation](#1-server-preparation)
- [2. Firewall, before Docker](#2-firewall-before-docker)
- [3. Install Docker](#3-install-docker)
- [4. Why ufw alone is not enough](#4-why-ufw-alone-is-not-enough)
- [5. Clone the repository](#5-clone-the-repository)
- [6. Configure `.env`](#6-configure-env)
- [7. DNS and TLS](#7-dns-and-tls)
- [8. Flower credentials](#8-flower-credentials)
- [9. First boot](#9-first-boot)
- [10. Load the metadata](#10-load-the-metadata)
- [11. Verify](#11-verify)
- [12. Create the jobs](#12-create-the-jobs)
- [Moving from the old deployment](#moving-from-the-old-deployment)
- [Day to day](#day-to-day)
- [Updating](#updating)
- [Backups](#backups)
- [Troubleshooting](#troubleshooting)

---

## Before you start

| Requirement | Notes |
|---|---|
| VPS | **2 vCPU / 4 GB RAM / 40 GB disk.** See [Sizing](#sizing): memory is mostly the Celery workers, disk mostly Docker images. |
| Debian 12 or Ubuntu 22.04/24.04 | Any systemd distro with a current Docker works; the commands assume Debian/Ubuntu. |
| A domain name | `flows.smartcitizen.me`. Let's Encrypt does not issue certificates for bare IP addresses. |
| DNS control | To point an `A` record at the server. |
| A Smart Citizen admin account | Its API token is `SC_BEARER`: processing posts with it, and the daily sync uses it to find devices. You also sign in to the web interface with an admin account. |
| AWS S3 credentials | For the backup task (raw device data to `S3_DATA_BUCKET`). |
| SSH access as a non-root user with sudo | Step 1 sets this up if you only have `root`. |

---

## Sizing

Measured on the local stack (October 2026), not estimated:

| | Memory |
|---|---|
| Whole stack, idle | **~450 MB** |
| ├─ `celery` (workers, after running jobs) | 385 MB |
| ├─ `web` (gunicorn, one worker) | 32 MB |
| ├─ `postgres` | 18 MB |
| ├─ `beat` | 14 MB |
| ├─ `redis` | 4 MB |
| └─ `proxy` (Caddy) and `flower` | ~60 MB together |

A processing dry run of device 18154 kept `celery` at 389 MB and took 2.4 s.
Runs process at most 1000 rows per device, so a single run stays small, but the
worker autoscales to **4 processes** (`--autoscale=4,2`). That is why 4 GB is the
recommendation, not the idle figure. After the deploy, processing catches up from
each device's `latest_postprocessing` (the backlog since late 2025 takes about two
months to clear), so expect the workers to be busy for a while.

The database is small: **~9 MB** with all the metadata (1 blueprint, 158 hardware,
594 calibrations), their revision history and a few hundred jobs. It grows with
the run history (one row per run, with its log).

**Docker images are the largest consumer of disk.** The flows image is about
**2.1 GB** (scdata and its scientific stack). Postgres, Redis and Caddy add about
400 MB. Building on the server adds build cache on top; each rebuild leaves the
previous image behind untagged. `docker image prune` and `docker builder prune`
reclaim it (see [Updating](#updating)).

---

## What runs where

| Service | Image | What it does |
|---|---|---|
| `proxy` | `caddy:2-alpine` | TLS (Let's Encrypt), HTTP to HTTPS redirect, `/flower` behind basic auth, everything else to `web`. The only service with published ports. |
| `web` | `scflows:latest` | Flask app: web interface, metadata API (`/api/v1/`). Applies database migrations before serving. |
| `celery` | `scflows:latest` | Workers that run the jobs: `dprocess` (processing) and `dbackup` (backups to S3). |
| `beat` | `scflows:latest` | Scheduler, **one instance only**: queues the due jobs every minute, syncs the jobs with the Smart Citizen API every day at 03:00 (`CELERY_TIMEZONE`). |
| `flower` | `scflows:latest` | Celery monitoring, under `/flower`. |
| `postgres` | `postgres:16-alpine` | Metadata (blueprints, hardware, calibrations), their revision history, jobs and runs. |
| `redis` | `redis:7-alpine` | Celery broker (database 0) and locks (database 1). |

`web`, `celery`, `beat` and `flower` share one image (`scflows:latest`, the
`x-flows-image` anchor in `compose.yml`): building any of them updates all four,
so they cannot run different versions of the code.

Processing reads its metadata from flows itself: smartcitizen-connector and scdata
get hardware, blueprints, calibrations and sensor names from `BASE_POSTPROCESSING_URL`,
which points at this deployment's own API.

---

## 1. Server preparation

Skip this on the existing server. On a new one, log in as `root` and create an
unprivileged account. Everything after this step runs as that user.

```bash
adduser --gecos "" deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy/
```

Open a **second terminal** and confirm `ssh deploy@your-server` works before
closing the first one.

Then harden SSH in `/etc/ssh/sshd_config`:

```
PermitRootLogin no
PasswordAuthentication no
```

```bash
sudo systemctl restart ssh
sudo apt update && sudo apt upgrade -y
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

Add swap if the host has none (`free -h` shows `Swap: 0B`): four workers
processing at once can briefly need more than the idle numbers above.

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 2. Firewall, before Docker

Install and configure `ufw` **before** installing Docker, and allow SSH
**before** enabling it.

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing

# SSH FIRST: enabling ufw without this rule locks you out
sudo ufw allow OpenSSH          # 22/tcp

sudo ufw allow 80/tcp           # ACME HTTP-01 challenge and the HTTP->HTTPS redirect
sudo ufw allow 443/tcp          # flows over TLS
sudo ufw allow 443/udp          # HTTP/3 (Caddy publishes it; optional)

sudo ufw enable
sudo ufw status verbose
```

**Port 80 must stay open**, also after the first certificate. Caddy renews the
certificate about every 60 days and answers the ACME challenge on port 80 (or on
443 with TLS-ALPN-01). It also redirects plain HTTP to HTTPS: with 80 closed,
`http://flows.smartcitizen.me` gets a connection refused instead of a redirect.
The old deployment only opened 443, so check this on the existing server.

Outgoing traffic needs no rules: the Smart Citizen API, GitHub (metadata import, firmware for `names sync`),
PyPI (builds) and S3 are all outbound connections.

---

## 3. Install Docker

Use Docker's own repository; distro packages are often too old.

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy
newgrp docker    # or log out and back in
docker compose version    # v2.20 or newer
```

> Adding a user to the `docker` group is equivalent to giving them root. Only do
> it for accounts you would give sudo to anyway.

### Log rotation

Docker keeps container logs forever by default, and `web` writes one access log
line per request (the connector polls the API on every run). Cap them before the
first `up`, since a container keeps the log settings it was created with. In
`/etc/docker/daemon.json`, keeping any keys already there:

```json
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" }
}
```

```bash
sudo systemctl restart docker
```

On a server that is already running, recreate the containers afterwards:
`docker compose up -d --force-recreate`.

---

## 4. Why ufw alone is not enough

When Docker publishes a container port, it inserts a `DNAT` rule into iptables
that is evaluated **before** ufw's `INPUT` chain:

> `sudo ufw deny 5000` does **not** close a port Docker published on 5000.
> The traffic reaches the container before ufw sees it.

So the only safe ports are the ones compose never publishes. In `compose.yml`
**only `proxy` has `ports:`** (80 and 443). `web` (5000), `flower` (5555),
`postgres` (5432) and `redis` (6379) are only on the internal Docker network.
This matters most for Flower and Redis: neither has authentication of its own,
and Redis is the job queue.

Confirm it before every deploy:

```bash
docker compose config | grep -B 3 'published:'
```

Only `proxy` should appear. For local development, the README publishes `web`
in a `compose.override.yml`: **never copy that file to the server.** Compose
reads `compose.override.yml` automatically when it exists next to `compose.yml`.

---

## 5. Clone the repository

```bash
sudo apt install -y git
cd ~
git clone https://github.com/fablabbcn/smartcitizen-flows.git
cd smartcitizen-flows
```

Pulling later is `git pull`, see [Updating](#updating).

---

## 6. Configure `.env`

```bash
cp env.example .env
chmod 600 .env
```

`.env` is not committed and not copied into the image: `compose.yml` passes it
to the containers at runtime, so it must stay in the checkout on the server.

Generate the secrets first:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"   # FLASK_SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(24))"   # POSTGRES_PASSWORD
```

### Values that must change from `env.example`

| Variable | Set it to | Why |
|---|---|---|
| `FLASK_SECRET_KEY` | A generated value | Signs the web interface sessions and its form tokens. The example value (or the one in an old README) lets anyone forge an admin session. Changing it later signs everyone out. |
| `POSTGRES_PASSWORD` | A generated value | |
| `SQLALCHEMY_DATABASE_URI` | `postgresql+psycopg://flows:<POSTGRES_PASSWORD>@postgres:5432/flows` | **A second copy of the same password**: nothing derives one from the other. See [Troubleshooting](#troubleshooting) if `web` cannot connect. |
| `DOMAIN` | `flows.smartcitizen.me` | **No scheme.** Caddy requests the certificate for this name. Compose refuses to start without it. |
| `PUBLIC_URL` | `https://flows.smartcitizen.me` | **With** the scheme. Used in links: the API root, lists, and the `blueprint_url` inside each hardware, which the connector follows. Without it, links are built from the request as seen behind Caddy, which is `http://`. It also makes the session cookie `Secure`. |
| `BASE_POSTPROCESSING_URL` | `https://flows.smartcitizen.me/api/v1/` | **With the trailing slash.** Where smartcitizen-connector and scdata read hardware, blueprints and calibrations. Without it, processing silently reads them from GitHub instead of flows. |
| `SC_BEARER` | The API token of a Smart Citizen **admin** | Processing posts the processed data with it, and the daily sync searches devices and users with it. |
| `FLASK_DEBUG` | `0` | Flask 3 applies `1` under gunicorn too: debug mode in production. |
| `S3_DATA_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` | The backup bucket and its credentials | Backups write raw device data there. boto3 reads `AWS_DEFAULT_REGION`: an old `AWS_REGION` alone is not enough. |

### Values to keep as they are

| Variable | Value | Notes |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_DB` | `flows` | Must match the URI above. |
| `CELERY_BROKER` | `redis://redis:6379/0` | |
| `REDIS_URL` | `redis://redis:6379/1` | Locks: one run per device and task at a time. |
| `CELERY_TIMEZONE` | `Europe/Madrid` | The daily sync runs at 03:00 in this zone. |
| `FLASK_APP` | `scflows` | |

### Optional

| Variable | Default | Notes |
|---|---|---|
| `API_URL` | `https://api.smartcitizen.me/v0/` | Smart Citizen API used to verify tokens and sign in, and by the connector. Only set it to use another API, e.g. staging. |

Old variables that are no longer read and can be deleted: `CELERY_RESULTS_BACKEND`
(run results are in the database), `FLOWER_BASIC_AUTH`, `FLOWER_PORT` and
`FLASK_ENV`.

---

## 7. DNS and TLS

Point an `A` record at the server before starting Caddy, and check it resolves:

```bash
dig +short flows.smartcitizen.me
```

Certificates and the ACME account live in the `caddy_data` volume, so they survive
`docker compose down` and container recreation. Don't delete that volume casually:
Let's Encrypt allows **5 certificates per exact domain per week**.

To try the configuration without spending that limit, use the staging CA first:
add this inside the site block of `scflows/public/caddy/Caddyfile`, and remove it
(then `docker compose restart proxy`) once it works:

```
tls {
	ca https://acme-staging-v02.api.letsencrypt.org/directory
}
```

---

## 8. Flower credentials

Flower has no login of its own: Caddy puts basic auth in front of `/flower`.
Create the credentials **before the first `up`**:

```bash
scflows/public/caddy/flower_auth.sh <user> <password>
```

It writes `scflows/public/caddy/flower_auth` (one `user bcrypt-hash` line, not
committed). If the file does not exist when `proxy` starts, Docker creates a
**directory** with that name to mount instead, and Caddy fails to read it. Delete
the directory, run the script, and `docker compose up -d proxy`.

---

## 9. First boot

```bash
docker compose build
docker compose up -d
docker compose ps
```

The first build takes a few minutes (scdata and its dependencies; timezonefinder
installs from a wheel). `web` waits for Postgres to be healthy, then applies the
database migrations (`flask --app scflows db upgrade`) before serving: the logs
show `Running upgrade ...` on the first start and nothing on the next ones.

Watch Caddy get its certificate:

```bash
docker compose logs -f proxy | grep -iE "certificate|obtain|error"
```

---

## 10. Load the metadata

flows starts empty. Import the blueprints, hardware, calibrations and sensor names
from smartcitizen-data, then check that what flows serves matches the source:

```bash
docker compose exec web sh -c "git clone --depth 1 https://github.com/fablabbcn/smartcitizen-data.git /tmp/smartcitizen-data \
  && flask --app scflows metadata import /tmp/smartcitizen-data \
  && flask --app scflows metadata verify https://raw.githubusercontent.com/fablabbcn/smartcitizen-data/master/"
```

Expect `1 created` blueprints, `158 created` hardware, `594 created` calibrations,
`144 created` names, then `All served metadata matches the source`. The import is recorded in the
revision history (as `import`, with no user). Every hardware must use a blueprint
that is in flows: hardware pointing at a blueprint that is not (as the old
`SCK21NILU` did) is not imported.

Run it once. Running it again keeps what already exists; `--overwrite` replaces it
with the GitHub version and **discards the changes made in flows**. After this step
flows is the source of truth for this metadata, not the smartcitizen-data repository.

---

## 11. Verify

From your laptop, not the server:

```bash
# TLS is live, HTTP redirects to HTTPS
curl -sSI https://flows.smartcitizen.me/ | head -1
curl -sSI http://flows.smartcitizen.me/ | head -1         # 308 to https

# The API, its links and the metadata
curl -sS https://flows.smartcitizen.me/api/v1/health       # {"status": "ok"}
curl -sS https://flows.smartcitizen.me/api/v1/ | python3 -m json.tool
curl -sS https://flows.smartcitizen.me/api/v1/hardware/SCAS220013.json | python3 -m json.tool
```

The hardware shows `"blueprint": "sc_air"` and
`"blueprint_url": "https://flows.smartcitizen.me/api/v1/blueprints/sc_air.json"`.
An `http://` link there means `PUBLIC_URL` is not set.

Check token verification without changing anything, with an **admin** token:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X PUT https://flows.smartcitizen.me/api/v1/calibrations/0 \
  -H "Authorization: Bearer <admin token>" -H "Content-Type: application/json" -d "{}"
```

| Answer | Meaning |
|---|---|
| `422` | Token accepted, content invalid: everything works. |
| `403` | The token is valid but not an admin's (researchers cannot write). |
| `401` | The Smart Citizen API rejected the token. |
| `503` | The Smart Citizen API is not reachable from the server. |

The first request with a token can take **30 seconds**: flows checks it with
`GET /me`, which embeds every device an admin can see. It is then cached for an
hour.

Sign in at `https://flows.smartcitizen.me/login` with a Smart Citizen admin
account (signing in also takes up to 30 seconds). Admins land on Jobs; Metadata
lists the hardware and calibrations, and "Check" on a hardware in use reports no
errors. Researchers can sign in too and see, read only, the hardware of their own
devices.

Flower answers at `https://flows.smartcitizen.me/flower` after the basic auth
prompt, and shows the `celery` worker online.

Then a processing dry run, which processes without posting:

```bash
docker compose exec web flask --app scflows jobs run 18154 process --dry-run --inline
```

Expect `success PROCESSED AND UPLOADED` (on device 18154 the CO2 channels are
skipped: it has no SCD30). Check that it read the metadata from flows and not
from GitHub:

```bash
docker compose logs web | grep -E "GET /api/v1/(hardware/SCAS220097|blueprints/sc_air|calibrations/calibrations|names/SCDevice).json"
```

All four requests should be there. If none are, `BASE_POSTPROCESSING_URL` is not
set in `.env`, or the containers were not recreated after setting it.

Finally, confirm nothing else is exposed. From your laptop, against the server:

```bash
for p in 5000 5432 5555 6379; do
  timeout 3 bash -c "</dev/tcp/flows.smartcitizen.me/$p" 2>/dev/null \
    && echo "OPEN   $p  <-- should not be" || echo "closed $p"
done
```

---

## 12. Create the jobs

Jobs are rows in the database; `beat` queues the ones that are due. Create them
from the Smart Citizen API:

```bash
docker compose exec web flask --app scflows jobs sync
```

It creates a processing job for every device with valid postprocessing (hardware in
flows) and new readings, and a backup job for every device of a researcher with
readings: about **70 processing** and **540 backup** jobs. The first runs are
spread over the interval (3 h for processing, 6 h for backups) instead of all
starting at once. `beat` repeats the sync every day at 03:00: new devices get
jobs, and jobs of devices that no longer qualify are disabled. Jobs an admin
paused stay paused.

Check:

- `https://flows.smartcitizen.me/jobs/` lists the jobs, with their next run.
- `docker compose logs beat` shows `Sending due task dispatch-due-jobs` every
  minute.
- After a few minutes, "Latest runs" fills in. Open one to see its log.

In `/jobs/`, run a processing dry run for one device ("Run now" with "Dry run"),
open it from "Latest runs" and check it ends in `success` and shows its log.

---

## Moving from the old deployment

The server at `flows.smartcitizen.me` ran nginx, certbot, RabbitMQ and a cron
container driven by `scflows/public/tasks/tabfile.tab`. Its last deploy predates
all of this. Do these in order, then continue with steps 9-12 above:

1. **Back up** `scflows/public/tasks/` (the tabfile and the task logs) and the old
   `.env`. Optionally also `scflows/public/nginx/auth/.htpasswd`: pulling deletes it.
2. `git pull` on `main`.
3. **Update `.env`** following [step 6](#6-configure-env). Compared to the old file:
   add `DOMAIN`, `PUBLIC_URL`, `BASE_POSTPROCESSING_URL`, `POSTGRES_USER`,
   `POSTGRES_PASSWORD`, `POSTGRES_DB` and `REDIS_URL`; replace
   `SQLALCHEMY_DATABASE_URI` (it pointed at SQLite) and `CELERY_BROKER` (it
   pointed at RabbitMQ); set `FLASK_DEBUG=0` (the old file had `1`); make sure
   `FLASK_SECRET_KEY` is not the README example value; remove
   `CELERY_RESULTS_BACKEND`. The web interface users lived in the old SQLite file
   and are gone: everyone signs in with their Smart Citizen account now.
4. **Flower credentials**: [step 8](#8-flower-credentials).
5. **Open port 80** ([step 2](#2-firewall-before-docker)): the old setup only needed 443.
6. **Stop the old stack**: `docker compose down --remove-orphans`. This removes
   the old nginx, rabbitmq and cron containers (the services no longer exist in
   `compose.yml`, so a plain `down` would leave them running and holding ports 80
   and 443). Volumes are kept.
7. Build and start: [step 9](#9-first-boot). The web app and Flower are no longer
   published on ports 5000 and 5555: use `https://flows.smartcitizen.me` and `/flower`.
8. [Load the metadata](#10-load-the-metadata), [verify](#11-verify) and
   [create the jobs](#12-create-the-jobs). The crontab is not restored: `beat`
   and the jobs table replace it, and `tabfile.tab` is no longer read.
9. **Clean up the host**: delete the certbot renewal hook
   `/etc/letsencrypt/renewal-hooks/post/flows.sh` (it reloads nginx and would
   fail every renewal), optionally `sudo certbot delete --cert-name flows.smartcitizen.me`,
   and remove `scflows/public/certbot` and `scflows/public/nginx`.

---

## Day to day

### Web interface

| Who | Sees |
|---|---|
| Smart Citizen admins | Overview, **Jobs** (run now, dry run, pause and resume per device, add a job, every run with its log) and **Metadata** (create, edit, check and delete hardware and calibrations, with history). |
| Smart Citizen researchers | Overview and Metadata, **read only**, limited to the hardware used by their devices and the calibrations of its sensors. Their devices are read when they sign in: a new device shows after signing in again. |
| Everyone else | Cannot sign in. |

The API reads (`GET /api/v1/...`) are public: processing reads them without a
token. Writes need an admin token; see the README for the endpoints.

### Command line

All from the checkout on the server:

```bash
docker compose exec web flask --app scflows jobs list [--task process|backup]
docker compose exec web flask --app scflows jobs run <device> process --dry-run   # queued to the workers
docker compose exec web flask --app scflows jobs run <device> backup --inline     # runs in the web container
docker compose exec web flask --app scflows jobs sync
docker compose exec web flask --app scflows metadata verify https://raw.githubusercontent.com/fablabbcn/smartcitizen-data/master/
docker compose exec web flask --app scflows names sync --dry-run   # new sensors in the API and the firmware
```

`names sync` (without `--dry-run`) asks before each change; it needs an interactive
terminal, so run it with `docker compose exec` (not `-T`). See the README, "Sensor names".

### Logs

```bash
docker compose logs -f celery      # job runs, as they happen
docker compose logs -f beat        # what is queued, the daily sync
docker compose logs -f web         # API and interface requests, migrations
docker compose logs -f proxy       # certificates
```

Each run's own log is also stored with the run and shown in `/jobs/runs/<id>`.

---

## Updating

```bash
cd ~/smartcitizen-flows
git pull
docker compose build
docker compose up -d
```

Building one of `web`, `celery`, `beat` or `flower` builds the shared
`scflows:latest` image, so all four update together. `web` applies new database
migrations on start; nothing else is needed for an upgrade unless its pull request
says so.

Take a backup before upgrading a release with schema changes (new files in
`scflows/migrations/versions/`): migrations are not reversed automatically.
Rolling back is `git checkout <previous commit>`, `docker compose build`,
`docker compose up -d`, and restoring that backup if the schema changed.

`beat` must run as **one instance** only: never `docker compose up --scale beat=2`,
or every job is queued twice. (The per-device locks would turn the duplicates into
`ALREADY_RUNNING` aborts, but the run history fills with them.) `celery` can be
scaled.

After a rebuild, reclaim the old image and its cache:

```bash
docker image prune -f
docker builder prune -f
```

---

## Backups

Durable state lives in named volumes:

| Volume | Contents | Losing it means |
|---|---|---|
| `postgres_data` | Blueprints, hardware, calibrations, sensor names, their revision history, jobs and runs | **The metadata.** After the deploy it is edited in flows, not GitHub, so it cannot be re-imported without losing those changes. |
| `caddy_data` | TLS certificates and ACME account | Re-issuing; mind the rate limit. |
| `redis_data` | Queued tasks | Nothing that matters: `beat` queues due jobs again within a minute. |

Postgres is the one to back up. It is small (~9 MB), so a daily dump is cheap.

### Running it by hand

```bash
scripts/backup.sh
```

It writes `~/backups/flows-<date>.sql.gz` (compressed by `pg_dump`, so a failed
dump is reported instead of leaving a broken file) and deletes dumps older than
14 days. `BACKUP_DIR` and `RETENTION_DAYS` change both.

Restore into an empty database. When `web` starts it applies any migrations the
dump is missing, so an older dump also works with newer code:

```bash
docker compose stop web celery beat
docker compose exec -T postgres sh -c 'dropdb -U "$POSTGRES_USER" "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
gunzip -c ~/backups/flows-2026-10-08.sql.gz | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose up -d
```

Store the dumps off the server: a backup on the same disk is not one. `.env`
belongs in a password manager, not with the dumps.

### Daily, with cron

```bash
crontab -e
```

```cron
30 2 * * * BACKUP_DIR=/home/deploy/backups /home/deploy/smartcitizen-flows/scripts/backup.sh >> /home/deploy/backups/backup.log 2>&1
```

- Use the **absolute path**: cron does not run from the checkout.
- Cron's `PATH` is minimal. If the log says `docker: command not found`, add a
  `PATH=/usr/bin:/bin` line (wherever `which docker` points) above the job.
- Redirect stdout and stderr: without mail configured, an unredirected failure
  disappears.

Check it ran: `tail /home/deploy/backups/backup.log`.

---

## Troubleshooting

**`docker compose up` fails with `required variable DOMAIN is missing a value`.**
`DOMAIN` is not in `.env`. It is required on purpose: without it Caddy would have
no name to request a certificate for.

**Caddy won't get a certificate.**
`docker compose logs proxy`. In order of likelihood: DNS not pointing at this
server yet; port 80 or 443 closed (`sudo ufw status`), or still taken by the old
nginx container (run `docker compose down --remove-orphans`); `DOMAIN` written
with a scheme; or the Let's Encrypt rate limit hit, which the log says explicitly.

**`proxy` fails with `reading file /etc/caddy/flower_auth: is a directory`.**
`proxy` started before `flower_auth.sh` ran, and Docker created a directory in
its place. `rm -r scflows/public/caddy/flower_auth`, run the script, then
`docker compose up -d proxy`.

**`web` logs `password authentication failed for user "flows"`.**
`POSTGRES_PASSWORD` and the password inside `SQLALCHEMY_DATABASE_URI` differ. Or
`postgres_data` was initialised with an older password: Postgres only reads
`POSTGRES_PASSWORD` when the data directory is empty. Make the URI match, or set
the live password:

```bash
docker compose exec postgres psql -U flows -d postgres -c "ALTER USER flows WITH PASSWORD '<password in .env>';"
docker compose up -d web celery beat
```

**A `flask --app scflows ...` command cannot find the app or its commands.**
It was run from inside `/scflows` in the container (the image's working
directory), where `flask --app scflows` fails. `compose.yml` runs `web` from `/` for that reason; run your own
`flask` commands through `docker compose exec web ...`, which uses that working
directory.

**Signing in, or a request with a token, answers 503 or takes 30 seconds.**
flows checks every token with the Smart Citizen API (`GET /me`), which for admins
embeds every device and takes 15-30 seconds. flows waits up to 60 seconds and
caches the answer for an hour; gunicorn's 120 second timeout covers it. A 503
means the Smart Citizen API did not answer in time or is unreachable from the
server.

**Processing reads hardware from GitHub instead of flows.**
`BASE_POSTPROCESSING_URL` is unset, has no trailing slash, or the containers were
started before it was set. Fix `.env`, then `docker compose up -d celery web`
(a `restart` keeps the old environment).

**A `.env` change has no effect.**
`docker compose restart` reuses the container's existing environment. Use
`docker compose up -d <service>`, which recreates it with the new values.

**Everyone was signed out.**
`FLASK_SECRET_KEY` changed. Sessions last 12 hours anyway; sign in again.

**A researcher sees no hardware.**
None of their devices has a `postprocessing.hardware_url` naming hardware in
flows, or the device was added after they signed in: sign out and in again.

**A form answers "The form expired, reload the page".**
The page was opened in another session (or before a deploy that changed
`FLASK_SECRET_KEY`). Reload it and submit again.

**Runs end as `aborted` with `ALREADY_RUNNING`.**
A run for the same device and task was still going (or its lock had not expired:
3 hours). Expected occasionally for slow devices; many of them mean more than one
`beat` is running.

**Out of disk.**
`docker system df`, then `docker image prune -a` and `docker builder prune`.
Container logs are capped only if [log rotation](#log-rotation) was set up before
the containers were created.
