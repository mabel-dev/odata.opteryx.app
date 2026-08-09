"""Rendering of catalog column statistics for the ``Custom.Statistics.*`` annotations.

Two things stand between ``SimpleDataset.describe()`` and an annotation a client
can believe:

**The missing-bound sentinel.** Manifest entries carry one int64 slot per column
for the min and the max, and a column the stats pass computed no bound for gets
``-(1 << 63)`` in that slot rather than a null (see ``NULL_FLAG`` in
``opteryx_catalog/catalog/manifest.py``). ``describe()`` passes that value
through untouched, so "no bound" and "the bound is int64's floor" arrive here
looking identical. Every unsigned column is in that state today -- the stats
pass only bounds the categories in ``_COMPRESSIBLE_CATEGORIES``, which is signed
integers, decimals, floats, temporals, BOOL and the string family -- so without
:func:`usable_bound` an IPv4 or UINT32 property advertises a min and max of
-9223372036854775808.

**IPv4's physical type.** An IPv4 column is a UINT32 carrying a logical
descriptor; rugo renders its rows as dotted-decimal (``ej_ipv4``) but its
bounds are the raw 32-bit integers. Emitting those verbatim would contradict
the payload the same property serves, so they are rendered back into addresses
here, along with the smallest CIDR block that encloses the pair -- which is the
form an address range is actually read in, and degrades to ``0.0.0.0/0`` when
the column spans the space.
"""

from .edm_types import extract_base_type

#: The int64 slot value meaning "this file offered no bound for this column".
#:
#: Mirrors ``opteryx_catalog.catalog.manifest.NULL_FLAG``, deliberately by
#: value rather than by import: it is a wire constant of the manifest format,
#: read out of documents written by past and future writer versions, so it
#: cannot follow whatever the installed catalog package happens to define.
#:
#: A signed column whose true minimum is exactly int64's floor is
#: indistinguishable from an absent bound and loses its annotation. That
#: ambiguity is the manifest format's, not this module's, and it costs one
#: annotation on a column no real dataset has.
MISSING_BOUND = -(1 << 63)

#: ``Custom.OriginalType`` spellings whose bounds are 32-bit address integers.
_IPV4_SOURCE_TYPES = frozenset({"IPV4", "INET"})

_UINT32_MAX = (1 << 32) - 1


def usable_bound(value):
    """``value`` if it is a real bound, else None.

    None means "this column has no bound to report", which is emitted as the
    absence of the annotation -- the same thing a client sees for a column
    whose statistics were never computed. There is no CSDL null to emit
    instead, and a present annotation is a claim about the data.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # bool is an int subclass; a boolean bound is not a number we render.
        return value
    if isinstance(value, int) and value == MISSING_BOUND:
        return None
    return value


def is_ipv4_source(source_type) -> bool:
    """Whether ``Custom.OriginalType`` names a column whose bounds are addresses."""
    if not source_type:
        return False
    return extract_base_type(str(source_type)) in _IPV4_SOURCE_TYPES


def ipv4_address(value) -> str | None:
    """A 32-bit address integer as dotted-decimal, or None if it is not one.

    Bounds reach ``describe()`` through ``_decode_minmax``, which will hand
    back a float or a string for a manifest slot written oddly. Anything that
    is not an in-range integer is not an address and is left to the caller to
    render as it sees fit, rather than being coerced into a plausible-looking
    but wrong address.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > _UINT32_MAX:
        return None
    return f"{(value >> 24) & 0xFF}.{(value >> 16) & 0xFF}.{(value >> 8) & 0xFF}.{value & 0xFF}"


def ipv4_enclosing_cidr(low, high) -> str | None:
    """The smallest CIDR block containing both bounds, or None if they are not addresses.

    The block covers the whole ``[low, high]`` range, so it is a superset of
    the addresses present: two addresses either side of a power-of-two
    boundary force a short prefix (``10.0.0.0`` with ``245.0.0.1`` gives
    ``0.0.0.0/0``), which is the honest answer -- the column's range is not
    summarisable more tightly than that. Equal bounds give a ``/32``.
    """
    if isinstance(low, bool) or isinstance(high, bool):
        return None
    if not isinstance(low, int) or not isinstance(high, int):
        return None
    if low < 0 or high < 0 or low > _UINT32_MAX or high > _UINT32_MAX:
        return None
    if low > high:
        low, high = high, low
    # The shared leading bits are the prefix; everything below the highest
    # differing bit is inside the block.
    prefix_length = 32 - (low ^ high).bit_length()
    host_bits = 32 - prefix_length
    network = (low >> host_bits) << host_bits if host_bits else low
    return f"{ipv4_address(network)}/{prefix_length}"


__all__ = [
    "MISSING_BOUND",
    "ipv4_address",
    "ipv4_enclosing_cidr",
    "is_ipv4_source",
    "usable_bound",
]
