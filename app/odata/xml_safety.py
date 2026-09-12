"""Make a value safe to put in an XML document.

WHY THIS EXISTS
---------------
`xml.etree.ElementTree` escapes the five markup characters and nothing else.
It will happily write a NUL, a lone surrogate or a U+FFFE straight into an
attribute value, producing bytes that satisfy no XML parser on earth --
`DOMParser` hands the caller a `<parsererror>` document, `expat` raises "not
well-formed (invalid token)". There is no escape hatch to reach for either:
XML 1.0 has no representation for a C0 control, so a numeric character
reference to one is illegal too. The character cannot be carried, only
replaced.

The values that reach us are not ours. A column's text bounds arrive from the
catalog as the leading bytes of real stored values
(`Custom.Statistics.MinPrefix`/`MaxPrefix`), and a dataset holding arbitrary
payloads -- JSON blobs, compressed fields, anything binary-ish written to a
VARCHAR -- has bounds full of control bytes. `public.github.events` is the
case that found this: its `payload`, `actor_display_login` and
`actor_gravatar_id` prefixes carried C0 controls, so the whole $metadata
document was unparseable and every client reading it failed whole -- not
degraded, failed, because a parse error is not per-annotation. Commit
messages, authors and tag names are the same kind of value; they simply have
not been unlucky yet.

WHAT IT DOES
------------
Anything outside the XML 1.0 `Char` production becomes U+FFFD -- replaced,
not stripped, so a prefix keeps its length and the positions of the characters
that ARE representable, and so a reader can see that something was there. That
is also what the same bytes already look like when they fail to decode as
UTF-8 upstream, so one unreadable character looks the same however it got that
way.

Applied as a pass over the finished tree rather than at each `SubElement`
call. Both metadata routes build their documents from dozens of scattered
annotation sites; a sanitizer that has to be remembered at each one is a
sanitizer that will be forgotten at the next one added.
"""

import re
import xml.etree.ElementTree as ET

# The complement of XML 1.0's Char production (section 2.2):
#   #x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] | [#x10000-#x10FFFF]
# Note what is NOT here: DEL (#x7F) and the rest of C1 are legal XML 1.0
# characters and are left alone. Surrogates are not -- a lone #xDCxx is what a
# `surrogateescape` decode upstream leaves behind, and it would fail at UTF-8
# encoding time (a 500) rather than at parse time.
# Written from ordinals rather than escapes so the ranges can be read as the
# spec states them, and so nothing here depends on a literal control
# character surviving a round trip through an editor.
_XML_CHAR_RANGES = (
    (0x09, 0x09),  # tab
    (0x0A, 0x0A),  # line feed
    (0x0D, 0x0D),  # carriage return
    (0x20, 0xD7FF),
    (0xE000, 0xFFFD),
    (0x10000, 0x10FFFF),
)

_NOT_XML_CHAR = re.compile(
    "[^" + "".join(chr(lo) + "-" + chr(hi) for lo, hi in _XML_CHAR_RANGES) + "]"
)

REPLACEMENT = chr(0xFFFD)


def xml_safe_text(value: str) -> str:
    """`value` with every character XML cannot carry replaced by U+FFFD."""
    return _NOT_XML_CHAR.sub(REPLACEMENT, value)


def sanitize_xml_tree(element: ET.Element) -> ET.Element:
    """Rewrite `element` in place so the whole tree can be serialized.

    Attribute VALUES, text and tails only. Attribute and element NAMES are
    this service's own constants, and a name that needed sanitizing would be a
    bug in the document's shape rather than in one dataset's contents -- worth
    failing on, not papering over.

    Returns the element it was given, so it can wrap a `tostring` argument.
    """
    for node in element.iter():
        for name, value in node.attrib.items():
            if isinstance(value, str):
                cleaned = xml_safe_text(value)
                if cleaned != value:
                    node.attrib[name] = cleaned
        if node.text:
            node.text = xml_safe_text(node.text)
        if node.tail:
            node.tail = xml_safe_text(node.tail)
    return element
