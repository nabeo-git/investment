import boto3
import yaml
from config_schema import Config


def load_config(bucket: str, key: str = "config.yaml") -> Config:
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"].read()
    return Config.model_validate(yaml.safe_load(body))
