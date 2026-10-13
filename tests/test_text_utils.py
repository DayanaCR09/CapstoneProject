import pytest

from text_utils import extract_isbns, normalize_string


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ISBN: 0-306-40615-2", ["0306406152"]),
        ("ISBN-13 978-0-306-40615-7", ["9780306406157"]),
        ("9780306406157", ["9780306406157"]),
        ("ISBN 0-8044-2957-X", ["080442957X"]),
        (
            "First: 978-0-306-40615-7; second: 0-306-40615-2",
            ["9780306406157", "0306406152"],
        ),
    ],
)
def test_extracts_valid_isbns(text, expected):
    assert extract_isbns(text) == expected


def test_extract_isbns_ignores_invalid_or_embedded_values():
    assert extract_isbns(
        "Invalid checksum: 9780306406158; "
        "embedded digits: 12345678901234; malformed: 978-ABC-40615-7"
    ) == []


def test_extract_isbns_deduplicates_normalized_values():
    assert extract_isbns("978-0-306-40615-7 and 9780306406157") == [
        "9780306406157"
    ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  Pride   and PREJUDICE! ", "pride and prejudice"),
        ("Crème brûlée—Vol. 2", "crème brûlée vol 2"),
        ("One\n\tTwo", "one two"),
        ("---", ""),
    ],
)
def test_normalize_string(value, expected):
    assert normalize_string(value) == expected


@pytest.mark.parametrize("function", [extract_isbns, normalize_string])
def test_text_helpers_require_strings(function):
    with pytest.raises(TypeError, match="string"):
        function(None)
