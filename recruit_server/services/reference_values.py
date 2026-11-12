"""Canonical reference IDs and explicit resolution values."""
from recruit_server.core.errors import APIError
from recruit_server.core.identifiers import numeric, rows


PERSON_NAME_FIELDS = ("DisplayName", "FirstName", "LastName")


def canonical(value):
    try:
        return numeric(value)
    except APIError:
        return None


def resolve_name(kind, records, errors):
    details = {}
    if kind == "person":
        details.update(dict.fromkeys(PERSON_NAME_FIELDS))
        emails = {row.get("WorkEmail") for row in records} - {None, ""}
        details["WorkEmail"] = next(iter(emails)) if len(emails) == 1 and not errors else None
        numbers = {row.get("PersonNumber") for row in records} - {None, ""}
        details["person_number"] = next(iter(numbers)) if len(numbers) == 1 and not errors else None
        details["person_number_status"] = ("unavailable" if errors else "resolved" if len(numbers) == 1
                                           else "ambiguous" if numbers else "not_visible_or_not_found")
    if errors:
        return {**details, "name": None, "status": "unavailable", "errors": errors}
    names = set()
    person_names = []
    for row in records:
        if kind == "person":
            children = rows(row.get("names", []))
            global_names = [item for item in children if item.get("NameType") == "GLOBAL"]
            candidates = global_names or children
            person_names.extend(candidates or [row])
            names.update(item.get("DisplayName") or item.get("FullName") for item in candidates)
            names.add(row.get("DisplayName") or row.get("FullName"))
        else:
            names.add(row.get("LocationFQN") if kind == "geography" else
                      row.get("LocationName") or row.get("LocationCode") if kind == "work_location" else row.get("Name"))
    if kind == "person":
        for field in PERSON_NAME_FIELDS:
            values = {item.get(field) for item in person_names} - {None, ""}
            details[field] = next(iter(values)) if len(values) == 1 else None
    names.discard(None)
    names.discard("")
    return {**details, "name": next(iter(names)) if len(names) == 1 else None,
            "status": "resolved" if len(names) == 1 else "ambiguous" if names else
            "missing_name" if records else "not_visible_or_not_found"}
