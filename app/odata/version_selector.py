"""Parse the `@label` snapshot-version selector on an OData dataset path segment.

A dataset path segment may carry a version selector: `dataset@label`, where
`label` is `current`, `previous`, a tag name, or a snapshot id. This mirrors
`VERSION AS OF` in opteryx's SQL grammar (see
opteryx/planner/logical_planner/logical_planner.py) — this module produces
the same `version`/`version_tag` values that grammar resolves to, so the two
surfaces agree on what a label means.
"""

from typing import Optional
from typing import Tuple


def split_dataset_label(raw: str) -> Tuple[str, Optional[str]]:
    """Split 'dataset@label' into (dataset, label). No '@' -> (raw, None)."""
    name, sep, label = raw.partition("@")
    if not sep:
        return raw, None
    if not name or not label:
        raise ValueError("invalid version selector: expected dataset@label")
    return name, label


def label_to_version_kwargs(label: Optional[str]) -> dict:
    """Map a parsed label to the version/version_tag kwargs opteryx's
    VERSION AS OF already understands (LogicalPlanNode.version / .version_tag,
    opteryx/planner/binder/dataset.py)."""
    if label is None:
        return {}
    lowered = label.lower()
    if lowered == "current":
        return {"version_tag": "current"}  # resolves with no catalog lookup
    if lowered == "previous":
        return {"version": 0}  # engine's sentinel for VERSION AS OF PREVIOUS
    if label.isdigit():
        return {"version": int(label)}
    return {"version_tag": label}  # tag name; catalog normalizes casing
