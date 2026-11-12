"""Verified resume metadata and bounded private downloads; never follow FileUrl."""
import os
import re
import tempfile
import zipfile
from pathlib import Path

from recruit_server.core.errors import APIError
from recruit_server.core.identifiers import identifier


FIELDS = ("AttachedDocumentId,CategoryName,DatatypeCode,FileName,Title,UploadedFileContentType,"
          "UploadedFileLength,LastUpdateDate,DmVersionNumber")


def classify(row):
    if str(row.get("CategoryName", "")).upper() == "IRC_CANDIDATE_RESUME":
        return "oracle_category"
    if re.search(r"\b(resume|cv)\b", str(row.get("FileName", "")) + " " + str(row.get("Title", "")), re.I):
        return "filename_or_title_hint"
    return None


def remove_file(path):
    Path(path).unlink(missing_ok=True)


class Resumes:
    def __init__(self, client):
        self.client = client

    def _collection(self, number):
        return f"recruitingCandidates/{identifier(number)}/child/attachments"

    def list(self, number, *, include_hints=False, fresh=False, **paging):
        collection = self._collection(number)
        # GET /recruitingCandidates/{CandidateNumber}/child/attachments: list attachment metadata.
        # Resume category/optional filename-hint selection happens locally on these returned rows.
        result = self.client.collection(collection, {"fields": FIELDS}, fresh=fresh, **paging)
        selected = []
        for row in result["data"]:
            evidence = classify(row)
            if evidence == "oracle_category" or include_hints and evidence:
                key = self.client.item_key(row, collection)
                selected.append({
                    "attachment_id": row.get("AttachedDocumentId"), "attachment_key": key,
                    "file_name": row.get("FileName"), "title": row.get("Title"),
                    "category": row.get("CategoryName"), "content_type": row.get("UploadedFileContentType"),
                    "file_size": row.get("UploadedFileLength"), "updated_at": row.get("LastUpdateDate"),
                    "version": row.get("DmVersionNumber"), "resume_evidence": evidence,
                    "download_available": key is not None,
                })
        result["metadata"]["attachment_rows_scanned"] = result["metadata"]["returned_count"]
        result["metadata"]["returned_count"] = len(selected)
        result["metadata"]["resume_selection"] = "category_or_labeled_hint" if include_hints else "oracle_category_only"
        result["data"] = selected
        return result

    def _verified(self, number, key, include_hints):
        collection, key = self._collection(number), identifier(key)
        # GET /recruitingCandidates/{CandidateNumber}/child/attachments/{key}:
        # recheck current attachment identity/category before reading any binary content.
        row = self.client.item(collection + "/" + key, {"fields": FIELDS}, fresh=True)
        if self.client.item_key(row, collection) != key:
            raise APIError("attachment_identity", "Oracle attachment identity could not be verified", 502)
        evidence = classify(row)
        if evidence != "oracle_category" and not (include_hints and evidence):
            raise APIError("not_resume", "Attachment does not meet the selected resume evidence policy", 409)
        if row.get("DatatypeCode") not in (None, "FILE"):
            raise APIError("not_file", "Attachment is not a downloadable file", 409)
        return collection + "/" + key + "/enclosure/FileContents", row

    def content(self, number, key, *, include_hints=False):
        path, _ = self._verified(number, key, include_hints)
        handle = tempfile.NamedTemporaryFile(prefix="recruit-resume-", delete=False)
        try:
            with handle:
                # GET .../child/attachments/{key}/enclosure/FileContents: stream the verified resume bytes.
                self.client.copy_binary(path, handle, self.client.settings.max_resume_bytes)
            return handle.name
        except BaseException:
            remove_file(handle.name)
            raise

    def archive(self, number, *, include_hints=False):
        result = self.list(number, include_hints=include_hints, fresh=True)
        resumes = self.client.require_complete(result)
        if not resumes:
            raise APIError("no_resumes", "No visible attachments meet the selected resume policy", 404)
        if any(not row["attachment_key"] for row in resumes):
            raise APIError("missing_key", "A resume has no verified Oracle attachment key", 502)
        handle = tempfile.NamedTemporaryFile(prefix="recruit-resumes-", suffix=".zip", delete=False)
        try:
            total = 0
            with handle, zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for index, resume in enumerate(resumes, 1):
                    path, row = self._verified(number, resume["attachment_key"], include_hints)
                    suffix = Path(row.get("FileName") or "").suffix.lower()
                    suffix = suffix if suffix in (".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt") else ".bin"
                    # Generated names avoid path traversal, duplicates, and untrusted filenames.
                    with archive.open(f"resume-{index:04d}{suffix}", "w") as member:
                        maximum = min(self.client.settings.max_resume_bytes,
                                      self.client.settings.max_archive_bytes - total)
                        # GET .../child/attachments/{key}/enclosure/FileContents for each verified ZIP member.
                        total += self.client.copy_binary(path, member, maximum)
                    if os.fstat(handle.fileno()).st_size > self.client.settings.max_archive_bytes:
                        raise APIError("archive_limit", "Resume archive exceeded its byte limit", 502)
            if Path(handle.name).stat().st_size > self.client.settings.max_archive_bytes:
                raise APIError("archive_limit", "Resume archive exceeded its byte limit", 502)
            return handle.name
        except BaseException:
            remove_file(handle.name)
            raise
