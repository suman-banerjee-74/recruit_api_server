import io
import os
import stat
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from recruit_server.core.errors import APIError
from recruit_server.services.resumes import Resumes, remove_file
from tests.support import client, linked, page, response, settings


def resume(key="K1", **extra):
    return linked("recruitingCandidates/C1/child/attachments", key, CategoryName="IRC_CANDIDATE_RESUME",
                  DatatypeCode="FILE", FileName="../../unsafe.pdf", **extra)


class ResumeTests(unittest.TestCase):
    def test_category_and_hints_are_distinct_and_keys_are_not_numeric_ids(self):
        api, _ = client([response(page([resume(), {"AttachedDocumentId": 99, "FileName": "resume.pdf"}]))])
        result = Resumes(api).list("C1")
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["attachment_key"], "K1")
        result = Resumes(api).list("C1", include_hints=True)
        self.assertEqual(len(result["data"]), 2)
        self.assertIsNone(result["data"][1]["attachment_key"])
        self.assertEqual(result["data"][1]["resume_evidence"], "filename_or_title_hint")

    def test_no_binary_when_attachment_link_mismatches(self):
        row = resume("WRONG")
        row["FileUrl"] = "https://evil.example/resume"
        api, session = client([response(row)])
        with self.assertRaises(APIError):
            Resumes(api).content("C1", "K1")
        self.assertEqual(session.request.call_count, 1)

    def test_private_file_and_no_overwrite(self):
        api, _ = client([response(resume()), response(raw=b"synthetic", headers={"Content-Type": "application/pdf"})])
        path = Resumes(api).content("C1", "K1")
        try:
            self.assertEqual(Path(path).read_bytes(), b"synthetic")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertNotIn("unsafe", path)
        finally:
            remove_file(path)

    def test_archive_contains_all_resumes_with_safe_generated_names(self):
        api, _ = client([response(page([resume("K1"), resume("K2")])), response(resume("K1")),
                          response(raw=b"one", headers={"Content-Type": "application/pdf"}), response(resume("K2")),
                          response(raw=b"two", headers={"Content-Type": "application/pdf"})])
        path = Resumes(api).archive("C1")
        try:
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), ["resume-0001.pdf", "resume-0002.pdf"])
                self.assertEqual(archive.read("resume-0002.pdf"), b"two")
        finally:
            remove_file(path)

    def test_archive_rejects_partial_listing(self):
        api, session = client([response(page([resume()], True)), response(status=403)])
        with self.assertRaises(APIError):
            Resumes(api).archive("C1")
        self.assertEqual(session.request.call_count, 2)

    def test_failed_download_removes_partial_file(self):
        api, _ = client([response(resume()), response(raw=b"too large", headers={"Content-Type": "application/pdf"})],
                         config=settings(max_resume_bytes=2))
        import tempfile
        original = tempfile.NamedTemporaryFile
        paths = []
        def record(*args, **kwargs):
            handle = original(*args, **kwargs)
            paths.append(handle.name)
            return handle
        with patch("recruit_server.services.resumes.tempfile.NamedTemporaryFile", side_effect=record), self.assertRaises(APIError):
            Resumes(api).content("C1", "K1")
        self.assertTrue(paths)
        self.assertTrue(all(not Path(path).exists() for path in paths))
