#!/bin/sh
# Cron jobs do not inherit the container environment: expose the runtime
# environment (compose env_file) through /etc/environment
set -eu
printenv | grep -v -E '^(_|HOME|HOSTNAME|PWD|SHLVL|TERM)=' > /etc/environment
exec cron -f
