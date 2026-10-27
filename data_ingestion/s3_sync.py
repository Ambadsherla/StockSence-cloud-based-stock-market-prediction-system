"""Back up data, model results and option snapshots to AWS S3 (runs after each full update if S3_BUCKET is set).
On EC2 attach an IAM role with access to the bucket; no AWS keys are needed on the server."""
import glob
import os

import boto3

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PATTERNS = ["data/processed/*.csv", "model/results/ml_summary.csv",
            "backtesting/results/backtest_summary.csv", "realtime/snapshots/*.csv"]


def sync_to_s3(bucket=None, prefix="stocksense/latest"):
    bucket = bucket or os.getenv("S3_BUCKET")
    if not bucket:
        raise RuntimeError("S3_BUCKET is not set")
    s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION") or None)
    count = 0
    for pattern in PATTERNS:
        for path in glob.glob(os.path.join(BASE_DIR, pattern)):
            key = f"{prefix}/{os.path.relpath(path, BASE_DIR).replace(os.sep, '/')}"
            s3.upload_file(path, bucket, key)
            count += 1
    return f"uploaded {count} files to s3://{bucket}/{prefix}/"


if __name__ == "__main__":
    print(sync_to_s3())