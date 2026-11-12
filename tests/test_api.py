import os
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from recruit_server.http.api import create_app
from recruit_server.core.config import Settings
from tests.support import client, linked, page, response, settings


class HTTPTests(unittest.TestCase):
    def app(self, payloads=(), **config):
        api, session = client(list(payloads), config=settings(**config))
        app = create_app(api.settings, client_factory=lambda config, cache: self.bind(api, cache))
        return TestClient(app, raise_server_exceptions=False), session

    def bind(self, api, cache):
        api.cache = cache
        return api

    def test_http_person_id_paging_auth_and_validation(self):
        http, session = self.app([
            response(page([{"CandidateNumber": "C1", "PersonId": 200}])),
            response(page([{"CandidatePersonId": 200}], True)),
        ])
        path = "/candidates/200/history?identity_type=person_id&retrieve_all=false&limit=10&offset=20"
        self.assertEqual(http.get(path).status_code, 401)
        session.request.assert_not_called()
        result = http.get(path, auth=("fusion-user", "fusion-secret"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(session.request.call_args_list[0].kwargs["params"]["finder"],
                         "findByPersonId;personId=200")
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(session.request.call_args.kwargs["params"]["offset"], 20)
        for query in ("identity_type=unknown", "identity_type=", "limit=501", "requisition=R1",
                      "limit=1&limit=2"):
            self.assertEqual(http.get("/candidates/C1/history?" + query,
                                      auth=("fusion-user", "fusion-secret")).status_code, 400)
        self.assertEqual(session.request.call_count, 2)

    def test_every_route_requires_basic_auth(self):
        http, session = self.app()
        for path in ("/health", "/capabilities", "/requisitions/open", "/not-found"):
            result = http.get(path)
            self.assertEqual(result.status_code, 401)
            self.assertIn("Basic", result.headers["www-authenticate"])
            self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertEqual(http.get("/health", auth=("fusion-user", "wrong")).status_code, 401)
        session.request.assert_not_called()

    def test_consolidated_candidate_details_route(self):
        http, session = self.app([
            response({"CandidateNumber": "C1", "PersonId": 10}),
            response(page([])), response(page([])), response(page([])),
        ])
        result = http.get("/candidates/C1/details?retrieve_all=false&limit=10",
                          auth=("fusion-user", "fusion-secret"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(set(result.json()["data"]), {"profile", "education", "experience", "skills"})
        self.assertEqual(session.request.call_count, 4)

    def test_legacy_candidate_profile_alias_has_identical_response(self):
        http, session = self.app([response({"CandidateNumber": "C1", "PersonId": 10})])
        canonical = http.get("/candidates/C1", auth=("fusion-user", "fusion-secret"))
        legacy = http.get("/applicants/C1", auth=("fusion-user", "fusion-secret"))
        self.assertEqual(canonical.status_code, 200)
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(canonical.json(), legacy.json())
        self.assertEqual(session.request.call_count, 1)

    def test_requisition_candidates_and_legacy_alias_share_filters(self):
        http, session = self.app([response(page([{"RequisitionNumber": "R1", "JobApplicationId": 10}]))])
        for path in ("/requisitions/R1/candidates", "/requisitions/R1/applicants"):
            result = http.get(path + "?retrieve_all=false&limit=10", auth=("fusion-user", "fusion-secret"))
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["data"][0]["JobApplicationId"], 10)
        self.assertEqual(session.request.call_count, 1)
        self.assertEqual(session.request.call_args.kwargs["params"]["q"], "RequisitionNumber='R1'")

    def test_authenticated_health_does_not_probe_oracle(self):
        http, session = self.app()
        result = http.get("/health", auth=("fusion-user", "fusion-secret"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["oracle_connectivity"], "not_probed")
        session.request.assert_not_called()

    def test_open_requisitions_pagination_and_cached_second_call(self):
        http, session = self.app([response(page([{"RequisitionNumber": "R1"}], True)), response(page([{"RequisitionNumber": "R2"}]))])
        for _ in range(2):
            result = http.get("/requisitions/open", auth=("fusion-user", "fusion-secret"))
            self.assertEqual(result.status_code, 200)
            self.assertEqual(len(result.json()["data"]), 2)
        self.assertTrue(result.json()["metadata"]["cache_hit"])
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(session.request.call_args.kwargs["params"]["q"], "PhaseName='Open'")

    def test_requisition_phase_and_state_filters(self):
        for path, payloads, suffix in (
            ("/requisitions", [], ""),
            ("/requisitions/open", [], ""),
            ("/hiring-managers/EMP1/requisitions",
             [response(page([{"PersonNumber": "EMP1", "PersonId": 5}]))], " and HiringManagerId=5"),
            ("/recruiters/EMP1/requisitions",
             [response(page([{"PersonNumber": "EMP1", "PersonId": 5}]))], " and RecruiterId=5"),
        ):
            for phase_key, state_key in (("PhaseName", "StateName"), ("phase", "state")):
                with self.subTest(path=path, phase_key=phase_key):
                    http, session = self.app([*payloads, response(page([]))])
                    result = http.get(path, params={phase_key: "Open", state_key: "Posted"},
                                      auth=("fusion-user", "fusion-secret"))
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(session.request.call_args.kwargs["params"]["q"],
                                     "PhaseName='Open' and StateName='Posted'" + suffix)

    def test_invalid_requisition_filter_aliases_do_not_call_oracle(self):
        for query in ("phase=Open&PhaseName=Closed", "state=Posted&StateName=Posted",
                      "PhaseName=", "StateName=", "PhaseName=Open&PhaseName=Open"):
            http, session = self.app()
            result = http.get("/requisitions?" + query, auth=("fusion-user", "fusion-secret"))
            self.assertEqual(result.status_code, 400)
            session.request.assert_not_called()
        http, session = self.app()
        result = http.get("/requisitions/open?PhaseName=Closed", auth=("fusion-user", "fusion-secret"))
        self.assertEqual(result.status_code, 400)
        session.request.assert_not_called()

    def test_query_errors_are_sanitized(self):
        http, session = self.app()
        for query in ("limit=0", "retrieve_all=yes", "FileUrl=https://secret.example", "limit=1&limit=2", "limit=SECRET", "limit=²"):
            result = http.get("/requisitions?" + query, auth=("fusion-user", "fusion-secret"))
            self.assertEqual(result.status_code, 400)
            self.assertNotIn("SECRET", result.text)
            self.assertNotIn("secret.example", result.text)
        session.request.assert_not_called()

    def test_move_validation_body_limit_and_disabled(self):
        http, session = self.app(enable_updates=True)
        path = "/requisitions/R1/candidates/C1/journey"
        for body in ({}, {"phase_id": True}, {"unknown": "PRIVATE_COMMENT"}):
            result = http.post(path, json=body, auth=("fusion-user", "fusion-secret"))
            self.assertEqual(result.status_code, 400)
            self.assertNotIn("PRIVATE_COMMENT", result.text)
        result = http.post(path, content=b"x" * 17000, headers={"Content-Type": "application/json"}, auth=("fusion-user", "fusion-secret"))
        self.assertEqual(result.status_code, 413)
        result = http.post(path, content='{"phase_id":1,"phase_id":2}', headers={"Content-Type": "application/json"}, auth=("fusion-user", "fusion-secret"))
        self.assertEqual(result.status_code, 400)
        session.request.assert_not_called()
        http, session = self.app()
        self.assertEqual(http.post(path, json={}, auth=("fusion-user", "fusion-secret")).status_code, 403)
        session.request.assert_not_called()

    def test_resume_content_http_and_cleanup(self):
        row = linked("recruitingCandidates/C1/child/attachments", "K1", CategoryName="IRC_CANDIDATE_RESUME", DatatypeCode="FILE")
        http, session = self.app([response(row), response(raw=b"synthetic resume", headers={"Content-Type": "application/pdf"})])
        result = http.get("/candidates/C1/resumes/K1/content", auth=("fusion-user", "fusion-secret"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content, b"synthetic resume")
        self.assertIn("attachment", result.headers["content-disposition"])
        self.assertEqual(session.request.call_count, 2)

    def test_unexpected_exception_does_not_expose_details(self):
        def broken(config, cache):
            raise RuntimeError("SECRET_PASSWORD PRIVATE_EMPLOYEE")
        http = TestClient(create_app(settings(), client_factory=broken), raise_server_exceptions=False)
        result = http.get("/requisitions", auth=("fusion-user", "fusion-secret"))
        self.assertEqual(result.status_code, 500)
        self.assertNotIn("SECRET", result.text)
        self.assertEqual(result.headers["cache-control"], "no-store")

    def test_named_route_scope_cannot_be_overridden(self):
        http, session = self.app()
        for path in ("/requisitions/open?phase=Closed", "/hiring-managers/EMP1/requisitions?hiring_manager_person_number=EMP2",
                     "/recruiters/EMP1/requisitions?recruiter_person_number=EMP2", "/requisitions?hiring_manager_person_number="):
            result = http.get(path, auth=("fusion-user", "fusion-secret"))
            self.assertEqual(result.status_code, 400)
        session.request.assert_not_called()

    def test_http_move_end_to_end_with_synthetic_oracle(self):
        row = linked("recruitingJobApplications", "APP_KEY", JobApplicationId=100, CandidatePersonId=200,
                     RequisitionNumber="R1", PhaseId=10, StateId=20)
        http, session = self.app([response({"CandidateNumber": "C1", "PersonId": 200}), response(page([row])),
                                  response(status=204)], enable_updates=True)
        body = {"application_id": 100, "expected_phase_id": 10, "expected_state_id": 20, "phase_id": 30, "state_id": 40}
        result = http.post("/requisitions/R1/candidates/C1/journey", json=body, auth=("fusion-user", "fusion-secret"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["data"]["update_status"], "accepted")
        self.assertEqual(session.request.call_args.args[0], "POST")


class ConfigTests(unittest.TestCase):
    def test_environment_only_and_invalid_values(self):
        env = {"FUSION_BASE_URL": "https://fusion.example", "FUSION_USERNAME": "user", "FUSION_PASSWORD": "password"}
        with patch.dict(os.environ, env, clear=True):
            config = Settings.from_env()
            self.assertEqual(config.page_size, 200)
            self.assertNotIn("password", repr(config))
            self.assertEqual(config.fusion_username, "user")
            self.assertEqual(config.fusion_password, "password")
            self.assertFalse(config.enable_updates)
        for variable, value in (("FUSION_USERNAME", ""), ("FUSION_PASSWORD", ""),
                                ("FUSION_USERNAME", "bad:user"), ("FUSION_PASSWORD", "bad\npassword"),
                                ("FUSION_TIMEOUT", "nan"), ("FUSION_MAX_PAGES", "0"),
                                ("FUSION_BASE_URL", "http://fusion.example"),
                                ("FUSION_API_VERSION", "../bad"), ("FUSION_ENABLE_JOURNEY_UPDATES", "yes")):
            with patch.dict(os.environ, {**env, variable: value}, clear=True), self.assertRaises(ValueError):
                Settings.from_env()
