"""Debug catalog structure to understand schema/snapshot/description formats."""

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

dataset = workspace.load_dataset("examples.vulnerabilities")

schema = dataset.schema()
snapshot = dataset.snapshot()
description = dataset.describe()

print("=" * 80)
print("SCHEMA")
print("=" * 80)
print(f"Type: {type(schema)}")
print(f"Value: {schema}")
if isinstance(schema, dict):
    for k, v in list(schema.items())[:3]:
        print(f"  {k}: {v} (type: {type(v)})")

print("\n" + "=" * 80)
print("SNAPSHOT")
print("=" * 80)
print(f"Type: {type(snapshot)}")
print(f"Value: {snapshot}")

print("\n" + "=" * 80)
print("DESCRIPTION")
print("=" * 80)
print(f"Type: {type(description)}")
if isinstance(description, dict):
    print(f"Keys: {list(description.keys())}")
    for k in list(description.keys())[:1]:
        print(f"  {k}: {description[k]} (type: {type(description[k])})")
else:
    print(f"Value: {description}")
