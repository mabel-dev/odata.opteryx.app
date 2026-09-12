"""A $metadata document has to PARSE, whatever a column happens to contain.

The values this service puts into attributes are not its own. A text column's
bounds are the leading bytes of real stored values, and a dataset holding
arbitrary payloads has bounds full of control bytes -- `public.github.events`
is the case that found this, with C0 controls in the prefixes of `payload`,
`actor_display_login` and `actor_gravatar_id`.

XML 1.0 cannot carry those characters at all: not raw, and not as a numeric
character reference either. ElementTree does not know that -- it escapes the
five markup characters and writes everything else through -- so the document
came out ill-formed, and an ill-formed document fails WHOLE. Not one missing
annotation: `DOMParser` returns a `<parsererror>` and the client's details
panel, column list and reference popover all have nothing to read.

So the tree is swept before serialization, and these tests pin both halves of
that: the document parses, and the damage is confined to the characters that
could not be carried.
"""

import xml.etree.ElementTree as ET

import pytest

from app.odata.xml_safety import REPLACEMENT
from app.odata.xml_safety import sanitize_xml_tree
from app.odata.xml_safety import xml_safe_text
from tests.test_ipv4_statistics_annotations import get_xml_for
from tests.test_ipv4_statistics_annotations import stat_terms


class TestWhichCharactersSurvive:
    @pytest.mark.parametrize(
        "carried",
        [
            "Earth",
            "a\tb",  # tab, newline and carriage return are legal XML 1.0
            "a\nb",  # characters; ElementTree escapes them in attributes
            "a\rb",  # as character references, which round-trips.
            "café",
            "",  # DEL is legal XML 1.0 -- C1 is not C0.
            "\U0001f600",  # astral plane, above the BMP
            "�",  # already-replaced bytes stay as they are
        ],
    )
    def test_a_legal_character_is_left_alone(self, carried):
        assert xml_safe_text(carried) == carried

    @pytest.mark.parametrize(
        "illegal",
        [
            "\x00",  # NUL, the one github.events actually carried
            "\x01",
            "\x08",
            "\x0b",  # vertical tab: a control, and NOT one of the three legal ones
            "\x0c",
            "\x1f",
            "\udcff",  # a lone surrogate, as a surrogateescape decode leaves it
            "￾",
            "￿",
        ],
    )
    def test_an_illegal_character_becomes_the_replacement(self, illegal):
        assert xml_safe_text(illegal) == REPLACEMENT

    def test_position_and_length_are_kept(self):
        """Replaced, not stripped. A prefix is a positional thing: dropping a
        character would silently shorten the bound and move the rest of it."""
        assert xml_safe_text("a\x00b\x01c") == "a" + REPLACEMENT + "b" + REPLACEMENT + "c"

    def test_markup_characters_are_not_this_function_s_job(self):
        """Escaping `&` and `<` is ElementTree's, and doing it here too would
        double-escape them."""
        assert xml_safe_text("a & b < c") == "a & b < c"


class TestSanitizeTree:
    def test_attribute_values_text_and_tails_are_all_swept(self):
        root = ET.Element("root", attrib={"Term": "a\x00b"})
        child = ET.SubElement(root, "child")
        child.text = "t\x01t"
        child.tail = "l\x02l"

        sanitize_xml_tree(root)

        assert root.attrib["Term"] == "a" + REPLACEMENT + "b"
        assert child.text == "t" + REPLACEMENT + "t"
        assert child.tail == "l" + REPLACEMENT + "l"

    def test_the_result_parses(self):
        root = ET.Element("root", attrib={"Term": "\x00\x01\x02"})
        with pytest.raises(ET.ParseError):
            ET.fromstring(ET.tostring(root, encoding="unicode"))
        ET.fromstring(ET.tostring(sanitize_xml_tree(root), encoding="unicode"))


class TestDatasetMetadataDocument:
    """End to end, through the route that serves the real thing."""

    def test_a_document_with_control_bytes_in_a_prefix_still_parses(self):
        content = get_xml_for(
            column_type="VARCHAR",
            stats={
                "min": None,
                "max": None,
                "min_display": "\x00\x01\x02 '#",
                "max_display": "zzz",
            },
        )
        # The assertion that matters: this is what every client does first, and
        # what used to fail for public.github.events.
        ET.fromstring(content)

    def test_the_representable_part_of_a_prefix_survives(self):
        content = get_xml_for(
            column_type="VARCHAR",
            stats={"min": None, "max": None, "min_display": "Ea\x00rth", "max_display": "Venus"},
        )
        terms = stat_terms(content)
        assert terms["MinPrefix"] == "Ea" + REPLACEMENT + "rth"
        # A clean bound beside a dirty one is untouched -- the damage is per
        # character, not per document.
        assert terms["MaxPrefix"] == "Venus"

    def test_a_clean_document_is_byte_for_byte_unchanged(self):
        """The sweep is a no-op for every dataset that was already fine, which
        is nearly all of them."""
        content = get_xml_for(
            column_type="VARCHAR",
            stats={"min": None, "max": None, "min_display": "Earth", "max_display": "Venus"},
        )
        terms = stat_terms(content)
        assert terms["MinPrefix"] == "Earth"
        assert terms["MaxPrefix"] == "Venus"
        assert REPLACEMENT not in content
