#!/bin/bash
# python flows.py auto-schedule forward checks
# python flows.py forward
exec gunicorn --workers 3 --bind 0.0.0.0:5000 -m 007 wsgi:app --error-logfile - --access-logfile -