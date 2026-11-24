import boto3
from botocore.config import Config

from .config import settings

_cfg = Config(signature_version="s3v4", s3={"addressing_style": "path"})

# Internal client talks to MinIO over the compose network.
s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint, region_name=settings.s3_region, config=_cfg)
# Presigning signs the Host header, so it must use the endpoint the *downloader* will hit.
s3_public = boto3.client("s3", endpoint_url=settings.s3_public_endpoint, region_name=settings.s3_region, config=_cfg)


def presign(key: str) -> str:
    return s3_public.generate_presigned_url(
        "get_object", Params={"Bucket": settings.exports_bucket, "Key": key},
        ExpiresIn=settings.presign_seconds,
    )
