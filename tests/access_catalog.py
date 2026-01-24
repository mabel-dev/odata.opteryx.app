import os
import sys

sys.path.insert(1, os.path.join(sys.path[0], "../../pyiceberg-firestore-gcs"))

from opteryx_catalog import OpteryxCatalog

FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE")
BUCKET_NAME = os.environ.get("GCS_BUCKET")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

workspace = OpteryxCatalog(
    "opteryx",
    firestore_project=GCP_PROJECT_ID,
    firestore_database=FIRESTORE_DATABASE,
    gcs_bucket=BUCKET_NAME,
)

dataset = workspace.load_dataset("ops.varlog")

schema = dataset.schema()
snapshot = dataset.snapshot()
description = dataset.describe()

print("=" * 80, "\nSCHEMA\n", "=" * 80)
print(schema)
print("=" * 80, "\nSNAPSHOT\n", "=" * 80)
print(snapshot)
print("=" * 80, "\nDESCRIPTION\n", "=" * 80)
print(description)
