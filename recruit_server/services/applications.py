"""Application records, current journeys, and guarded application moves."""
from recruit_server.services.candidates import Candidates
from recruit_server.services.entity_service import EntityService
from recruit_server.core.errors import APIError, invalid
from recruit_server.core.identifiers import literal, numeric
from recruit_server.services.reference_values import canonical
from recruit_server.infrastructure.oracle import envelope


APPLICATION_CHILDREN = ("attachments", "education", "experience", "languages", "licensesAndCertificates", "workPreferences")


class Applications(EntityService):
    def __init__(self, client, candidates=None):
        super().__init__(client)
        self.candidate_service = candidates if candidates is not None else Candidates(client)

    def candidate_history(self, candidate_identifier, *, identity_type="candidate_number", **paging):
        """List current application snapshots across requisitions for a verified candidate."""
        if identity_type == "candidate_number":
            candidate = self.candidate_service.candidate(candidate_identifier)
        elif identity_type == "person_id":
            candidate = self.candidate_service.candidate_by_person_id(candidate_identifier)
        else:
            raise invalid("identity_type must be candidate_number or person_id")
        person_id = numeric(candidate["PersonId"])
        result = self.client.collection("recruitingJobApplications", {
            "q": "CandidatePersonId=" + person_id, "totalResults": "true",
        }, **paging)
        groups = {}
        application_count = len(result["data"])
        for row in result["data"]:
            if canonical(row.get("CandidatePersonId")) != person_id:
                raise APIError("application_identity", "Oracle returned an application outside the requested identity scope", 502)
            number = row.get("RequisitionNumber")
            if number not in groups:
                groups[number] = {"RequisitionNumber": number, "applications": []}
            groups[number]["applications"].append(row)
        result["data"] = list(groups.values())
        result["metadata"].update({
            "grouped_by": "RequisitionNumber",
            "returned_count": len(groups),
            "application_count": application_count,
            "pagination_unit": "applications",
            "candidate_identity": {"CandidateNumber": candidate["CandidateNumber"], "PersonId": candidate["PersonId"]},
            "scope": "candidate_applications_across_requisitions",
            "history_status": "unavailable",
            "source": "current_oracle_application_snapshots",
            "history_limitation": "Application dates and current phase/state are returned; historical transitions are unavailable. Results are limited to the service account's visibility.",
        })
        return self._clean(result)

    def applications(self, requisition_number, *, candidate_id=None, application_id=None, include_children=False,
                     include_profiles=False, fresh=False, **paging):
        filters = ["RequisitionNumber=" + literal(requisition_number)]
        if candidate_id is not None:
            filters.append("CandidatePersonId=" + numeric(candidate_id))
        if application_id is not None:
            filters.append("JobApplicationId=" + numeric(application_id))
        # GET /recruitingJobApplications?q=RequisitionNumber=... [and candidate/application IDs]:
        # Oracle filters applications for applicant lists, journey snapshots, and move prechecks.
        result = self.client.collection("recruitingJobApplications", {
            "q": " and ".join(filters), "totalResults": "true",
        }, fresh=fresh, **paging)
        profiles = {}
        for row in result["data"]:
            if (str(row.get("RequisitionNumber")) != str(requisition_number)
                    or candidate_id is not None and canonical(row.get("CandidatePersonId")) != numeric(candidate_id)
                    or application_id is not None and canonical(row.get("JobApplicationId")) != numeric(application_id)):
                raise APIError("application_identity", "Oracle returned an application outside the requested identity scope", 502)
            key = self.client.item_key(row, "recruitingJobApplications")
            row["oracle_key"] = key
            if include_children:
                self._children(row, "recruitingJobApplications", key, APPLICATION_CHILDREN, paging.get("limit"))
            if include_profiles:
                person_id = canonical(row.get("CandidatePersonId"))
                if person_id not in profiles:
                    try:
                        if person_id is None:
                            raise APIError("missing_candidate_id", "Application has no valid candidate person ID", 502)
                        # GET /recruitingCandidates?finder=findByPersonId;personId=...:
                        # turn an application CandidatePersonId into a verified candidate profile/number.
                        candidates = self.client.collection("recruitingCandidates", {"finder": "findByPersonId;personId=" + person_id})
                        records = self.client.require_complete(candidates)
                        if (len(records) != 1 or canonical(records[0].get("PersonId")) != person_id
                                or not records[0].get("CandidateNumber")):
                            raise APIError("candidate_ambiguous", "Application candidate profile could not be uniquely verified", 409)
                        profiles[person_id] = envelope(records[0], "recruitingCandidates")
                    except APIError as exc:
                        profiles[person_id] = envelope(None, "recruitingCandidates", retrieval_status="unavailable", errors=[exc.public()])
                row["candidate_profile"] = profiles[person_id]
        self._mark_enrichment(result)
        if any(row.get("candidate_profile", {}).get("metadata", {}).get("errors") for row in result["data"]):
            result["metadata"]["retrieval_status"] = "partial"
        return self._clean(result)

    def journey(self, requisition_number, candidate_number, **paging):
        candidate = self.candidate_service.candidate(candidate_number)
        result = self.applications(requisition_number, candidate_id=candidate["PersonId"], **paging)
        for row in result["data"]:
            row["journey"] = {
                "application_date": row.get("JobApplicationDate"),
                "current_phase": row.get("PhaseName"), "current_state": row.get("StateName"),
                "transitions": [], "interviews": [], "screenings": [], "offer_status": None,
                "history_status": "unavailable", "source": "current_oracle_application_snapshot",
            }
        result["metadata"]["history_limitation"] = "Current state does not establish earlier transitions or completed milestones."
        return result

    def move(self, requisition_number, candidate_number, body):
        # Serialize moves for one candidate/requisition inside this process.
        # Oracle-side changes and other processes still require reconciliation.
        lock_key = (str(requisition_number), str(candidate_number))
        with self.client.cache.write_locks[hash(lock_key) % len(self.client.cache.write_locks)]:
            return self._move(requisition_number, candidate_number, body)

    def _move(self, requisition_number, candidate_number, body):
        if not self.client.settings.enable_updates:
            raise APIError("updates_disabled", "Journey updates are disabled in server configuration", 403)
        # Never use a cached identity/application snapshot to authorize a write.
        candidate = self.candidate_service.candidate(candidate_number, fresh=True)
        result = self.applications(requisition_number, candidate_id=candidate["PersonId"],
                                   application_id=body["application_id"], fresh=True)
        records = self.client.require_complete(result)
        if not records:
            raise APIError("application_not_found", "Application is not visible for this candidate and requisition", 404)
        if len(records) != 1:
            raise APIError("application_ambiguous", "Application identity is ambiguous", 409)
        application = records[0]
        if (canonical(application.get("PhaseId")) != str(body["expected_phase_id"])
                or canonical(application.get("StateId")) != str(body["expected_state_id"])):
            raise APIError("stale_journey", "Application phase or state changed; retrieve the journey again", 409)
        key = application.get("oracle_key")
        if key is None:
            raise APIError("missing_key", "Oracle did not return a verified application key", 502)
        action = {"phaseId": body["phase_id"], "stateId": body["state_id"]}
        for local, oracle in (("reason_id", "reasonId"), ("comments", "comments")):
            if local in body:
                action[oracle] = body[local]
        # POST /recruitingJobApplications/{key}/action/move: submit the verified phase/state change once.
        self.client.move(key, action)
        # Accepted does not guarantee an immediate transition; Oracle can apply rules.
        return envelope({"application_id": body["application_id"], "update_status": "accepted",
                         "requested_phase_id": body["phase_id"], "requested_state_id": body["state_id"],
                         "next_step": "Retrieve the journey again to observe Oracle's current state"},
                        "recruitingJobApplications", cache_invalidated=True,
                        concurrency="best_effort_precheck_only; no atomic Oracle compare-and-swap contract established")
