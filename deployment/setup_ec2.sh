#!/usr/bin/env bash
# Run ON the EC2 server (Ubuntu 24.04), after the project is in /opt/stocksense:
#   cd /opt/stocksense && sudo bash deployment/setup_ec2.sh
set -euo pipefail
cd /opt/stocksense
apt-get update -y
apt-get install -y python3-venv python3-pip nginx
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
chown -R ubuntu:ubuntu /opt/stocksense
cp deployment/stocksense.service /etc/systemd/system/stocksense.service
cp deployment/nginx.conf /etc/nginx/sites-available/stocksense
ln -sf /etc/nginx/sites-available/stocksense /etc/nginx/sites-enabled/stocksense
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl enable --now stocksense
systemctl restart nginx
echo "Done. Open http://<your-server-ip>/"