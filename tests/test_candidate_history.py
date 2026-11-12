"""Offline coverage for candidate-wide application history."""
import unittest

from recruit_server.core.errors import APIError
from recruit_server.services.recruiting import Recruiting
from tests.support import client, page, response


class CandidateHistoryTests(unittest.TestCase):
    def test_number_resolves_identity_and_pages_across_requisitions(self):
        api, session = client([
            response({"CandidateNumber": "C1", "PersonId": 200}),
            response(page([{"CandidatePersonId": 200, "JobApplicationId": 1,
                            "RequisitionNumber": "R1", "PhaseName": "Interview"}], True)),
            response(page([{"CandidatePersonId": 200, "JobApplicationId": 2,
                            "RequisitionNumber": "R2", "StateName": "Withdrawn"}])),
        ])
        result = Recruiting(api).candidate_history("C1", limit=1)
        self.assertEqual([row["RequisitionNumber"] for row in result["data"]], ["R1", "R2"])
        for call in session.request.call_args_list[1:]:
            self.assertEqual(call.kwargs["params"]["q"], "CandidatePersonId=200")
        self.assertEqual(session.request.call_args.kwargs["params"]["offset"], 1)
        self.assertEqual(result["metadata"]["history_status"], "unavailable")
        self.assertEqual(result["metadata"]["candidate_identity"]["CandidateNumber"], "C1")

    def test_person_id_must_resolve_unique_complete_identity(self):
        for records, more in (([], False),
                              ([{"CandidateNumber": "C1", "PersonId": 201}], False),
                              ([{"CandidateNumber": "C1", "PersonId": 200}] * 2, False),
                              ([{"CandidateNumber": "C1", "PersonId": 200}], True)):
            payloads = [response(page(records, more))]
            if more:
                payloads.append(response(status=403))
            api, session = client(payloads)
            with self.assertRaises(APIError):
                Recruiting(api).candidate_history("200", identity_type="person_id")
            self.assertTrue(all("recruitingCandidates" in call.args[1]
                                for call in session.request.call_args_list))

    def test_out_of_scope_application_is_rejected(self):
        api, _ = client([response({"CandidateNumber": "C1", "PersonId": 200}),
                         response(page([{"CandidatePersonId": 201}]))])
        with self.assertRaises(APIError) as raised:
            Recruiting(api).candidate_history("C1")
        self.assertEqual(raised.exception.code, "application_identity")

    def test_partial_application_result_preserves_errors(self):
        api, _ = client([response({"CandidateNumber": "C1", "PersonId": 200}),
                         response(page([{"CandidatePersonId": 200}], True)),
                         response(status=403)])
        result = Recruiting(api).candidate_history("C1", limit=1)
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["metadata"]["retrieval_status"], "partial")
        self.assertTrue(result["metadata"]["errors"])

    def test_groups_same_requisition_across_pages_and_retains_applications(self):
        rows = [{"CandidatePersonId": 200, "JobApplicationId": i,
                 "RequisitionNumber": number, "PhaseName": "Interview"}
                for i, number in enumerate(("R1", "R2", "R1"), 1)]
        api, _ = client([response({"CandidateNumber": "C1", "PersonId": 200}),
                         response(page(rows[:2], True)), response(page(rows[2:]))])
        result = Recruiting(api).candidate_history("C1", limit=2)
        self.assertEqual([group["RequisitionNumber"] for group in result["data"]], ["R1", "R2"])
        self.assertEqual(result["data"][0]["applications"], [rows[0], rows[2]])
        self.assertEqual(result["metadata"]["returned_count"], 2)
        self.assertEqual(result["metadata"]["application_count"], 3)
        self.assertEqual(result["metadata"]["pagination_unit"], "applications")

    def test_missing_requisition_is_retained_and_empty_history_has_no_groups(self):
        for rows in ([], [{"CandidatePersonId": 200, "JobApplicationId": 1}]):
            api, _ = client([response({"CandidateNumber": "C1", "PersonId": 200}),
                             response(page(rows))])
            result = Recruiting(api).candidate_history("C1")
            self.assertEqual(result["metadata"]["application_count"], len(rows))
            if rows:
                self.assertIsNone(result["data"][0]["RequisitionNumber"])
                self.assertEqual(result["data"][0]["applications"], rows)
            else:
                self.assertEqual(result["data"], [])

