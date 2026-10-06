FROM python:3.11

RUN apt-get update && apt-get -y install cron nano

COPY pyproject.toml README.md LICENSE ./
COPY scflows scflows

RUN pip install --upgrade pip && pip install -e .

COPY docker/cron-entrypoint.sh /usr/local/bin/cron-entrypoint.sh

WORKDIR /scflows
