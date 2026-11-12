# HTTP API

The public API uses **candidate** consistently. Use `/candidates/{candidate_number}`
for a profile and `/requisitions/{number}/candidates` for a requisition's candidate
applications. Existing `/applicants/...` and `/requisitions/{number}/applicants/...`
paths remain compatibility aliases with identical authentication and behavior.
New clients should use the canonical `/candidates` routes.

Except for the history route's explicit `identity_type=person_id` option,
`candidate_number` always means Oracle **CandidateNumber**, never CandidatePersonId
or JobApplicationId. Obtain it using
`GET /requisitions/{number}/candidates?include_profiles=true`, then read
`candidate_profile.data.CandidateNumber` on the matching application.

All routes require `Authorization: Basic ...` with the configured `FUSION_USERNAME` and `FUSION_PASSWORD`.
These same credentials authenticate outbound Oracle requests. JSON responses use `data` and `metadata`;
validation/authentication/action failures use an `error` object. Oracle payload
fields retain their names. Transport `links`, FileUrl, and FileContents fields
are removed from JSON; verified `oracle_key` values are added where needed.
Return Oracle HTML as untrusted data; clients must sanitize it before rendering.

## Candidate application history

```http
GET /candidates/C1/history?retrieve_all=true&limit=100&offset=0
GET /candidates/200/history?identity_type=person_id&retrieve_all=false&limit=10&offset=0
```

`identity_type` defaults to `candidate_number`; the only other value is `person_id`
(Oracle PersonId/CandidatePersonId, not JobApplicationId). Numeric candidate numbers
are still treated as candidate numbers unless `person_id` is explicitly selected.
The server verifies the candidate first, then filters applications by CandidatePersonId
without a requisition restriction. PersonId lookup must return one verified, complete
candidate match; missing/ambiguous identities stop retrieval.

`data` contains groups shaped as
`{"RequisitionNumber": "R1", "applications": [...]}`. Applications sharing a
requisition are grouped together across all fetched pages, in first-seen order.
Missing requisition numbers appear in a group with `RequisitionNumber: null`.
Each group's applications retain Oracle fields including JobApplicationId,
RequisitionNumber, JobApplicationDate, PhaseName, StateName, and LastUpdateDate
when Oracle supplies them. `metadata.candidate_identity` contains CandidateNumber
and PersonId. This endpoint returns current snapshots across visible requisitions;
it does not return historical phase/state transitions. Metadata explicitly sets
`history_status=unavailable` and describes that limitation. Progress History or a
separate verified OTBI integration is needed for historical transitions; neither
is fetched by this endpoint.

Standard collection pagination and partial-error metadata apply. `limit` is page
size (1–500); use `retrieve_all=false` for one page and continue with
`metadata.next_offset`. With the default `retrieve_all=true`, bounded pagination
follows available pages. Results reflect the Oracle service account's visibility,
and no chronological order or tenant-wide completeness is guaranteed.

Pagination counts source applications, not requisition groups. A group may span
separate requests when `retrieve_all=false`. `metadata.returned_count` counts
groups in this response; `application_count` counts fetched application rows,
and `pagination_unit` is `applications`. `next_offset`, `has_more`, and Oracle
total counts continue to describe source applications.

## Requisitions

| Method / route | Behavior and options |
|---|---|
| `GET /requisitions/open` | All visible requisitions with `PhaseName='Open'`; state is independent |
| `GET /requisitions` | List/search; optional phase, state, modified_since, hiring_manager_person_number, recruiter_person_number, resolve_references |
| `GET /hiring-managers/{person_number}/requisitions` | Resolve PersonNumber, filter HiringManagerId; optional PhaseName, StateName and other list filters |
| `GET /recruiters/{person_number}/requisitions` | Resolve PersonNumber, filter RecruiterId; optional PhaseName, StateName and other list filters |
| `GET /requisitions/statuses` | Count phase/state combinations observed in the fetched visible rows |
| `GET /requisitions/{number}` | Unique requisition by display number using Oracle's number finder |
| `GET /requisitions/by-key/{key}` | Single item using an actual Oracle-returned opaque key |

All requisition list routes (`/requisitions`, `/requisitions/open`, and both manager
routes) accept `PhaseName` and `StateName` query filters, combined with AND.
The existing `phase` and `state` names remain supported aliases. Use only one
name per filter; empty values or supplying both aliases return 400. Names and
values are case-sensitive. The open route still requires phase `Open`.

Examples (use HTTP Basic authentication with your configured Fusion credentials):

```http
GET /requisitions?PhaseName=Open&StateName=Posted&retrieve_all=false&limit=10
GET /hiring-managers/EMP001234/requisitions?PhaseName=Open&StateName=Posted
GET /recruiters/EMP001234/requisitions?PhaseName=Open&StateName=Posted
GET /requisitions/open?StateName=Posted
```

On both person-number routes, phase and state restrictions combine with the
resolved manager/recruiter restriction using AND. Either filter may be omitted.
`EMP001234` is a synthetic person number. In Postman, use the **Params** tab to
set `PhaseName` and `StateName`; URL-encode values when constructing URLs manually.

