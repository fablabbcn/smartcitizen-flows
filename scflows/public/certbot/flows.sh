#!/bin/bash
set -eou pipefail

DOMAIN='flows.smartcitizen.me'

echo 'Copying certificates to volume...'
cp -L -r /etc/letsencrypt/live/$DOMAIN/*.pem /root/smartcitizen-flows/scflows/public/certbot/www/
cp /etc/letsencrypt/ssl-dhparams.pem /root/smartcitizen-flows/scflows/public/certbot/www/
cp /etc/letsencrypt/options-ssl-nginx.conf /root/smartcitizen-flows/scflows/public/certbot/www/

# Gracefully reload nginx after
echo 'Reloading nginx...'
docker exec -it smartcitizen-flows-proxy-1 nginx -s reload
echo 'Done'
