import os
import sys

sys.path.insert(1, os.path.join(sys.path[0], "../../pyiceberg-firestore-gcs"))

from opteryx_catalog import OpteryxCatalog

FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE")
BUCKET_NAME = os.environ.get("GCS_BUCKET")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

workspace = OpteryxCatalog(
    "public",
    firestore_project=GCP_PROJECT_ID,
    firestore_database=FIRESTORE_DATABASE,
    gcs_bucket=BUCKET_NAME,
)

dataset = workspace.load_dataset("examples.planets")

schema = dataset.schema()
snapshot = dataset.snapshot()
description = dataset.describe()

print(description)