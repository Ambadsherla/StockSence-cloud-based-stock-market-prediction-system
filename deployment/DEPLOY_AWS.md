# Deploy StockSense on AWS (EC2 + optional S3) and get a public link

The link will be `http://<Elastic-IP>`. You create the server; the files in this folder set it up.

## 1. Create the server (AWS console)
1. EC2 -> **Launch instance**. Name: `stocksense`. Image: **Ubuntu Server 24.04 LTS**. Type: **t3.micro** (or the free-tier type shown).
2. **Key pair**: create one, download the `.pem` file, keep it safe.
3. **Network / Security group**: allow **SSH (22)** from *My IP*, and **HTTP (80)** from *Anywhere*.
4. Storage 16 GB is enough. Launch.
5. EC2 -> **Elastic IPs** -> Allocate -> **Associate** with the instance. This is your fixed address.
   (AWS charges for public IPv4 addresses; check Billing and set a budget alert. **Stop the instance when you don't need it.**)

## 2. Upload the project
On your laptop, zip the project **without** `venv/`, `__pycache__/`, `.env`, `data/users.db`. Then:
```
scp -i yourkey.pem stocksense.zip ubuntu@<Elastic-IP>:~
ssh -i yourkey.pem ubuntu@<Elastic-IP>
sudo apt-get install -y unzip && sudo mkdir -p /opt/stocksense && sudo chown ubuntu:ubuntu /opt/stocksense
unzip ~/stocksense.zip -d /opt/stocksense
```
(Make sure `/opt/stocksense/backend/app.py` exists; if the zip made an extra folder, move its contents up.)

## 3. Create the server's `.env`
```
nano /opt/stocksense/.env
```
Paste your values (never share this file):
```
GEMINI_API_KEY=...
UPSTOX_API_KEY=...
UPSTOX_API_SECRET=...
UPSTOX_REDIRECT_URI=...
UPSTOX_ACCESS_TOKEN='...'
SECRET_KEY=<long random text>
SIGNUP_CODE=<an invite code only you know>
S3_BUCKET=<optional bucket name>
AWS_REGION=<your region>
```
Do NOT put AWS access keys here; use the IAM role in step 6.

## 4. Install and start
```
cd /opt/stocksense && sudo bash deployment/setup_ec2.sh
/opt/stocksense/venv/bin/python data_ingestion/run_pipeline.py      # builds data + models (a few minutes)
sudo systemctl restart stocksense
```

## 5. Open the link
Go to `http://<Elastic-IP>/` -> **Create account** (enter your SIGNUP_CODE) -> dashboard.
Check: `http://<Elastic-IP>/healthz` shows `ok`. Logs: `sudo journalctl -u stocksense -n 50`.

## 6. S3 backup (optional)
1. S3 -> Create bucket (private), turn on **Versioning**.
2. IAM -> Roles -> Create role for **EC2** -> policy allowing `s3:PutObject`, `s3:GetObject`, `s3:ListBucket` on that bucket only.
3. EC2 -> your instance -> Actions -> Security -> **Modify IAM role** -> pick it.
4. Put `S3_BUCKET` in `.env`, restart. After each daily update the CSVs, results and snapshots are copied to `s3://<bucket>/stocksense/latest/`.
5. Your local `.env` has AWS access keys: **rotate them** in IAM (Security credentials) if they were ever shared, and keep them off the server.

## 7. Upstox daily token on the server
The Upstox login needs a browser, so do it on your laptop (`python realtime/upstox_auth.py`), then on the server
run `nano /opt/stocksense/.env` and replace the `UPSTOX_ACCESS_TOKEN` line. No restart is needed.

## 8. HTTPS (recommended before real use)
Plain HTTP sends passwords unencrypted. With a domain pointing to the Elastic IP:
```
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
```
Then add `COOKIE_SECURE=1` to `.env` and `sudo systemctl restart stocksense`.
Until then, do not reuse a real password on this site.

## Update later
Upload changed files with `scp`, then `sudo systemctl restart stocksense`.