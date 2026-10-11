# Smart Citizen Flows

Data processing for the [Smart Citizen](https://smartcitizen.me) platform. Flows processes and backs up the data of devices in the Smart Citizen API periodically, with [scdata](https://github.com/fablabbcn/smartcitizen-data) and [smartcitizen-connector](https://github.com/fablabbcn/smartcitizen-connector), and serves the processing metadata (blueprints, hardware, calibrations). It is a [flask](https://flask.palletsprojects.com) application with [celery](https://docs.celeryq.dev) workers.

![](assets/flows.png)

## Jobs

Each device has a job per task: `process` (calculate channels from the blueprint and post them to the Smart Citizen API, every 3 hours) and `backup` (store the data in S3, every 6 hours). Jobs are stored in the database:

- `celery beat` queues the jobs that are due every minute, and syncs the jobs with the Smart Citizen API every day. Which jobs a device gets depends on the blueprints of its hardware (see Blueprint kinds): `process` if its hardware has a process blueprint and it has new readings; `backup` if its hardware has a long blueprint, or if it belongs to a researcher and its hardware has a backup blueprint or it has no hardware in flows. Jobs that do not qualify anymore are disabled; jobs paused by admins stay paused
- `celery` workers run them. A device task never runs twice at the same time (lock in Redis)
- Each run is recorded with its result and log

Admins see the jobs and runs in `/jobs/`, or in the API (`/api/v1/jobs`, `/api/v1/runs/<id>`). From the command line:

```
flask --app scflows jobs sync                                     # sync with the Smart Citizen API now
flask --app scflows jobs list [--task process|backup]
flask --app scflows jobs run <device> process --dry-run [--inline] # run now (--inline: here, not in the workers)
```

## Blueprint kinds

Each blueprint has a kind (`meta.kind`, `process` when missing) and each hardware lists one or two blueprints, at most one of each kind:

- `process`: processed every few hours on the latest readings from the Smart Citizen API (up to 1000), with its health checks, and posted back
- `long`: processed on a long window of the device's backups (`window_days`, every `every_days`), with baselines that need months of data. Results stay in S3 and flows. The device is always backed up
- `backup`: the device is only backed up (devices of researchers)

`long` and `backup` do not go together: long processing already backs up. The hardware served to smartcitizen-connector keeps a single `blueprint` and `blueprint_url` (the process blueprint, or the only one) and lists all of them in `blueprints`.

## Device health

Every processing run also runs the checks of the device's blueprint (`checks` in the blueprint: gaps, implausible values, flat values, outliers) on the data it processed, and flows stores the result. Each column gets a status: `ok` (nothing flagged), `warning` (part of the readings flagged, or of the time for gaps) or `problem` (20% or more, `PROBLEM_RATIO` in `scflows/health.py`); a check that cannot run (e.g. wrong settings) is an `error`. The device takes the worst status. Records are kept for 30 days (`KEEP_DAYS`), always keeping the latest of each device; `beat` deletes older ones every day at 03:30.

Admins see every device in `/health/`, researchers the devices they own (read from their Smart Citizen account when they sign in). Each device page shows its recent runs and, per check, the columns flagged and when. The same in the API, with a Smart Citizen token of an admin or researcher:

| Endpoint | Content |
|---|---|
| `GET /api/v1/devices/health` | Latest health of each device, with its worst issues |
| `GET /api/v1/devices/<id>/health[?limit=50]` | Latest health of a device in full, and its history |

## Processing metadata

Flows serves the processing metadata (blueprints, hardware, calibrations and sensor names) that used to live as json files in [smartcitizen-data](https://github.com/fablabbcn/smartcitizen-data). The paths follow the layout of that repository, so `https://<host>/api/v1/` can be used as the base url by `smartcitizen-connector` (`BASE_POSTPROCESSING_URL`) and `scdata`.

| Endpoint | Content |
|---|---|
| `GET /api/v1/` | Links to the endpoints |
| `GET /api/v1/blueprints` | List of blueprints |
| `GET /api/v1/blueprints/<name>[.json]` | Blueprint |
| `GET /api/v1/hardware` | List of hardware |
| `GET /api/v1/hardware/<name>[.json]` | Hardware, as in `hardware/<name>.json`, plus `blueprint` (name). `blueprint_url` links to the blueprint in flows |
| `GET /api/v1/calibrations` (or `/calibrations/calibrations.json`) | All calibrations. Filter with `?kind=alphasense_sensor` or `?kind=afe_board` |
| `GET /api/v1/calibrations/<sensor_id>` | Calibration of a sensor or board |
| `GET /api/v1/names` (or `/names/SCDevice.json`) | Sensor names, in order: the name scdata and the blueprints give each Smart Citizen sensor id |
| `GET /api/v1/names/<name>` | One sensor name |
| `GET /api/v1/health` | Health check |

Links use `PUBLIC_URL` (e.g. `https://flows.smartcitizen.me`), or the request host when it is not set. The data is stored in PostgreSQL. Apply the database migrations with `flask --app scflows db upgrade` (the `web` container does it on start). Load the data from a smartcitizen-data checkout, and check that what is served matches it. The `postgres` host of `SQLALCHEMY_DATABASE_URI` is only reachable inside the compose network: run these commands in the `web` container (`docker compose exec web flask --app scflows ...`), or point the URI to a database reachable from where they run.

```
git clone --depth 1 https://github.com/fablabbcn/smartcitizen-data.git /tmp/smartcitizen-data
flask --app scflows metadata import /tmp/smartcitizen-data
flask --app scflows metadata verify /tmp/smartcitizen-data
flask --app scflows metadata verify https://raw.githubusercontent.com/fablabbcn/smartcitizen-data/master/
```

`import` keeps items that already exist, unless `--overwrite` is passed. `verify` compares hardware by blueprint name, as flows links its own blueprints instead of the GitHub urls. Every hardware must use a blueprint in flows: import blueprints first, hardware whose blueprint is not in flows is not imported.

### Editing metadata

Reading metadata is public (processing reads it without a token). Admins of the Smart Citizen platform create, update and delete it, using their Smart Citizen API token (`Authorization: Bearer <token>`). Flows checks the token with `GET {API_URL}me` (`API_URL` defaults to `https://api.smartcitizen.me/v0/`) and caches it for an hour. That call returns all the devices visible to the user: for admins it can take 30 seconds, so the first request with a token is slow.

| Endpoint | Who | |
|---|---|---|
| `PUT /api/v1/blueprints/<name>` | admin | Create or replace a blueprint (validated with `scdata`) |
| `PUT /api/v1/hardware/<name>` | admin | Create or replace a hardware description, same structure as the hardware files. List its blueprints with `blueprints` (names of blueprints in flows), or give one with `blueprint` or `blueprint_url` |
| `PUT /api/v1/calibrations/<sensor_id>` | admin | Create or replace a calibration (Alphasense sensor or AFE board) |
| `PUT /api/v1/names/<name>` | admin | Create or replace a sensor name. New names go to the end of the list |
| `DELETE /api/v1/<blueprints\|hardware\|calibrations\|names>/<name>` | admin | Delete |
| `POST /api/v1/hardware/<name>/check` | anyone | Check a hardware description without saving it |
| `GET /api/v1/<blueprints\|hardware\|calibrations\|names>/<name>/revisions` | anyone | History of changes |

In the web interface, admins edit the metadata and run the jobs. Researchers sign in to see, read only, the hardware used by their devices (from the `postprocessing` of their devices when they sign in) and the calibrations of its sensors.

Hardware is checked before saving. Errors reject it: invalid structure or dates, blueprint not in flows, unknown slots or Alphasense sensor codes, overlapping versions. Warnings are returned with the saved item: sensors without calibration, slots without channels in the blueprint. Blueprints used by hardware cannot be deleted.

```
curl -X PUT https://flows.smartcitizen.me/api/v1/calibrations/212830246 \
  -H "Authorization: Bearer $SC_TOKEN" -H "Content-Type: application/json" \
  -d @calibration.json
```

### Sensor names

scdata renames the readings of each sensor id with the names in `names/SCDevice.json`, and the blueprints use those names. The Smart Citizen API does not know them (sensor 55 is "Sensirion SHT31 - Temperature" there, `TEMP` here), and the SCK firmware (`lib/Sensors/Sensors.h`) names some sensors differently from what the blueprints use, so flows keeps the list. To bring in new sensors:

```
flask --app scflows names sync --dry-run
flask --app scflows names sync
```

It compares the names with the sensors of the Smart Citizen API and the firmware, and asks before each change: new names for firmware sensors with an id and no name, and the id of names that have none. It never renames nor changes units (scdata converts readings with them). Different names for the same id, ids not in the API and names without id are listed to review by hand, with the blueprints that use each name. `--yes` applies everything, `--firmware <path or url>` reads another `Sensors.h`. Changes are recorded in the history as `names sync`. Names can also be edited with `PUT /api/v1/names/<name>` (`{"id": 258, "description": "SCD4X CO2", "unit": "ppm"}`, admin) and `DELETE`.

## Local deployment

You can deploy via `docker` or by running the different components separately.

### Installation

```
pip install -e ".[dev]"
```

### Environment variables

Copy `env.example` to `.env` and fill it in. `.env` is never committed nor copied into the docker image: `compose.yml` loads it at runtime.

```
#TOKENS
SC_BEARER=sc-bearer
# CELERY (broker) and locks: redis service
CELERY_BROKER=redis://redis:6379/0
REDIS_URL=redis://redis:6379/1
CELERY_TIMEZONE=Europe/Madrid
# FLASK
FLASK_APP=scflows
FLASK_DEBUG=0
# Keep the user, password and database in line with POSTGRES_* below
SQLALCHEMY_DATABASE_URI=postgresql+psycopg://flows:change-me@postgres:5432/flows
FLASK_SECRET_KEY=change-me
PUBLIC_URL=https://flows.smartcitizen.me
# POSTGRES
POSTGRES_USER=flows
POSTGRES_PASSWORD=change-me
POSTGRES_DB=flows
# BACKUPS
S3_DATA_BUCKET=bucket-name
AWS_ACCESS_KEY_ID=key-id
AWS_SECRET_ACCESS_KEY=secret-key
AWS_DEFAULT_REGION=eu-west-1
```

Generate `FLASK_SECRET_KEY` with `python -c "import secrets; print(secrets.token_hex(32))"`.

### Tests

```
pytest
```

### Flask app

Run the app, either directly with flask:

```
flask run
```

Which will run the app in `localhost:5000`. Or with `gunicorn`:

```
gunicorn --workers 1 --bind 0.0.0.0:5000 -m 007 'scflows:create_app()' --error-logfile - --access-logfile -
```

### Celery workers

```
celery --app scflows.worker:app worker -l info
celery --app scflows.worker:app beat -l info   # only one instance
```

#### Flower

[Flower](https://flower.readthedocs.io/en/latest/) monitors the celery workers. It runs behind the proxy under `/flower`, protected with basic auth. Create the credentials (the file is not committed):

```
scflows/public/caddy/flower_auth.sh <user> <password>
```

![](assets/flower.png)

### Running with Docker

```
docker compose build
docker compose up -d
```

Services: `postgres`, `redis`, `web` (flask app and API), `celery` (workers), `beat` (schedule), `flower` and `proxy` (Caddy). Only the proxy publishes ports (80 and 443). For local use without the proxy, publish the web app in a `compose.override.yml`:

```
services:
  web:
    ports:
      - "5000:5000"
```

Then load the metadata and create the jobs:

```
docker compose exec web flask --app scflows metadata import <smartcitizen-data checkout>
docker compose exec web flask --app scflows jobs sync
```

### Deploying

See [docs/deploy.md](docs/deploy.md): server setup, `.env`, first boot, loading the metadata, verifying, moving from the old deployment, updates, backups (`scripts/backup.sh`) and troubleshooting.

The `proxy` service runs [Caddy](https://caddyserver.com/) with `scflows/public/caddy/Caddyfile`. It requests and renews the TLS certificates for `DOMAIN` (set in `.env`) automatically, redirects HTTP to HTTPS and protects `/flower` with basic auth. Ports 80 and 443 must be reachable and `DOMAIN` must point to the server.

```
scflows/public/caddy/flower_auth.sh <user> <password>
docker compose up -d proxy
```

Certificates are stored in the `caddy_data` volume.
