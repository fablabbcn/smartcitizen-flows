FROM python:3.9

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY app app
WORKDIR app
RUN chmod a+x boot.sh

ENV FLASK_APP app.py

EXPOSE 5000
ENTRYPOINT ["./boot.sh"]