"""Candidate identity verification, search, and profile details."""
from recruit_server.services.entity_service import EntityService
from recruit_server.core.errors import APIError, invalid
from recruit_server.core.identifiers import finder, identifier, literal, numeric
from recruit_server.infrastructure.oracle import envelope


class Candidates(EntityService):
    def candidate_by_person_id(self, person_id):
        person_id = numeric(person_id)
        result = self.client.collection("recruitingCandidates", {
            "finder": "findByPersonId;personId=" + person_id,
        })
        records = self.client.require_complete(result)
        if not records:
            raise APIError("candidate_not_found", "Candidate is not visible for this person ID", 404)
        if (len(records) != 1 or numeric(records[0].get("PersonId")) != person_id
                or not records[0].get("CandidateNumber")):
            raise APIError("candidate_ambiguous", "Candidate person ID could not be uniquely verified", 409)
        return records[0]

    def candidate(self, number, *, fresh=False):
        number = identifier(number)
        # GET /recruitingCandidates/{CandidateNumber}: verify candidate identity; writes require a fresh read.
        row = self.client.item("recruitingCandidates/" + number, fresh=fresh)
        if str(row.get("CandidateNumber")) != number:
            raise APIError("candidate_identity", "Oracle candidate number could not be verified", 502)
        numeric(row.get("PersonId"))
        return row

    def candidates(self, *, keywords=None, last_name=None, candidate_type=None, modified_since=None, **paging):
        params, filters = {}, []
        if keywords is not None:
            params["finder"] = "search;keywords=" + finder(keywords)
        for field, value in (("LastName", last_name), ("CandidateType", candidate_type), ("CandLastModifiedDate", modified_since)):
            if value is not None:
                filters.append(field + (">=" if field == "CandLastModifiedDate" else "=") + literal(value))
        if filters:
            params["q"] = " and ".join(filters)
        # GET /recruitingCandidates: Oracle keyword finder plus last-name/type/modified-date filters.
        return self._clean(self.client.collection("recruitingCandidates", params, **paging))

    def candidate_details(self, number, child, **paging):
        if child not in ("education", "experience", "skills"):
            raise invalid("Candidate detail must be education, experience, or skills")
        # GET /recruitingCandidates/{CandidateNumber}/child/{education|experience|skills}: profile details.
        return self._clean(self.client.collection(f"recruitingCandidates/{identifier(number)}/child/{child}", **paging))

    def candidate_summary(self, number, **paging):
        # GET /recruitingCandidates/{CandidateNumber}: verify identity before child reads.
        profile = self.candidate(number)
        data = {"profile": profile}
        failed = []
        for child in ("education", "experience", "skills"):
            # GET /recruitingCandidates/{CandidateNumber}/child/{child}; each retains its own paging/errors.
            data[child] = self.candidate_details(number, child, **paging)
            if data[child]["metadata"]["retrieval_status"] != "retrieved":
                failed.append(child)
        return self._clean(envelope(data, "recruitingCandidates",
                                    retrieval_status="partial" if failed else "retrieved",
                                    failed_details=failed))
