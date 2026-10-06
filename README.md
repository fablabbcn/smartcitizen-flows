# Smart Citizen Flows

This repository contains a data processing application for loading, transforming and pushing data from various data streams from [scdata](https://github.com/fablabbcn/smartcitizen-data). `scdata` can connect to various custom API connectors (such as [smartcitizen-connector](https://github.com/fablabbcn/smartcitizen-connector)) among others. This data processing application allows to create tasks to process data, using periodic schedules, and workers based on [celery](https://docs.celeryq.dev/en/stable/getting-started/introduction.html). A small [flask](https://flask.palletsprojects.com/en/3.0.x/) application is used to manage the tasks, with a [gunicorn](https://gunicorn.org/) server.

![](assets/flows.png)

## Tasks

Tasks are managed by the `flows.py` script. This file manages various task routines such as periodic schedules (via cron). The script can program tasks in a automated or manual way. If done automatically, it can schedule them `@daily`, `@hourly` and `@minute`, with optional load balancing (not scheduling them all at the same time, but randomly in low load times).

In addition, you can use a task queue with workers with [celery](https://docs.celeryq.dev/en/stable/getting-started/introduction.html).

#### Start scheduling

This will schedule based on postprocessing information in the platform having a non-null value:

```
python flows.py auto-schedule
```

Or with `celery` as an `backend` for data processing:

```
python flows.py auto-schedule --celery
```

For this option to work, you need to lunch `celery` and a `message` broker. In this case, we use [rabbitmq](https://www.rabbitmq.com/) as a broker. You can launch the `message` broker via the provided `docker` containers, or on your own. The celery app can be run with:

```
cd scflows
celery --app worker:app worker -l info
```

If you want to `dry-run` for checking if your workflow works, `force-first-run` and `overwrite` the tasks:

- For running processing tasks:
```
python flows.py auto-schedule --task process --dry-run --force-first-run --overwrite
```

- For running backup tasks:
```
python flows.py auto-schedule --task backup --dry-run --force-first-run --overwrite
```

#### Logs

Logs are stored in the `public/tasks` directory, as a volume in `docker` as well. Otherwise, the `flask` app can access those logs.

```
➜  tasks tree -L 2
.
├── 13238
│   └── 13238.log
├── 13486
├── README.md
├── scheduler.log
└── tabfile.tab
```

#### Manual scheduling

This will schedule a device regardless the auto-scheduling:

```
python flows.py manual-schedule --device <device> --dry-run --force-first-run --overwrite
```

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
# CELERY
CELERY_BROKER=amqp://guest:guest@rabbitmq:5672//
CELERY_RESULTS_BACKEND=rpc://
CELERY_TIMEZONE=Europe/Madrid
FLOWER_PORT=5555
# FLASK
FLASK_ENV=production
FLASK_APP=scflows
FLASK_DEBUG=1
SQLALCHEMY_DATABASE_URI=sqlite:///db.sqlite
FLASK_SECRET_KEY=change-me
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

You can run `celery` with

```
celery --app worker:app worker -l info
```

#### Flower

[Flower](https://flower.readthedocs.io/en/latest/) is a front-end application for monitoring and managing `celery` clusters. There is a docker container for it to work, or you can run it by:

```
celery flower -l info -app worker:tasks
```

![](assets/flower.png)

Note that you need to add the [url-prefix](https://flower.readthedocs.io/en/latest/config.html#url-prefix) to run behind the proxy:

```
celery flower -l info -app worker:tasks -url-prefix=flower
```

In addition, you will need to protect `flower` when running behind the proxy (see [Deploying](#deploying)). Create the basic auth credentials (the file is not committed):

```
scflows/public/caddy/flower_auth.sh <user> <password>
```

More info in the [flower docs](https://flower.readthedocs.io/en/latest/auth.html).

### Running with Docker

You can build:

```
docker compose build -t scflows:latest .
```

And run:

```
docker compose up -d rabbitmq flows celery flower web
```

Which will run the `flask` app in `localhost:5000` and `flower` in `localhost:5555`. The `flows` container runs `cron`, which does not inherit the container environment: its entrypoint writes it to `/etc/environment` on start. You can jump into the flows `docker` `flows` container and run the `auto-schedule`, to start processing tasks.

```
doco exec -it flows bash
```

You should see in the IP address. Then:

```
python flows.py auto-schedule --celery
```

### Deploying

The `proxy` service runs [Caddy](https://caddyserver.com/) with `scflows/public/caddy/Caddyfile`. It requests and renews the TLS certificates for `DOMAIN` (set in `.env`) automatically, redirects HTTP to HTTPS and protects `/flower` with basic auth. Ports 80 and 443 must be reachable and `DOMAIN` must point to the server.

```
scflows/public/caddy/flower_auth.sh <user> <password>
docker compose up -d proxy
```

Certificates are stored in the `caddy_data` volume.
