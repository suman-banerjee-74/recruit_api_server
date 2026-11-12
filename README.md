# Standalone Oracle Fusion Recruiting API

An authenticated Python HTTP server for requisitions, candidates, resumes, and
application journeys. Copy this entire folder to another location to run it.
It imports only its own `recruit_server` package and installed dependencies;
it never reads the parent project's code, configuration, or `.env` at runtime.

## Setup

Use Python 3.10+:

```sh
cd recruit_api_server
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
cp .env.example .env
chmod 600 .env
```

Edit this folder's `.env` with `FUSION_USERNAME` and `FUSION_PASSWORD` for both Oracle access and API authentication.
Load it explicitly in your shell, then start the server:

```sh
set -a
source ./.env
set +a
python -m recruit_server
```

The application reads **environment variables only**. It does not automatically
load any `.env` file. Shell-quote values containing spaces or special characters
in the sourced file. A service manager may inject the variables instead.
The installed `fusion-recruit-api` command starts the same application.

The server binds to `127.0.0.1:8010`. Keep Basic credentials on loopback or use a
private HTTPS reverse proxy for remote clients. Do not publish plain HTTP.
Every route, including health and capabilities, requires HTTP Basic authentication.
The API uses the same `FUSION_USERNAME` and `FUSION_PASSWORD` as the Oracle service account: authenticated
callers share that account's visibility and, when enabled, write permissions.

## Requested functions

| Function | Server endpoint |
|---|---|
| All open requisitions | `GET /requisitions/open` |
| Specific requisition by number | `GET /requisitions/{number}` |
| All requisitions by hiring-manager person number | `GET /hiring-managers/{person_number}/requisitions` |
| All requisitions by recruiter person number | `GET /recruiters/{person_number}/requisitions` |
| All candidate applications for a requisition | `GET /requisitions/{number}/candidates` |
| All resumes, metadata or ZIP | `GET /candidates/{candidate_number}/resumes` or `/resumes/archive` |
| Candidate journey for a requisition | `GET /requisitions/{number}/candidates/{candidate_number}/journey` |
| Update application phase/state | `POST /requisitions/{number}/candidates/{candidate_number}/journey` |

“Recruiting manager” maps to Oracle's **RecruiterId**, as in the existing API
reference. A recruiter's line-management hierarchy is not inferred. Both manager
endpoints resolve an exact PersonNumber through `workers`, verify a unique
PersonId, then filter `HiringManagerId` or `RecruiterId`. Failed or ambiguous
resolution stops the operation before a requisition request.

Example requests (curl prompts for the API password):

```sh
curl --user "$FUSION_USERNAME" 'http://127.0.0.1:8010/requisitions/open'
curl --user "$FUSION_USERNAME" 'http://127.0.0.1:8010/requisitions/R1?include_children=true&include_descriptions=true&resolve_references=true'
curl --user "$FUSION_USERNAME" 'http://127.0.0.1:8010/hiring-managers/EMP001234/requisitions?PhaseName=Open&StateName=Posted'
curl --user "$FUSION_USERNAME" 'http://127.0.0.1:8010/recruiters/EMP001234/requisitions?PhaseName=Open&StateName=Posted'
curl --user "$FUSION_USERNAME" 'http://127.0.0.1:8010/requisitions/R1/candidates?include_profiles=true'
curl --user "$FUSION_USERNAME" 'http://127.0.0.1:8010/candidates/C1/resumes'
curl --user "$FUSION_USERNAME" --fail --output resumes.zip 'http://127.0.0.1:8010/candidates/C1/resumes/archive'
```

`R1`, `C1`, and `EMP001234` are synthetic placeholders. Keep downloaded resumes
in private storage with appropriate retention. Avoid overwriting existing files
with curl's `--output`; server-side temporary files are exclusive and private.

## Candidate naming

Use `/candidates` consistently for candidate search, profiles, details, resumes,
and requisition journeys. For example:

