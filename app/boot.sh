#!/bin/bash
# python flows.py auto-schedule forward checks
# python flows.py forward
flask run --host 0.0.0.0
# exec gunicorn -b :5000 --acces-logfile - -error-logfile - wsgi:app
exec gunicorn --workers 3 --bind :5000 -m 007 wsgi:app --acces-logfile - -error-logfile -