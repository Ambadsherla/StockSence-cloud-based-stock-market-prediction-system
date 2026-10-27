"""One command to update everything: python data_ingestion/run_pipeline.py"""
import os
import subprocess
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, '.env'))
sys.path.append(os.path.join(BASE_DIR, 'data_ingestion'))
sys.path.append(os.path.join(BASE_DIR, 'model'))


def full_update():
    from refresh_data import refresh_data
    from train_model import train_all

    report = refresh_data()
    print("Refresh:", report)
    if all("error" in v for v in report.values()):
        raise RuntimeError("Data refresh failed for every ticker; keeping the old models.")
    train_all()
    subprocess.run([sys.executable, os.path.join('backtesting', 'backtester.py')], cwd=BASE_DIR, check=True)
    if os.getenv("S3_BUCKET"):                 # optional AWS S3 backup of data, results and snapshots
        try:
            from s3_sync import sync_to_s3
            print("S3:", sync_to_s3())
        except Exception as exc:
            print("S3 sync skipped:", exc)
    return report


if __name__ == "__main__":
    full_update()
    print("PIPELINE COMPLETE: data refreshed, models retrained, backtest rebuilt.")