# Oracle endpoint contracts and support

The implementation was derived from the repository's supplied RecruitAI API
guides. This document is self-contained; the standalone application needs none
of those source files at runtime. All paths below are relative to:

```text
https://YOUR_INSTANCE/hcmRestApi/resources/11.13.18.05
```

The instance origin/version are configurable. **Tenant access is unverified.**
Mock tests establish application behavior, not Oracle privileges or external-use
support. All operations are GET except the explicitly requested application move.

## Read endpoints

| Endpoint | Contract used |
|---|---|
| `/recruitingJobRequisitions` | Number finder `findByRequisitionNumber;RequisitionNumber=...`; q filters PhaseName, StateName, RequisitionLastModifiedDate, HiringManagerId, RecruiterId |
| `/recruitingJobRequisitions/{key}` | Oracle-returned requisition item key |
| `/recruitingJobRequisitions/{key}/child/{child}` | skills, languages, otherLocations, otherWorkLocations, collaborators, publishedJobs |
| `/recruitingJobRequisitions/{key}/enclosure/{attribute}` | ExternalDescription, ExternalDescriptionHTML, ExternalShortDescription, InternalDescription, InternalDescriptionHTML, InternalShortDescription |
| `/workers` | Exact PersonNumber→PersonId for manager/recruiter list filters |
| `/publicWorkers/{PersonId}` | Verified PersonId, PersonNumber, DisplayName, FirstName, LastName, WorkEmail for hiring-manager/recruiter enrichment |
| `/recruitingLocationsLOV` | `findByGeographyId;GeographyId=...`; LocationFQN for requisition PrimaryLocationId |
| `/locations` | LocationId query; LocationName/LocationCode for PrimaryWorkLocationId |
| `/departmentsLov` | OrganizationId query; Name |
| `/hcmBusinessUnitsLOV` | BusinessUnitId query; Name |
| `/recruitingCandidates` | Keyword search finder; LastName, CandidateType, CandLastModifiedDate filters; findByPersonId finder for optional verified candidate profiles |
| `/recruitingCandidates/{CandidateNumber}` | Verify candidate number and obtain PersonId for journey scope |
| `/recruitingCandidates/{CandidateNumber}/child/{child}` | education, experience, skills |
| `/recruitingCandidates/{CandidateNumber}/child/attachments` | Metadata projection, links retained for key verification |
| `/recruitingCandidates/{CandidateNumber}/child/attachments/{key}` | Fresh metadata verification before binary retrieval |
| `/recruitingCandidates/{CandidateNumber}/child/attachments/{key}/enclosure/FileContents` | Bounded binary content; no FileUrl/UCM fallback |
| `/recruitingJobApplications` | RequisitionNumber plus optional CandidatePersonId/JobApplicationId; current snapshots, totalResults=true |
| `/recruitingJobApplications/{key}/child/{child}` | attachments metadata, education, experience, languages, licensesAndCertificates, workPreferences |

Reference queries deduplicate IDs. Person enrichment uses
`/publicWorkers/{PersonId}?fields=PersonId,PersonNumber,DisplayName,FirstName,LastName,WorkEmail&onlyData=true`.
The response PersonId must match the requested ID; no opaque worker key is guessed.
Successful item responses are cached; all selected person fields use one request.
Other references use at most 25 IDs per `in (...)` predicate, except geography's
individual finder. A numeric PersonId,
RequisitionId, or AttachedDocumentId is never fabricated into an opaque item key.
CandidateNumber and candidate PersonId remain separate identifiers.

Collections use REST-Framework-Version 4, `items`, `hasMore`, offset/limit, and
validated next links. All original filters/finders/fields are preserved across
pages. Bounds, repeated pages, unsafe links, and stalled pagination are explicit
errors with retained rows. Scalar items, descriptions, and binary downloads are
not paginated.

Oracle's [requisition collection contract](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingjobrequisitions-get.html),
[candidate collection contract](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingcandidates-get.html),
[application collection contract](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingjobapplications-get.html),
and [FileContents contract](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingcandidates-candidatenumber-child-attachments-attachmentsuniqid-enclosure-filecontents-get.html)
describe these resource families. Reference lookup contracts:
[workers](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-workers-get.html),
[recruiting geography](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitinglocationslov-get.html),
[locations](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-locations-get.html),
[departments](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-departmentslov-get.html),
[business units](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-hcmbusinessunitslov-get.html).

## Application move: separately requested write

```text
POST /recruitingJobApplications/{OracleReturnedApplicationKey}/action/move
Content-Type: application/vnd.oracle.adf.action+json
```

Oracle documents `phaseId`, `stateId`, optional `reasonId`, and optional `comments`
for this action. The server requires both target IDs and performs a fresh
candidate/requisition/application match and expected-current-state precheck before
calling it. No other Oracle write/action route is exposed. The POST is never
retried automatically, including on 429, 5xx, or timeout.

The [Oracle move action contract](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingjobapplications-jobapplicationid-action-move-post.html)
describes a hiring-team action, not an arbitrary rewrite of historical journey
events. Oracle defines permissible transitions, reasons, and business rules.
No atomic conditional-update or durable idempotency contract has been established
for this implementation. Subsequent GET confirms the observable current state.

## Support and access limitations

Oracle's [Job Applications category](https://docs.oracle.com/en/cloud/saas/human-resources/farws/api-recruiting-job-applications.html)
carries an internal-use notice. Both application reads and the requested move
retain this support uncertainty. User authorization and a documented endpoint
do not establish Oracle external-integration support or tenant privileges.

The supplied guides identify candidate read privilege
`IRC_REST_SERVICE_ACCESS_CANDIDATES_RO_PRIV` and requisition read-only duty
`ORA_IRC_REST_SERVICE_ACCESS_JOB_REQUISITIONS_RO`; tenant administrators must verify
actual grants/data scope. Workers, LOVs, attachment content, internal descriptions,
and application actions can require additional privileges. No broad administrator
grant is assumed or recommended.

No externally supported application-transition history or application-linked
interview/screening/offer retrieval has been established here. Journey reads
therefore expose current snapshots with unavailable history. There are no internal
UI fallbacks, fabricated milestones, promotion inference, or AI processing.

## Candidate-wide application history

`GET /candidates/{identifier}/history` first resolves CandidateNumber through
`recruitingCandidates/{CandidateNumber}`, or resolves an explicitly selected
PersonId using `recruitingCandidates?finder=findByPersonId;personId=...` with a
complete, unique identity check. It then reads `recruitingJobApplications` using
`q=CandidatePersonId=<verified PersonId>` on every page, without RequisitionNumber.
Returned application identities are checked against the resolved PersonId.

These records describe current application snapshots, not transition history.
No Progress History or OTBI extraction is implemented. Oracle references:
[candidate](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingcandidates-candidatenumber-get.html),
[job applications](https://docs.oracle.com/en/cloud/saas/human-resources/farws/op-recruitingjobapplications-get.html).

