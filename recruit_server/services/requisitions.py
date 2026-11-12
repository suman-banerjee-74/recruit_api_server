"""Oracle requisition queries, details, descriptions, and reference enrichment."""
import base64
import binascii

from recruit_server.services.entity_service import EntityService
from recruit_server.core.errors import APIError
from recruit_server.core.identifiers import finder, identifier, literal
from recruit_server.services.lookups import Lookups
from recruit_server.infrastructure.oracle import envelope


REQUISITION_CHILDREN = ("skills", "languages", "otherLocations", "otherWorkLocations", "collaborators", "publishedJobs")
DESCRIPTIONS = ("ExternalDescription", "ExternalDescriptionHTML", "ExternalShortDescription",
                "InternalDescription", "InternalDescriptionHTML", "InternalShortDescription")


class Requisitions(EntityService):
    def __init__(self, client):
        super().__init__(client)
        self.lookups = Lookups(client)

    def _requisition_rows(self, result):
        for row in result["data"]:
            row["oracle_key"] = self.client.item_key(row, "recruitingJobRequisitions")
            for field in ("ExternalDescription", "ExternalDescriptionHTML", "ExternalQualHTML", "ExternalRespHTML"):
                value = row.get(field)
                if isinstance(value, str):
                    try:
                        row[field] = base64.b64decode(value, validate=True).decode("utf-8")
                    except (ValueError, binascii.Error, UnicodeDecodeError):
                        pass

    def requisitions(self, *, phase=None, state=None, modified_since=None, hiring_manager=None,
                     recruiter=None, resolve_references=True, number=None, **paging):
        filters, params = [], {}
        for field, value in (("PhaseName", phase), ("StateName", state),
                             ("RequisitionLastModifiedDate", modified_since)):
            if value is not None:
                filters.append(field + (">=" if field == "RequisitionLastModifiedDate" else "=") + literal(value))
        resolutions = {}
        for role, field, number_value in (("hiring_manager", "HiringManagerId", hiring_manager),
                                           ("recruiter", "RecruiterId", recruiter)):
            if number_value is not None:
                resolved = self.lookups.person_id(number_value)
                filters.append(field + "=" + resolved)
                resolutions[role] = {"person_id": resolved, "status": "resolved"}
        if filters:
            params["q"] = " and ".join(filters)
        if number is not None:
            params["finder"] = "findByRequisitionNumber;RequisitionNumber=" + finder(number)
        # GET /recruitingJobRequisitions?q=...: Oracle applies phase/state/date/person filters before pagination.
        result = self.client.collection("recruitingJobRequisitions", params, **paging)
        self._requisition_rows(result)
        if resolve_references:
            self.lookups.enrich(result["data"])
            self._mark_enrichment(result)
        if resolutions:
            result["metadata"]["person_filter_resolution"] = resolutions
        return self._clean(result)

    def requisition(self, number=None, *, key=None, include_children=False,
                    include_descriptions=False, resolve_references=True):
        if key is not None:
            key = identifier(key)
            # GET /recruitingJobRequisitions/{key}: retrieve one requisition using its verified Oracle key.
            row = self.client.item("recruitingJobRequisitions/" + key)
            # The supplied key still must agree with Oracle's item identity.
            if self.client.item_key(row, "recruitingJobRequisitions") != key:
                raise APIError("requisition_identity", "Oracle requisition item identity could not be verified", 502)
            result = envelope([row], "recruitingJobRequisitions")
        else:
            params = {"finder": "findByRequisitionNumber;RequisitionNumber=" + finder(number)}
            # GET /recruitingJobRequisitions?finder=findByRequisitionNumber;RequisitionNumber=...
            # Resolve the display number without downloading an unfiltered collection.
            result = self.client.collection("recruitingJobRequisitions", params)
            records = self.client.require_complete(result)
            if not records:
                raise APIError("not_found", "Requisition is not visible or was not found", 404)
            if len(records) != 1 or str(records[0].get("RequisitionNumber")) != str(number):
                raise APIError("requisition_ambiguous", "Requisition number could not be uniquely verified", 409)
        self._requisition_rows(result)
        row = result["data"][0]
        key = row.get("oracle_key")
        if include_children:
            self._children(row, "recruitingJobRequisitions", key, REQUISITION_CHILDREN)
        if include_descriptions:
            descriptions = {}
            for attribute in DESCRIPTIONS:
                try:
                    if key is None:
                        raise APIError("missing_key", "Oracle did not return a usable requisition key", 502)
                    # GET /recruitingJobRequisitions/{key}/enclosure/{attribute}: description text/HTML.
                    descriptions[attribute] = envelope(self.client.text(
                        f"recruitingJobRequisitions/{key}/enclosure/{attribute}"), "recruitingJobRequisitions")
                except APIError as exc:
                    descriptions[attribute] = envelope(None, "recruitingJobRequisitions",
                                                        retrieval_status="unavailable", errors=[exc.public()])
            row["descriptions"] = descriptions
        if resolve_references:
            self.lookups.enrich([row])
        self._mark_enrichment(result)
        result["data"] = row
        return self._clean(result)

    def statuses(self, **paging):
        # GET /recruitingJobRequisitions?fields=PhaseId,PhaseName,StateId,StateName:
        # fetch only status fields, then count observed combinations locally.
        result = self.client.collection("recruitingJobRequisitions", {
            "fields": "PhaseId,PhaseName,StateId,StateName",
        }, **paging)
        groups = {}
        for row in result["data"]:
            key = tuple(row.get(field) for field in ("PhaseId", "PhaseName", "StateId", "StateName"))
            groups[key] = groups.get(key, 0) + 1
        result["data"] = [dict(zip(("PhaseId", "PhaseName", "StateId", "StateName"), key), count=count)
                          for key, count in groups.items()]
        result["metadata"]["summary_scope"] = "returned_visible_rows_only"
        return self._clean(result)