Use phase/state values from your Oracle tenant. These filters apply to requisition
lists, not candidate or journey routes.

Collection options: `limit` (1–500; configured default 200), `offset` (default 0),
`retrieve_all` (default true). Detail options: `include_children`,
`include_descriptions` (both default false), and `resolve_references` (default true).
Reference resolution defaults to true on all requisition list and detail routes,
including hiring-manager, recruiter, and by-key routes. Set
`resolve_references=false` to skip lookup enrichment. Manager filters combine with AND;
an explicit manager path value cannot be overridden in the query. The open route
cannot be changed to a different phase. Use `/requisitions?phase=...` for a custom
tenant/language phase name. Date filters apply Oracle's documented timestamp
attributes and do not create synchronization guarantees.

Each returned requisition includes `reference_resolution`. Original Oracle IDs
remain unchanged. For example (synthetic values):

```json
{
  "reference_resolution": {
    "hiring_manager": {
      "person_number": "EMP001234",
      "person_number_status": "resolved",
      "name": "Example Manager",
      "DisplayName": "Example Manager",
      "WorkEmail": "manager@example.test",
      "FirstName": "Example",
      "LastName": "Manager",
      "status": "resolved"
    },
    "recruiter": {
      "person_number": "EMP005678",
      "person_number_status": "resolved",
      "name": "Example Recruiter",
      "DisplayName": "Example Recruiter",
      "WorkEmail": "recruiter@example.test",
      "FirstName": "Example",
      "LastName": "Recruiter",
      "status": "resolved"
    },
    "recruiting_location": {"name": "India, Mumbai", "status": "resolved"},
    "work_location": {"name": "Mumbai Office", "status": "resolved"}
  }
}
```

`HiringManagerId` and `RecruiterId` resolve through
`/publicWorkers/{PersonId}?fields=PersonId,PersonNumber,DisplayName,FirstName,LastName,WorkEmail&onlyData=true`.
The returned PersonId must match the requested ID. `DisplayName` also populates
`name`; `person_number` comes from PersonNumber. `WorkEmail` contains the returned
work email, or null when absent or unavailable. IDs are deduplicated and successful
reads use the process cache. This replaces worker/name enrichment calls.
`FirstName` and `LastName` are requested in the same public-worker call and returned
without additional lookups. Missing values remain null; names are never inferred
from DisplayName.

`PrimaryLocationId` resolves to recruiting geography (`LocationFQN`);
`PrimaryWorkLocationId` resolves to a work-location name, falling back to its code.
Department and business-unit names are also resolved when their IDs exist.
Lookup failures retain requisitions with null reference values, explicit statuses,
and partial response metadata. Person-number filters on manager/recruiter list
routes still resolve PersonNumber to PersonId through `/workers`.

### Filtering and performance

PhaseName, StateName, modified_since, and resolved hiring-manager/recruiter IDs
are passed to Oracle in a combined `q` predicate, retained on every page. The
server does not retrieve an unfiltered requisition dataset and filter it locally.
Requisition-number detail requests use Oracle's number finder. Local identity
checks validate the returned records; status summaries aggregate fetched rows.

Reference IDs are deduplicated across roles and requisitions. Public-worker fields
are fetched together in one cached item request per distinct person. Other
reference responses are indexed by ID once per batch rather than rescanned for
each requested ID. Successful reads share the bounded process cache (default TTL
60 seconds). A cold request still performs sequential lookups for distinct people.
For interactive requests, use `retrieve_all=false&limit=10` to bound the parent
page; `resolve_references=false` skips enrichment if only Oracle IDs are needed.
Avoid `include_children` and `include_descriptions` unless required. No throughput
or tenant latency improvement has been benchmarked.

Requisition children: skills, languages, otherLocations, otherWorkLocations,
collaborators, publishedJobs. Each has independent pagination/retrieval metadata.
Languages are requisition localization records, not inferred candidate requirements.
Descriptions contain six independent external/internal text/HTML/short enclosure
results. Posting dates remain Oracle posting dates, not inferred opening/closing dates.

## Candidates, applications, and journeys

| Method / route | Behavior and options |
|---|---|
| `GET /requisitions/{number}/candidates` | Application records for a requisition; paging, include_children, include_profiles |
| `GET /requisitions/{number}/candidates/{candidate_number}/journey` | Resolve candidate number to PersonId; fetch application snapshots for both identities; paging |
| `POST /requisitions/{number}/candidates/{candidate_number}/journey` | Move one verified application to target phase/state; JSON body described below |
| `GET /candidates` | Candidate search; paging, keywords, last_name, candidate_type, modified_since |
| `GET /candidates/{candidate_number}` | Verified candidate profile |
| `GET /candidates/{candidate_number}/details` | Consolidated profile, education, experience, and skills; paging per child collection |
| `GET /candidates/{candidate_number}/details/{child}` | Exactly education, experience, or skills; paging |

