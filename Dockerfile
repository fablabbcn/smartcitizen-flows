FROM python:3.9

RUN apt-get update && apt-get -y install cron

COPY .env .cache/scdata/.env
COPY requirements.txt requirements.txt
COPY scflows scflows
COPY setup.py setup.py

RUN pip install -r requirements.txt
RUN python setup.py install

WORKDIR /scflows
# RUN chmod a+x boot.sh

ENV FLASK_APP app.py
# EXPOSE 5000
# ENTRYPOINT ["./boot.sh"]