# Entity service architecture

The service groups business behavior by entity while sharing HTTP, transport,
configuration, caching, and response helpers.

| Module | Responsibility |
|---|---|
| `http/logging.py` | Request lifecycle, safe route templates, correlation response header |
| `core/observability.py` | Application logger setup and request correlation context |
| `http/api.py` | Authentication, routes, query/body validation, HTTP responses |
| `services/recruiting.py` | Compatibility facade delegating to entity services |
| `services/requisitions.py` | Oracle-side requisition filtering, details, descriptions, status summaries |
| `services/persons.py` | PersonNumber-to-PersonId verification and public-worker enrichment |
| `services/locations.py` | Recruiting geography finder and work-location queries |
| `services/candidates.py` | Candidate identity verification, search, and detail collections |
| `services/applications.py` | Applications, candidate profiles, journeys, and guarded moves |
| `services/resumes.py` | Verified attachment selection, binary downloads, ZIPs, temporary-file cleanup |
| `services/lookups.py` | Deduplicated reference coordination, batch matching, department/business-unit lookups |
| `services/reference_values.py` | Canonical IDs and explicit resolved/missing/ambiguous reference values |
| `services/entity_service.py` | Response sanitation, child collections, and partial-enrichment metadata |
| `infrastructure/oracle.py`, `infrastructure/cache.py` | Oracle HTTP calls, safe pagination/retries, bounds, shared cache and invalidation |

## Package boundaries

Paths in the table are relative to `recruit_server/`. `http/` owns the ASGI
boundary; `services/` owns entity behavior; `infrastructure/` owns Oracle transport
and caching; `core/` owns configuration, errors, and identifier helpers. Core code
must not depend on HTTP or entity services. Use explicit absolute package imports
across these folders. Keep package `__init__.py` files free of initialization side
effects.

The root `__main__.py` composes the application. Both `python -m recruit_server`
and the installed `fusion-recruit-api` command remain supported. Direct Python
imports now use the grouped paths, for example
`from recruit_server.http.api import create_app` and
`from recruit_server.services.recruiting import Recruiting`. Tests use the new
module paths, including patch targets.

## Dependencies and lifetime

HTTP handlers continue to call `Recruiting`; its methods delegate to
`Requisitions`, `Candidates`, and `Applications`. Applications receive the same
candidate service for journey reads and fresh write prechecks. Requisition
reference coordination delegates to `Persons` and `Locations`. Resumes remain
an independent entity service.

All services for an HTTP request use the same Oracle client and process cache.
Request-local deduplication and child-result reuse stay scoped to the relevant
service. This refactor creates no additional Oracle calls or independent caches.
Existing routes, response fields, authentication, filters, retry rules, and move
safeguards retain their behavior.

## Extending the service

Place entity rules in the relevant module and keep HTTP handling in `http/api.py`.
Reuse Oracle transport rather than creating sessions in entity code. Cross-entity
operations should use explicit service dependencies, as applications do for
candidate verification. Keep common helpers narrowly scoped; add a new entity
module when it owns meaningful behavior rather than one module per endpoint.

Run `python -m unittest discover -s tests -t . -v`. Existing recruiting tests
exercise the facade and entity implementations together; Oracle/cache and resume
tests cover shared infrastructure and binary handling independently.
