"""Worker person-number filters and public-worker identity enrichment."""
from recruit_server.core.errors import APIError
from recruit_server.core.identifiers import literal, text
from recruit_server.services.reference_values import canonical, resolve_name


class Persons:
    def __init__(self, client):
        self.client = client
        self.people = {}

    def person_id(self, number):
        number = text(number)
        if number not in self.people:
            # GET /workers?q=PersonNumber=...: verify the numeric ID used in manager/recruiter filters.
            result = self.client.collection("workers", {
                "q": "PersonNumber=" + literal(number), "fields": "PersonId,PersonNumber", "onlyData": "true",
            })
            records = self.client.require_complete(result)
            ids = {canonical(row.get("PersonId")) for row in records}
            if not records:
                raise APIError("person_not_visible", "Person number was not found in the account-visible worker view", 404)
            if None in ids or len(ids) != 1 or any(row.get("PersonNumber") != number for row in records):
                raise APIError("person_ambiguous", "Person number could not be uniquely verified", 409)
            resolved = next(iter(ids))
            self.people[number] = resolved
        return self.people[number]

    def resolve(self, person_id):
        try:
            # GET /publicWorkers/{PersonId}: resolve person number, names, and work email in one cached read.
            row = self.client.item("publicWorkers/" + person_id, {
                "fields": "PersonId,PersonNumber,DisplayName,FirstName,LastName,WorkEmail", "onlyData": "true",
            })
            if canonical(row.get("PersonId")) != person_id:
                raise APIError("person_identity", "Public worker identity could not be verified", 502)
        except APIError as exc:
            return resolve_name("person", [], [exc.public()])
        return resolve_name("person", [row], [])
