"""Verified identity resolution and batched, request-local reference enrichment."""
from recruit_server.services.locations import LOCATION_SPECS, Locations
from recruit_server.services.persons import Persons
from recruit_server.services.reference_values import PERSON_NAME_FIELDS, canonical, resolve_name


SPECS = {
    "person": ("workers", "PersonId", "PersonId,PersonNumber"),
    "work_location": LOCATION_SPECS["work_location"],
    "department": ("departmentsLov", "OrganizationId", "OrganizationId,Name"),
    "business_unit": ("hcmBusinessUnitsLOV", "BusinessUnitId", "BusinessUnitId,Name"),
    "geography": LOCATION_SPECS["geography"],
}

REFERENCES = (
    ("recruiter", "person", "RecruiterId"),
    ("hiring_manager", "person", "HiringManagerId"),
    ("recruiting_location", "geography", "PrimaryLocationId"),
    ("work_location", "work_location", "PrimaryWorkLocationId"),
    ("department", "department", "DepartmentId"),
    ("business_unit", "business_unit", "BusinessUnitId"),
)


class Lookups:
    def __init__(self, client):
        self.client = client
        self.persons = Persons(client)
        self.locations = Locations(client)
        self.names = {}

    _name = staticmethod(resolve_name)

    def person_id(self, number):
        return self.persons.person_id(number)

    def enrich(self, records):
        # Canonicalize each reference once, then reuse it for fetches and output.
        references = [{role: (kind, canonical(row.get(field)))
                       for role, kind, field in REFERENCES} for row in records]
        pending = {kind: set() for kind in SPECS}
        for refs in references:
            for kind, value in refs.values():
                if value is not None and (kind, value) not in self.names:
                    pending[kind].add(value)
        for kind, (resource, id_field, projection) in SPECS.items():
            ids = sorted(pending[kind], key=int)
            if kind == "person":
                for person_id in ids:
                    self.names[(kind, person_id)] = self.persons.resolve(person_id)
                continue
            batch_size = 1 if kind == "geography" else 25
            for start in range(0, len(ids), batch_size):
                batch = ids[start:start + batch_size]
                params = {"fields": projection}
                if kind in LOCATION_SPECS:
                    result = self.locations.fetch(kind, batch)
                else:
                    params["q"] = id_field + ("=" + batch[0] if len(batch) == 1 else " in (" + ",".join(batch) + ")")
                    # GET /departmentsLov?q=OrganizationId=... or /hcmBusinessUnitsLOV?q=BusinessUnitId=...:
                    # batch department/business-unit IDs to obtain their reference names.
                    result = self.client.collection(resource, params)
                errors = result["metadata"]["errors"]
                matches = {requested: [] for requested in batch}
                for item in result["data"]:
                    key = canonical(item.get(id_field))
                    if key in matches:
                        matches[key].append(item)
                for requested, matched in matches.items():
                    self.names[(kind, requested)] = self._name(kind, matched, errors)
                if any(error.get("oracle_status") in (401, 403, 404) for error in errors):
                    for requested in ids[start + batch_size:]:
                        self.names[(kind, requested)] = self._name(kind, [], errors)
                    break
        for row, refs in zip(records, references):
            row["reference_resolution"] = {
                role: self.names.get((kind, value), {"name": None, "status": "missing_or_invalid_id",
                    **({"person_number": None, "person_number_status": "missing_or_invalid_id",
                        **dict.fromkeys(PERSON_NAME_FIELDS), "WorkEmail": None}
                       if kind == "person" else {})})
                for role, (kind, value) in refs.items()
            }
