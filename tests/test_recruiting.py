import unittest

from recruit_server.core.errors import APIError
from recruit_server.services.recruiting import Recruiting
from tests.support import client, linked, page, response, settings


def application(**extra):
    return linked("recruitingJobApplications", "APP_KEY", JobApplicationId=100, CandidatePersonId=200,
                  RequisitionNumber="R1", PhaseId=10, StateId=20, PhaseName="Screen", StateName="New", **extra)


class RecruitingTests(unittest.TestCase):
    def test_open_phase_and_manager_resolution_reuses_shared_person(self):
        api, session = client([response(page([{"PersonNumber": "EMP1", "PersonId": 5}])), response(page([]))])
        result = Recruiting(api).requisitions(phase="Open", hiring_manager="EMP1", recruiter="EMP1")
        self.assertEqual(session.request.call_count, 2)
        query = session.request.call_args.kwargs["params"]["q"]
        self.assertEqual(query, "PhaseName='Open' and HiringManagerId=5 and RecruiterId=5")
        self.assertEqual(result["metadata"]["person_filter_resolution"]["hiring_manager"]["person_id"], "5")

    def test_failed_or_ambiguous_identity_never_searches_requisitions(self):
        for workers in ([], [{"PersonNumber": "OTHER", "PersonId": 5}],
                        [{"PersonNumber": "EMP1", "PersonId": 5}, {"PersonNumber": "EMP1", "PersonId": 6}]):
            api, session = client([response(page(workers))])
            with self.assertRaises(APIError):
                Recruiting(api).requisitions(hiring_manager="EMP1")
            self.assertEqual(session.request.call_count, 1)

    def test_partial_identity_lookup_is_not_unique(self):
        api, session = client([response(page([{"PersonNumber": "EMP1", "PersonId": 5}], True)), response(status=403)])
        with self.assertRaises(APIError):
            Recruiting(api).requisitions(hiring_manager="EMP1")
        self.assertEqual(session.request.call_count, 2)

    def test_detail_resolves_number_and_optional_children(self):
        row = linked("recruitingJobRequisitions", "REQ_KEY", RequisitionNumber="R1", Title="Synthetic")
        api, session = client([response(page([row]))] + [response(page([])) for _ in range(6)])
        result = Recruiting(api).requisition("R1", include_children=True)
        self.assertEqual(result["data"]["oracle_key"], "REQ_KEY")
        self.assertEqual(len(result["data"]["children"]), 6)
        self.assertEqual(session.request.call_count, 7)

    def test_children_without_key_report_partial_and_never_guess(self):
        api, session = client([response(page([{"RequisitionNumber": "R1", "RequisitionId": 123}]))])
        result = Recruiting(api).requisition("R1", include_children=True)
        self.assertEqual(result["metadata"]["retrieval_status"], "partial")
        self.assertEqual(session.request.call_count, 1)

    def test_reference_people_use_public_workers_and_deduplicate_ids(self):
        api, session = client([response(page([
            {"RequisitionNumber": "R1", "RecruiterId": 1, "HiringManagerId": 2},
            {"RequisitionNumber": "R2", "RecruiterId": 1, "HiringManagerId": 2},
        ])), response({"PersonId": 1, "PersonNumber": "EMP1", "DisplayName": "Synthetic One", "FirstName": "Synthetic", "LastName": "One", "WorkEmail": "recruiter@example.test"}),
             response({"PersonId": 2, "PersonNumber": "EMP2", "DisplayName": "Synthetic Two", "FirstName": "Synthetic", "LastName": "Two", "WorkEmail": "manager@example.test"})])
        for _ in range(2):
            result = Recruiting(api).requisitions()
            refs = result["data"][0]["reference_resolution"]
            self.assertEqual(refs["hiring_manager"]["name"], "Synthetic Two")
            self.assertEqual(refs["hiring_manager"]["person_number"], "EMP2")
            self.assertEqual(refs["hiring_manager"]["FirstName"], "Synthetic")
            self.assertEqual(refs["hiring_manager"]["LastName"], "Two")
            self.assertEqual(refs["recruiter"]["DisplayName"], "Synthetic One")
            self.assertEqual(refs["recruiter"]["WorkEmail"], "recruiter@example.test")
            self.assertEqual(refs["hiring_manager"]["WorkEmail"], "manager@example.test")
            self.assertEqual(refs["recruiter"]["FirstName"], "Synthetic")
            self.assertEqual(refs["recruiter"]["LastName"], "One")
        self.assertEqual(session.request.call_count, 3)
        for call, person_id in zip(session.request.call_args_list[1:], (1, 2)):
            self.assertTrue(call.args[1].endswith("/publicWorkers/" + str(person_id)))
            self.assertEqual(call.kwargs["params"], {
                "fields": "PersonId,PersonNumber,DisplayName,FirstName,LastName,WorkEmail", "onlyData": "true"})

    def test_requisition_filters_are_sent_to_oracle_on_every_page(self):
        api, session = client([
            response(page([{"PersonNumber": "EMP1", "PersonId": 5}])),
            response(page([{"RequisitionNumber": "R1"}], more=True)),
            response(page([{"RequisitionNumber": "R2"}])),
        ])
        result = Recruiting(api).requisitions(
            phase="Open", state="Posted", modified_since="2026-01-01", hiring_manager="EMP1",
            recruiter="EMP1", resolve_references=False, limit=1)
        expected = ("PhaseName='Open' and StateName='Posted' and "
                    "RequisitionLastModifiedDate>='2026-01-01' and HiringManagerId=5 and RecruiterId=5")
        for call, offset in zip(session.request.call_args_list[1:], (0, 1)):
            self.assertEqual(call.kwargs["params"]["q"], expected)
            self.assertEqual(call.kwargs["params"]["offset"], offset)
            self.assertEqual(call.kwargs["params"]["limit"], 1)
        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(session.request.call_count, 3)

    def test_batched_reference_index_verifies_matches_and_keeps_ambiguity(self):
        api, session = client([response(page([
            {"DepartmentId": 1}, {"DepartmentId": 2}, {"DepartmentId": 1},
        ])), response(page([
            {"OrganizationId": 999, "Name": "Wrong"},
            {"OrganizationId": 2, "Name": "Two"},
            {"OrganizationId": 1, "Name": "One"},
            {"OrganizationId": 1, "Name": "Conflicting"},
        ]))])
        result = Recruiting(api).requisitions()
        refs = [row["reference_resolution"]["department"] for row in result["data"]]
        self.assertEqual(refs[0]["status"], "ambiguous")
        self.assertIsNone(refs[0]["name"])
        self.assertEqual(refs[1]["name"], "Two")
        self.assertEqual(refs[0], refs[2])
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(session.request.call_args.kwargs["params"]["q"], "OrganizationId in (1,2)")

    def test_person_name_fields_prefer_global_and_do_not_guess_conflicts(self):
        from recruit_server.services.lookups import Lookups
        lookup = Lookups(None)
        records = [{"PersonNumber": "EMP1", "names": {"items": [
            {"NameType": "LOCAL", "DisplayName": "Local", "FirstName": "Local", "LastName": "Name"},
            {"NameType": "GLOBAL", "DisplayName": "Global Name", "FirstName": "Global", "LastName": "Name"},
        ]}}]
        resolved = lookup._name("person", records, [])
        self.assertEqual(resolved["DisplayName"], "Global Name")
        self.assertEqual(resolved["FirstName"], "Global")
        self.assertEqual(resolved["LastName"], "Name")
        records[0]["names"]["items"].append(
            {"NameType": "GLOBAL", "DisplayName": "Other Name", "FirstName": "Other", "LastName": "Name"})
        resolved = lookup._name("person", records, [])
        self.assertIsNone(resolved["DisplayName"])
        self.assertIsNone(resolved["FirstName"])
        self.assertEqual(resolved["LastName"], "Name")
        self.assertEqual(resolved["status"], "ambiguous")
        records = [{}]
        lookup.enrich(records)
        self.assertIsNone(records[0]["reference_resolution"]["recruiter"]["FirstName"])

    def test_public_worker_failure_retains_requisition(self):
        api, _ = client([response(page([{"RecruiterId": 300000123456789}])), response(status=403)])
        result = Recruiting(api).requisitions()
        ref = result["data"][0]["reference_resolution"]["recruiter"]
        self.assertIsNone(ref["person_number"])
        self.assertEqual(ref["person_number_status"], "unavailable")
        self.assertIsNone(ref["DisplayName"])
        self.assertIsNone(ref["WorkEmail"])
        self.assertEqual(ref["status"], "unavailable")
        self.assertEqual(result["metadata"]["retrieval_status"], "partial")

    def test_detail_resolves_people_and_locations_by_default(self):
        row = linked("recruitingJobRequisitions", "REQ_KEY", RequisitionNumber="R1",
                     HiringManagerId=1, RecruiterId=1, PrimaryWorkLocationId=2, PrimaryLocationId=3)
        api, session = client([response(page([row])),
                               response({"PersonId": 1, "PersonNumber": "EMP1", "DisplayName": "Manager"}),
                               response(page([{"LocationId": 2, "LocationName": "Main Office"}])),
                               response(page([{"GeographyId": 3, "LocationFQN": "India, Mumbai"}]))])
        result = Recruiting(api).requisition("R1")
        refs = result["data"]["reference_resolution"]
        self.assertEqual(refs["hiring_manager"]["person_number"], "EMP1")
        self.assertEqual(refs["recruiter"]["name"], "Manager")
        self.assertIsNone(refs["recruiter"]["WorkEmail"])
        self.assertEqual(refs["work_location"]["name"], "Main Office")
        self.assertEqual(refs["recruiting_location"]["name"], "India, Mumbai")
        self.assertEqual(session.request.call_count, 4)

    def test_public_worker_identity_mismatch_is_not_used(self):
        api, _ = client([response(page([{"RecruiterId": 1}])),
                         response({"PersonId": 999, "PersonNumber": "WRONG", "DisplayName": "Wrong Person"})])
        ref = Recruiting(api).requisitions()["data"][0]["reference_resolution"]["recruiter"]
        self.assertIsNone(ref["person_number"])
        self.assertIsNone(ref["DisplayName"])
        self.assertIsNone(ref["WorkEmail"])
        self.assertEqual(ref["status"], "unavailable")
        self.assertEqual(ref["errors"][0]["code"], "person_identity")

    def test_journey_is_snapshot_and_resolves_candidate_number(self):
        api, session = client([response({"CandidateNumber": "C1", "PersonId": 200}), response(page([application()]))])
        result = Recruiting(api).journey("R1", "C1")
        self.assertEqual(result["data"][0]["journey"]["history_status"], "unavailable")
        self.assertEqual(result["data"][0]["journey"]["transitions"], [])
        self.assertIn("CandidatePersonId=200", session.request.call_args.kwargs["params"]["q"])

    def test_applications_do_not_fabricate_candidate_number(self):
        api, _ = client([response(page([application()]))])
        result = Recruiting(api).applications("R1")
        self.assertNotIn("CandidateNumber", result["data"][0])
        self.assertIn("internal-use", result["metadata"]["support_notice"])

    def test_move_verifies_full_scope_sends_exact_contract_and_clears_cache(self):
        api, session = client([response({"CandidateNumber": "C1", "PersonId": 200}),
                               response(page([application()])), response(status=204)], config=settings(enable_updates=True))
        api.cache.load("old", lambda: {"cached": True})
        result = Recruiting(api).move("R1", "C1", {"application_id": 100, "phase_id": 30, "state_id": 40,
                                                   "expected_phase_id": 10, "expected_state_id": 20,
                                                   "comments": "Synthetic", "reason_id": 50})
        call = session.request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertTrue(call.args[1].endswith("/APP_KEY/action/move"))
        self.assertEqual(call.kwargs["json"], {"phaseId": 30, "stateId": 40, "comments": "Synthetic", "reasonId": 50})
        self.assertEqual(call.kwargs["headers"]["Content-Type"], "application/vnd.oracle.adf.action+json")
        self.assertEqual(result["data"]["update_status"], "accepted")
        self.assertFalse(api.cache.entries)

    def test_move_stale_wrong_requisition_missing_key_or_disabled_sends_no_post(self):
        body = {"application_id": 100, "phase_id": 30, "state_id": 40, "expected_phase_id": 10, "expected_state_id": 20}
        wrong = application()
        wrong["RequisitionNumber"] = "OTHER"
        no_key = application()
        no_key.pop("links")
        stale = application()
        stale["PhaseId"] = 999
        for row in (wrong, no_key, stale):
            api, session = client([response({"CandidateNumber": "C1", "PersonId": 200}), response(page([row]))],
                                   config=settings(enable_updates=True))
            with self.assertRaises(APIError):
                Recruiting(api).move("R1", "C1", body)
            self.assertTrue(all(call.args[0] == "GET" for call in session.request.call_args_list))
        api, session = client([])
        with self.assertRaises(APIError):
            Recruiting(api).move("R1", "C1", body)
        session.request.assert_not_called()

    def test_profile_resolution_is_unique_and_reused_for_repeated_candidates(self):
        other = application()
        other["JobApplicationId"] = 101
        api, session = client([response(page([application(), other])),
                               response(page([{"PersonId": 200, "CandidateNumber": "C1"}]))])
        result = Recruiting(api).applications("R1", include_profiles=True)
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(result["data"][1]["candidate_profile"]["data"]["CandidateNumber"], "C1")

    def test_child_failure_retains_parent_successful_siblings_and_reuses_failed_read(self):
        api, session = client([response(page([application(), application()])), response(status=403)]
                               + [response(page([{"SyntheticField": "kept"}])) for _ in range(5)])
        result = Recruiting(api).applications("R1", include_children=True)
        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(result["metadata"]["retrieval_status"], "partial")
        self.assertEqual(session.request.call_count, 7)
        self.assertEqual(result["data"][0]["children"]["education"]["data"][0]["SyntheticField"], "kept")

    def test_description_partial_failure_is_explicit(self):
        row = linked("recruitingJobRequisitions", "REQ_KEY", RequisitionNumber="R1")
        api, session = client([response(page([row])), response(raw=b"external", headers={"Content-Type": "text/plain"}),
                               response(status=403)] + [response(raw=b"text", headers={"Content-Type": "text/plain"}) for _ in range(4)])
        result = Recruiting(api).requisition("R1", include_descriptions=True)
        self.assertEqual(result["metadata"]["retrieval_status"], "partial")
        self.assertEqual(result["data"]["descriptions"]["ExternalDescription"]["data"], "external")
        self.assertEqual(session.request.call_count, 7)
