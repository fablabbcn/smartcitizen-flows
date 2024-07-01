FROM python:3.11

RUN apt-get update && apt-get -y install cron nano

COPY .env /etc/environment
COPY requirements.txt requirements.txt
COPY scflows scflows
COPY setup.py setup.py

RUN pip install -r requirements.txt
RUN pip install -e .

WORKDIR /scflows