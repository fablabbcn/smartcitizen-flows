# Smart Citizen Flows

This repository contains a _WIP_ deployable data processing application for interacting with various data streams via `scdata` (i.e. custom API [connectors](https://github.com/fablabbcn/smartcitizen-connector), file or kafka streams via [spark](https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html)).

## Tasks

Tasks are managed by the `flows.py` script. This file manages various task routines such as:

- periodic schedules
- continuous checks
- forwarding scripts

### Periodic schedules

This is done via cron jobs, managed by `scheduler.py` and CronTab, thanks to `python-crontab` (full doc [here](https://gitlab.com/doctormo/python-crontab)). The script can program tasks in a automated or manual way. If done automatically, it can schedule them `@daily`, `@hourly` and `@minute`, with optional load balancing (not scheduling them all at the same time, but randomly in low load times).

#### Start scheduling

This will schedule based on postprocessing information in the platform having a non-null value:

```
python flows.py auto-schedule
```

or (optional dry-run for checks, force-first-run and overwritting if task is already there):

```
python flows.py --dry-run --force-first-run --overwrite
```

Task status and tabfile will be saved in `~/.cache/scdata/tasks` by default. This can be changed in the config:

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

## Continuos checks

**WIP**!

## Forwarding

Forwarding is enabled between brokers. Topic reworking is supported. For now, payloads are kept. This is managed by `app/daemons/dfoward.py`

## Local deployment

### Flask app

Install requirements:

```
pip install -r requirements.txt
```

Run:

```
export FLASK_APP=app.py
flask run
```

Which will run the app in `localhost:5000`.

### Docker

For now, no `compose.yml` file is provided. You can build:

```
docker build -t scflows:latest .
```

And run:

```
docker run --name scflows -d -p 8000:5000 --rm scflows:latest
```

Which will run the app in `localhost:8000`.

If you want to run `autoschedule`, `forward`, `checks` from the beginning, you should uncomment from `app/boot.sh` the following:

```
# python flows.py auto-schedule forward checks
# python flows.py forward
```