```http
GET /requisitions/13726/candidates?include_profiles=true
GET /candidates/<CandidateNumber>
GET /candidates/<CandidateNumber>/details
```

`CandidateNumber` comes from `candidate_profile.data.CandidateNumber`; it is not
CandidatePersonId. Requisition candidate lists contain application records, so a
candidate may have multiple applications. Existing `/applicants` URLs remain
compatibility aliases; use `/candidates` for new integrations.

## Consolidated candidate details

Fetch a candidate profile, education, experience, and skills in one API response:

```http
GET /candidates/<CandidateNumber>/details?retrieve_all=false&limit=10
```

Use the usual Fusion Basic authentication. The response contains `data.profile`
and separate `data.education`, `data.experience`, and `data.skills` envelopes with
per-section pagination and errors. The default retrieves all bounded pages.
Partial detail failures retain successful sections and are marked in metadata.

## Requisition phase and state filters

Both `PhaseName` and `StateName` are optional on these list endpoints:

| Scope | Example request |
|---|---|
| All requisitions | `GET /requisitions?PhaseName=Open&StateName=Posted` |
| Hiring-manager person number | `GET /hiring-managers/EMP001234/requisitions?PhaseName=Open&StateName=Posted` |
| Recruiter person number | `GET /recruiters/EMP001234/requisitions?PhaseName=Open&StateName=Posted` |
| Open requisitions | `GET /requisitions/open?StateName=Posted` |

Filters combine with AND, including the person-number restriction. Omit either
filter to leave that field unrestricted; `/requisitions/open` always restricts
phase to `Open`. Values are case-sensitive tenant names; `Posted` is an example.
The aliases `phase` and `state` remain supported. Do not supply both names for
the same filter or an empty value. In Postman, add these names and values in the
**Params** tab. Pagination options such as `retrieve_all=false&limit=10` can be
combined with the filters.

## Filtering performance

Requisition filters run in Oracle using `q` (phase, state, modified date, and
resolved manager/recruiter IDs), with the same predicate on every page. They are
not applied to a locally downloaded unfiltered dataset. Reference lookups reuse
deduplicated IDs, cached person records, and indexed batch responses. For a small
interactive request, use `retrieve_all=false&limit=10`; omit optional child and
description enrichment, or set `resolve_references=false` when only IDs are needed.

## Pagination and caching

Collection endpoints default to `retrieve_all=true`, following all available
pages within configured page, item, and aggregate byte bounds. For one page use:

```text
?retrieve_all=false&limit=100&offset=0
```

`limit` is page size (1–500), not total row count. Continue at
`metadata.next_offset`. Resume filtering and status summaries operate on fetched
source rows, so their output count can be smaller than the underlying page.
`metadata.pagination_complete` means the requested range was exhausted, not a
complete tenant-wide view. `is_complete` remains false for that reason.

Inspect `metadata.retrieval_status`, `errors`, `has_more`, and nested enrichment
metadata. Later-page failures retain earlier rows and return a partial result.
An unavailable collection returns HTTP 502; partial data returns HTTP 200 with
explicit errors. No filtered empty request is retried as an unfiltered extraction.
Offset pagination is not a transactionally consistent snapshot during concurrent
Oracle updates; timestamp filters are not a deletion-aware synchronization engine.

A bounded, process-local TTL/LRU cache coalesces identical simultaneous reads,
returns independent copies, and caches successful page/item/lookup reads. Default
TTL is 60 seconds; set `RECRUIT_CACHE_TTL=0` to disable shared caching. Cache scope
is one server process with fixed Oracle credentials. Restarts clear it. Partial
collections are not shared-cache entries. Identity and reference results can be
stale within the TTL; write prechecks and binary verification bypass the cache.
Every attempted application move invalidates the whole process cache, including
in-flight cache insertions. Multiple server processes have independent caches;
use the provided single-process launcher for these invalidation guarantees.

