import re

_ISBN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:ISBN(?:-1[03])?\s*:?\s*)?"
    r"(\d(?:[ -]?\d){8,11}[ -]?[\dXx])(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_NON_WORD_PATTERN = re.compile(r"[\W_]+", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_string(value: str) -> str:
    """Case-fold text, replace punctuation with spaces, and collapse whitespace."""
    if not isinstance(value, str):
        raise TypeError("value must be a string")

    normalized = _NON_WORD_PATTERN.sub(" ", value.casefold())
    return _WHITESPACE_PATTERN.sub(" ", normalized).strip()


def _is_valid_isbn(isbn: str) -> bool:
    if len(isbn) == 10:
        digits = [10 if char.upper() == "X" else int(char) for char in isbn]
        checksum = sum(
            digit * weight for digit, weight in zip(digits, range(10, 0, -1))
        )
        return checksum % 11 == 0

    if len(isbn) == 13 and isbn.isdigit():
        checksum = sum(
            int(digit) * (1 if index % 2 == 0 else 3)
            for index, digit in enumerate(isbn)
        )
        return checksum % 10 == 0

    return False


def extract_isbns(value: str) -> list[str]:
    """Extract valid ISBN-10 and ISBN-13 values, normalized to unseparated text."""
    if not isinstance(value, str):
        raise TypeError("value must be a string")

    matches: list[str] = []
    seen: set[str] = set()
    for match in _ISBN_PATTERN.finditer(value):
        isbn = re.sub(r"[ -]", "", match.group(1)).upper()
        if isbn not in seen and _is_valid_isbn(isbn):
            seen.add(isbn)
            matches.append(isbn)
    return matches