The requisition candidate collection returns one row per application, with CandidatePersonId,
CandidateName, JobApplicationId, and current phase/state where supplied. These IDs
do not imply CandidateNumber. `include_profiles=true` resolves each distinct
candidate PersonId through Oracle's documented candidate finder, verifies a unique
match, and adds `candidate_profile.data.CandidateNumber`. Optional profiles are
cached and not fetched once per repeated application. Failed profiles remain
explicitly unavailable without dropping application rows.

Application `include_children=true` fetches attachments (metadata only), education,
experience, languages, licensesAndCertificates, and workPreferences. No nested
children or application attachment binaries are retrieved. Parent pagination
finishes before child enrichment. Child collections always retrieve all within
configured bounds, using the parent page size; inspect their individual metadata.

Journey data includes each application's actual application date and current
phase/state. `transitions`, `interviews`, and `screenings` are empty with
`history_status=unavailable`; `offer_status` is null. This is not a full historical
journey. Successful writes do not manufacture history entries.

### Consolidated candidate details

Use `GET /candidates/{candidate_number}/details` with the same Basic authentication
as other endpoints. It returns `data.profile`, `data.education`, `data.experience`,
and `data.skills`. The profile is a verified candidate record; each detail section
contains its own `data` and `metadata` envelope.

```http
GET /candidates/C1/details?retrieve_all=false&limit=10&offset=0
```

Paging parameters apply independently to each child collection. By default, all
three collections retrieve all pages within configured bounds. Inspect each
section's pagination metadata to continue that collection using the existing
`/details/{child}` endpoint. A failed or partial detail section does not discard
the profile or successful sections: HTTP 200 includes top-level
`metadata.retrieval_status=partial` and `metadata.failed_details`. Profile lookup
or identity failures stop the request before child reads. Resume binaries and
application journeys remain separate endpoints.

A cold request makes one profile request plus at least one request per child
collection; pagination can add requests. Successful reads reuse the existing
cache. Existing profile and individual-detail routes remain available.

### Move body

Required positive integer fields: `application_id`, `phase_id`, `state_id`,
`expected_phase_id`, `expected_state_id`. Optional: positive integer `reason_id`
and text `comments` (at most 4000 characters). IDs must fit signed 64-bit values.
Unknown fields, duplicate JSON keys, and request bodies over 16 KiB are rejected.
Only `application/json` requests are accepted. GET operations never move an application.

Updates require `FUSION_ENABLE_JOURNEY_UPDATES=true`. A stale expected phase/state
returns 409 before POST. Success means Oracle accepted the action, not that a
subsequent read has confirmed the requested state. GET the journey after the move.
Uncertain write outcomes include `retry_safe=false` and require reconciliation.

## Resumes

| Method / route | Behavior and options |
|---|---|
| `GET /candidates/{candidate_number}/resumes` | Resume metadata; paging and include_hints |
| `GET /candidates/{candidate_number}/resumes/{attachment_key}/content` | One verified binary download; include_hints |
| `GET /candidates/{candidate_number}/resumes/archive` | ZIP of every visible selected resume; include_hints |

`include_hints=false` defaults to category evidence only. Setting it to true also
includes filename/title matches labeled `filename_or_title_hint`. An item with no
verified attachment link has `download_available=false`; its numeric
AttachedDocumentId is never used as the key. ZIPs use safe generated filenames and
complete-source pagination. An empty ZIP selection returns 404. A failed member
or incomplete attachment listing fails the ZIP request; partial ZIPs are not sent.

Binary responses use download disposition and a bounded private temporary file.
Single-file content is served as application/octet-stream; archives as application/zip.
Neither endpoint takes an arbitrary URL or local filesystem path.

## Request correlation

Every HTTP response includes a generated `X-Request-ID`. Use this value to find
the corresponding request and Oracle-call events in the launcher's terminal logs.
Logs show status, timing, cache use, and collection retrieval status without raw
request paths, filter values, authorization headers, or response bodies.
`X-Elapsed-Ms` measures time until response headers; the `request_finished` log
measures the full response lifetime, including downloads.

## Operational routes and errors

`GET /health` checks only the local server, not Oracle connectivity.
`GET /capabilities` reports limits, cache TTL, update configuration, and unavailable
history. Both are authenticated and make no Oracle calls.

| HTTP status | Meaning |
|---|---|
| 200 | Successful result or retained partial collection; inspect metadata |
| 400 / 413 / 415 | Invalid arguments, excessive move body, or wrong content type |
| 401 | Missing/incorrect API Basic authentication |
| 403 | Journey writes disabled locally |
| 404 | No visible requested item/resume selection, or unknown route |
| 409 | Ambiguous identity, stale phase/state, or attachment not a resume/file |
| 502 | Oracle failure, unsafe/invalid Oracle response, or incomplete required identity lookup |
| 500 | Sanitized unexpected server failure |

Oracle status codes are preserved as `oracle_status` in sanitized errors; an Oracle
401/403 is an upstream account issue rather than failed server Basic authentication.
There is no automatic fallback to a different API family or unfiltered request.
Unknown/repeated query parameters are rejected. Redirects and credential-bearing
pagination links outside the configured Oracle resource origin are rejected.
