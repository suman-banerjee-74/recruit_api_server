"""Keep display numbers, numeric IDs, and Oracle opaque keys separate."""
import re

from recruit_server.core.errors import invalid


def identifier(value):
    value = str(value)
    if len(value) > 512 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value) or value in (".", "..", "None"):
        raise invalid("A valid Oracle identifier is required")
    return value


def text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise invalid("A nonempty text value of at most 512 characters is required")
    return value


def literal(value):
    return "'" + text(value).replace("'", "''") + "'"


def finder(value):
    value = text(value)
    if any(c in value for c in ";,="):
        raise invalid("Finder values cannot contain query delimiters")
    return value


def numeric(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)) or int(value) <= 0:
        raise invalid("A positive numeric Oracle ID is required")
    return str(int(value))


def rows(value):
    if isinstance(value, dict):
        value = value.get("items", [])
    return value if isinstance(value, list) else []


def public_row(row):
    """Do not expose arbitrary Oracle URLs, file contents, or transport links."""
    if isinstance(row, list):
        return [public_row(item) for item in row]
    if isinstance(row, dict):
        return {key: public_row(value) for key, value in row.items()
                if key not in ("links", "FileUrl", "FileContents")}
    return row