## Journey reads and updates

For a candidate's applications across all visible requisitions, use:

```http
GET /candidates/C1/history?retrieve_all=true&limit=100&offset=0
GET /candidates/200/history?identity_type=person_id&retrieve_all=false&limit=10&offset=0
```

The first route uses CandidateNumber; the second explicitly uses PersonId.
Identity is verified before fetching applications filtered only by CandidatePersonId.
Results are grouped by `RequisitionNumber`, each with an `applications` list.
Pagination still applies to application rows, so one requisition may span pages.
Application rows retain their Oracle fields, including application/requisition IDs,
application dates, and current phase/state when supplied. This is application
history across requisitions, with `metadata.history_status=unavailable` for actual
phase/state transition history. See [the API contract](docs/API.md#candidate-application-history).

Journey GET returns the current Oracle application snapshot. Transition,
interview, screening, and offer histories remain explicitly unavailable; no dates
or milestones are inferred. An application can have multiple snapshots/rows;
the GET returns matching applications without silently selecting one.

Set `FUSION_ENABLE_JOURNEY_UPDATES=true` and restart to enable the requested write
operation. Oracle must grant the service account the necessary hiring-team/action
access. Obtain valid target phase/state/reason IDs from your tenant's recruiting
configuration; the server does not guess them. Save this synthetic example to
`move.json`, replacing IDs with verified values:

```json
{
  "application_id": 100,
  "expected_phase_id": 10,
  "expected_state_id": 20,
  "phase_id": 30,
  "state_id": 40,
  "reason_id": 50,
  "comments": "Move requested by the hiring team"
}
```

```sh
curl --user "$FUSION_USERNAME" --request POST \
  --header 'Content-Type: application/json' --data-binary @move.json \
  'http://127.0.0.1:8010/requisitions/R1/candidates/C1/journey'
```

`reason_id` and `comments` are optional. The server verifies a fresh candidate,
application, and requisition match, checks the expected current phase/state, and
uses the Oracle-returned application key. Local moves for the same candidate and
requisition are serialized within the process. This precheck is **not an atomic
Oracle concurrency guarantee**; another Oracle client can change the application
after it. Oracle's business rules decide whether a move is permitted.

The action is sent once, with no automatic write retries. Success reports
`update_status=accepted`; retrieve the journey again to confirm the observed
state. A timeout or server error can mean `update_outcome=unknown`. Reconcile with
a fresh GET before deciding whether another move is appropriate. There is no
durable idempotency-key store or historical journey database.

## Additional inherited capabilities

The standalone server also includes candidate search/profile/details, observed
requisition status summaries, optional requisition and application children,
bounded description enclosures, and default reference resolution. Reference IDs are
deduplicated. People use
`publicWorkers/{PersonId}?fields=PersonId,PersonNumber,DisplayName,FirstName,LastName,WorkEmail&onlyData=true`
with verified IDs and cached item responses. Other references batch up to 25
IDs; recruiting geography uses its documented individual finder. Original Oracle field names and IDs are retained.
Requisition lists and details resolve hiring-manager and recruiter names and
person numbers by default in `reference_resolution.hiring_manager` and
`reference_resolution.recruiter` (`name`, `person_number`, `DisplayName`, `WorkEmail`, `FirstName`, `LastName`, and resolution statuses).
`reference_resolution.recruiting_location` and `.work_location` contain resolved
location names. First and last names are fetched in the same public-worker call;
missing values remain null. This also applies to both person-number list routes. Set
`resolve_references=false` to skip these lookups. Missing or ambiguous values
remain null with explicit statuses.
Only verified matches supply names; unavailable or ambiguous names stay null.

Resume lists default to Oracle's `IRC_CANDIDATE_RESUME` category. Opt into labeled
filename/title hints with `include_hints=true`. Downloads verify metadata and
matching attachment links before reading FileContents. They never follow FileUrl,
redirects, cross-origin links, or guessed opaque keys. Files are bounded, private
temporary files and deleted after the response; failed transfers publish no
response file. ZIP retrieval fails explicitly if any selected resume cannot be
verified or downloaded, rather than silently omitting it. A process crash may
leave private temporary files for host-level cleanup.

JSON responses are `Cache-Control: no-store`; all HTTP responses include
`X-Elapsed-Ms`. Collection metadata indicates cache hits. Raw Uvicorn access logs and raw error
logging are disabled by the launcher to avoid exposing person numbers, record
keys, credentials, or resumes. No AI processing, arbitrary URL proxy, or MCP server
is included.

## Operational logs

Start normally with `python -m recruit_server` (or `fusion-recruit-api`). The
launcher prints application INFO logs to the terminal, including startup port,
request start/completion, HTTP status and elapsed milliseconds, Oracle resource
and response status, retry attempts, cache hits, and collection retrieval status.

```text
event=request_started request_id=... method=GET
event=oracle_response request_id=... method=GET resource=recruitingJobRequisitions attempt=1 status=200 elapsed_ms=125.4
event=oracle_collection request_id=... cache_hit=False pages=1 returned_count=10 retrieval_status=retrieved
event=request_finished request_id=... method=GET route=/requisitions status=200 outcome=completed elapsed_ms=130.2
```

Match the response's `X-Request-ID` header in Postman to `request_id` in the logs.
Cached responses may have no `oracle_started` events because no network call was
needed. `oracle_started` without a corresponding response yet means the transport
is waiting for Oracle response headers; body streaming can continue afterward.
Inspect `oracle_collection` retrieval status and response metadata for partial
results even when HTTP status is 200. Oracle timing covers transport through
response headers; request completion timing covers the complete API response.

Only registered route templates and known Oracle resource names are logged.
Credentials, actual record/person IDs, raw URLs/query values, bodies, and raw
exception text are excluded. Logs go to stderr; no log files are created.
Embedding the ASGI application directly requires configuring the `recruit_server`
logger (or calling `core.observability.configure_logging()`).

## Project layout and verification

```text
recruit_server/
  __main__.py           command-line launcher
  http/
    api.py              HTTP routes, authentication, request validation
  services/
    recruiting.py       facade composing entity services
    requisitions.py     requisition queries, details, descriptions, summaries
    persons.py          worker IDs and public-worker enrichment
    locations.py        geography and work-location queries
    candidates.py       candidate verification, search, profile details
    applications.py     applications, journeys, guarded moves
    resumes.py          resume selection, downloads, ZIPs, cleanup
    lookups.py          reference coordination and batching
    reference_values.py reference ID and resolution helpers
    entity_service.py   shared response and child-enrichment helpers
  infrastructure/
    oracle.py           Oracle transport, pagination, bounds, retries
    cache.py            shared bounded TTL/LRU cache and invalidation
  core/
    config.py           environment settings
    errors.py           structured errors
    identifiers.py      identifier validation and output sanitation
tests/                  offline unittest tests and mocked HTTP helpers
docs/                   API, configuration, architecture, Oracle contracts
```

```sh
python -m unittest discover -s tests -t . -v
```

Tests cover authentication, redaction, manager identity, partial pagination,
URL safety, retry limits, cache bounds/invalidation, downloads, and move safeguards.
No test requires credentials or calls a live Oracle tenant.

Current validation: 49 logging, candidate, Oracle/cache, recruiting, and resume tests
passed; Python syntax checks passed. HTTP tests could not run because Starlette
is unavailable in the current environment. Tests use synthetic offline data;
Oracle tenant behavior has not been verified.

See [architecture](docs/ARCHITECTURE.md), [API documentation](docs/API.md), [configuration](docs/CONFIGURATION.md), and
[Oracle endpoint contracts and support limits](docs/ORACLE_APIS.md).
