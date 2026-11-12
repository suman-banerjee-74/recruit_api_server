"""Shared response sanitation and request-local child enrichment."""
from copy import deepcopy

from recruit_server.core.identifiers import public_row
from recruit_server.infrastructure.oracle import envelope


class EntityService:
    def __init__(self, client):
        self.client = client
        self.child_results = {}

    def _clean(self, result):
        result["data"] = public_row(result["data"])
        return result

    def _children(self, row, resource, key, names, limit=None):
        children = {}
        for child in names:
            if key is None:
                children[child] = envelope([], resource, retrieval_status="unavailable",
                                          errors=[{"code": "missing_key", "message": "Oracle did not return a usable parent key"}])
            else:
                cache_key = (resource, key, child, limit)
                if cache_key not in self.child_results:
                    # GET /{recruitingJobRequisitions|recruitingJobApplications}/{key}/child/{child}:
                    # fetch requested related records; application attachments here are metadata only.
                    self.child_results[cache_key] = self.client.collection(f"{resource}/{key}/child/{child}", limit=limit)
                # Failed reads are reused within this invocation, not shared across requests.
                children[child] = deepcopy(self.child_results[cache_key])
        row["children"] = children

    def _mark_enrichment(self, result):
        failed = []
        for row in result["data"]:
            for bundle in ("children", "descriptions"):
                failed.extend(name for name, child in row.get(bundle, {}).items()
                              if child["metadata"]["retrieval_status"] != "retrieved")
            failed.extend("reference:" + name for name, ref in row.get("reference_resolution", {}).items()
                          if ref.get("status") == "unavailable")
        if failed:
            result["metadata"]["retrieval_status"] = "partial"
            result["metadata"]["failed_enrichments"] = sorted(set(failed))
