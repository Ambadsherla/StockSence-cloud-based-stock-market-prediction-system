import boto3
import os
from dotenv import load_dotenv

# Load AWS credentials from .env file
load_dotenv()

AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION")

# Connect to S3
s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)

BUCKET_NAME = "stock-prediction-ambadas-2026-mca"  # must be globally unique

def create_bucket():
    try:
        s3.create_bucket(
            Bucket=BUCKET_NAME,
            CreateBucketConfiguration={'LocationConstraint': AWS_REGION}
        )
        print(f"Bucket '{BUCKET_NAME}' created successfully!")
    except Exception as e:
        print(f"Error creating bucket: {e}")

def upload_file(file_path, s3_key):
    try:
        s3.upload_file(file_path, BUCKET_NAME, s3_key)
        print(f"Uploaded {file_path} to S3 as {s3_key}")
    except Exception as e:
        print(f"Error uploading file: {e}")

if __name__ == "__main__":
    create_bucket()
    
    # Upload all CSV files from data folder
    for file in os.listdir("data"):
        if file.endswith(".csv"):
            upload_file(f"data/{file}", f"raw-data/{file}")