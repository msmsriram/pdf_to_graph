# AWS Scalable Application Plan

## Decision summary

S3, DynamoDB, and EKS are a sound core, but they are not a complete production platform.

- Use **S3** for immutable uploads and large artifacts: originals, normalized PDFs, page images, chart crops, exports, and optional raw provider responses.
- Use **DynamoDB** for compact operational records: users, permissions, batches, documents, jobs, page/chart state, version pointers, usage, budgets, and audit metadata. Keep large JSON in S3 because DynamoDB items are limited to 400 KB.
- Use one **EKS Auto Mode** cluster for separate frontend, API, validation/scanning, and processing-worker Deployments. Do not use pod-local files as durable storage.
- In the selected deployment, serve the React build from a frontend container in EKS through ALB and CloudFront. Frontend and backend remain separate images and Deployments, not containers in one pod.
- The minimal durable platform is **S3, DynamoDB, SQS with a DLQ, Cognito, ECR, ALB, CloudFront, Secrets Manager, CloudWatch, and CloudTrail**, inside a VPC. Private workers require NAT for calls to the external LLM provider.
- Route 53 and ACM are conditional on using a custom domain. WAF is recommended for production. Choose either GuardDuty Malware Protection for S3 or a maintained ClamAV scanner in EKS.
- Do not add customer-managed KMS initially: S3 and DynamoDB already provide default encryption. EBS is not application storage.

## Required product capabilities

### Upload and storage

- A batch may contain multiple PDF, DOCX, PPT/PPTX, PNG, JPEG, or WebP files.
- Record who uploaded each file, tenant, batch, original name, detected MIME type, size, checksum, source format, and timestamps.
- Validate extension, MIME, and file signature; enforce per-user limits for formats, file count, bytes, and pages.
- Upload directly from the browser to tenant-scoped S3 keys with short-lived presigned multipart URLs.
- Quarantine new objects until malware scanning and validation succeed.
- Retain the immutable original and separately store canonical files, pages, crops, outputs, and exports.
- Enable S3 Block Public Access, default SSE-S3 encryption, versioning, incomplete-multipart cleanup, and retention/lifecycle rules. Move to a customer-managed KMS key only if policy requires it.

Suggested key layout:

```text
tenant/{tenantId}/user/{userId}/batch/{batchId}/document/{documentId}/
  source/original.ext
  canonical/document.pdf
  pages/page-0001.png
  charts/{chartId}/crop.png
  charts/{chartId}/versions/{version}.json
  exports/{exportId}.{png|jpeg|svg|csv|json}
  llm/{callId}/response.json
```

The API generates every key. A client must never choose an arbitrary S3 key.

### Processing

- PDF: validate, extract native text/metadata, and rasterize pages.
- DOCX/PPT/PPTX: convert in an isolated, resource-limited LibreOffice worker to PDF, then use the PDF pipeline.
- Images: decode safely, apply EXIF orientation, normalize color, and create canonical PNG pages.
- Create a durable job per document; a batch is a parent grouping and a failure in one document must not fail the others.
- Persist every state transition. Support cancellation, retries, partial success, page-level retry, and restart recovery.
- Store structured chart data, schema/model/prompt versions, confidence, usage, cost, and S3 artifact pointers.
- Retain only provider-returned content or reasoning summaries. Do not expect or store hidden chain-of-thought.

### Accounts, approval, and administration

Use Cognito User Pools for authentication, password reset, MFA, and tokens. Use coarse Cognito groups such as `platform-admin`, `tenant-admin`, `user`, and `auditor`. Store fine-grained application permissions in DynamoDB and enforce them in backend middleware on every request.

Signup approval flow:

```text
Registration request -> email verification -> pending admin review
  -> approve -> Cognito AdminCreateUser -> temporary password -> forced password change -> active
  -> reject/expire
```

Each verified request appears in the application’s admin board. The frontend retrieves pending requests through the API; no separate email or notification service is required initially. Approval, rejection, role assignment, and later access changes are application audit events.

Admins may create users and issue temporary credentials, but the application must never store or reveal a permanent password. Require MFA for administrators.

Required permission controls:

| Area | Example controls |
|---|---|
| Application | Login, use application, suspend account |
| Upload UI | Show upload component, permit upload, permit bulk upload |
| Formats | PDF, DOCX, PPT/PPTX, image independently allowed |
| Limits | Files per batch, file bytes, pages, jobs/day, concurrent jobs |
| Results | View/edit own, view any, retry, cancel |
| Export | PNG, JPEG, SVG, CSV, JSON, original download |
| Cost | View estimate, own usage, all-user usage |
| Analytics | Own dashboard or tenant/global dashboard |
| Administration | Approve, invite, suspend, assign roles, set budgets, view audit |

Use versioned role templates plus per-user overrides:

```text
effective permissions = role allows + user allows - user denies
```

Explicit deny wins. “Grant all” assigns a named, versioned `FULL_ACCESS` template; it is not a frontend bypass. Changes take effect at the backend on the next request.

### Quotas, cost controls, and analytics

- Configure daily/monthly user and tenant budgets, soft alerts, hard limits, and concurrency limits.
- Before accepting a job, transactionally reserve a conservative maximum cost in DynamoDB.
- Before costly model calls, check cancellation, entitlement, remaining reservation, and provider capacity.
- Settle actual usage after each call and release unused reservation.
- Use integer monetary micro-units, an immutable usage event ID, model/provider request ID, token counts, latency, retry count, and a price-version snapshot.
- Make every settlement idempotent; retries must not double-count.
- AWS Budgets protects the AWS account but is not a real-time per-user OpenAI budget system. The application ledger is authoritative.

User dashboard:

- Uploads/documents/pages by day, week, and month.
- Breakdown by PDF, Office file, and image.
- Completed, failed, cancelled, and partial jobs.
- LLM calls, input/cached/reasoning/output tokens, latency, and cost.
- Spent, reserved, remaining, and forecast budget.

Admin dashboard:

- Filter by tenant, user, format, model, status, and date.
- Top users by documents, pages, and cost.
- Pending registrations, active/suspended users, users near budget limits.
- Queue age, failures, retries, provider throttling, and model-cost trends.
- Audited export of the usage ledger.

Use DynamoDB daily/monthly rollups for the interactive dashboard. Send detailed immutable usage/audit events through DynamoDB Streams to S3 for Athena/Glue and optionally QuickSight when deeper BI is needed.

## AWS service plan

### First production release

| Capability | AWS service |
|---|---|
| DNS and TLS | Route 53 and ACM only for a custom domain |
| Frontend/CDN | Frontend EKS Deployment behind ALB and CloudFront |
| Edge protection | AWS WAF recommended for production |
| Authentication | Cognito User Pools |
| API/WebSocket ingress | ALB and AWS Load Balancer Controller |
| Frontend, API and workers | EKS Auto Mode Deployments/Jobs |
| Images | ECR |
| Raw/generated artifacts | S3 with default encryption and versioning |
| Metadata/state/RBAC/usage | DynamoDB with PITR |
| Durable work | SQS Standard queues and DLQs |
| Secrets | Secrets Manager through EKS Pod Identity |
| Operations | CloudWatch logs, metrics, alarms, and tracing |
| AWS audit | CloudTrail; separate application audit records |
| Upload scanning | GuardDuty Malware Protection for S3 or maintained ClamAV worker |

### Add as scale or complexity grows

| Need | Addition |
|---|---|
| Durable visual orchestration and fan-out/fan-in | Step Functions Standard |
| Cross-pod WebSocket fan-out | ElastiCache Serverless for Valkey/Redis, or API Gateway WebSocket/AppSync |
| Rich analytics | DynamoDB Streams, S3 Parquet, Glue, Athena, QuickSight |
| Complex relationship/attribute policies | Amazon Verified Permissions |
| Automated worker/node scaling | HPA plus Karpenter |
| Compliance-grade immutable audit | Separate S3 audit bucket with Object Lock |
| Regional disaster recovery | S3 cross-region replication and, only if justified, DynamoDB global tables |

## EKS deployment and source-control guidance

Frontend and backend **can** run as two containers in one pod, but they should not for this application. Containers in one pod share an IP and lifecycle and are deployed, restarted, and scaled together. That pattern is intended for tightly coupled sidecars. Here, the API, conversion workers, LLM workers, and static UI have different scaling and failure characteristics.

Recommended workloads:

- `api`: stateless FastAPI REST/status/WebSocket service, at least two replicas across Availability Zones.
- `conversion-worker`: isolated Office/PDF conversion and page rendering, on a CPU/memory worker pool.
- `analysis-worker`: gate/extraction/style processing, scaled from queue depth and rate limits.
- `event-relay`: optional WebSocket fan-out if real-time sockets remain in FastAPI.
- `frontend`: Nginx serving the immutable Vite build in its own EKS Deployment behind the shared ALB and CloudFront.

Git branches do not determine pods. Kubernetes deploys container image digests. The current monorepo is suitable: keep `frontend/` and `backend/`, add separate Dockerfiles/build jobs, publish separate ECR images, and record a shared Git SHA. Prefer trunk/main plus short-lived branches and promote the same immutable images through dev, staging, and production. Do not maintain permanent environment branches.

## Suggested DynamoDB separation

Use at least two logical tables:

1. `AppControl`: users, memberships, approval requests, role templates, overrides, batches, documents, pages, charts, versions, jobs, budget summaries, and idempotency records.
2. `UsageAudit`: append-oriented LLM usage and application audit events, partitioned by tenant/user/month.

Important access patterns and indexes:

- Documents by user and creation time.
- Documents in a batch.
- Jobs by status and update time.
- Pending signup requests by creation time.
- Users by tenant and role.
- Usage by user/day/month and tenant/day/month.

Large LLM JSON, raw responses, page data, and exported files stay in S3; DynamoDB stores their URI, checksum, byte length, content type, schema version, and retention class.

## Security and reliability baseline

- Private EKS compute across at least two Availability Zones; controlled NAT egress for the external model provider.
- Least-privilege EKS Pod Identity per workload; no static AWS credentials in pods.
- Separate dev/stage/prod AWS accounts and infrastructure as code using Terraform, CDK, or CloudFormation.
- WAF rate controls for signup, upload initialization, authentication-adjacent, and expensive endpoints.
- Server-side authorization for every object, download, edit, export, cost, and admin action.
- Short-lived action-specific presigned URLs; S3 is never publicly readable.
- Conditional DynamoDB state transitions, idempotency keys, attempt records, leases, retries with jitter, and DLQ alarms.
- Structured correlation IDs: tenant, user, batch, document, job, page, model call, and request.
- Never log source contents, passwords, access tokens, provider keys, or hidden reasoning.
- Back up with S3 versioning/lifecycle and DynamoDB PITR; recreate EKS from infrastructure code and test restoration.

## Migration sequence

1. **Identity and tenancy:** Cognito, signup approval, admin-created users, role templates, entitlement middleware.
2. **Durable storage:** introduce S3 object keys and DynamoDB records; migrate filesystem manifests and `/storage` URLs.
3. **Direct bulk uploads:** preflight API, presigned multipart uploads, checksums, malware scanning, format policies.
4. **Durable processing:** replace `asyncio.create_task`, in-memory cancellation, and event backlog with SQS/DLQ workers and durable state.
5. **Format adapters:** Office conversion, image normalization, page/context preparation.
6. **Bounded parallelism:** document/page fan-out with provider, tenant, user, and budget limits.
7. **Usage and analytics:** immutable usage ledger, reservations, rollups, user/admin dashboards.
8. **Production operations:** WAF, alarms, tracing, CI/CD, IaC, restore tests, retention and deletion workflows.
9. **Optional scale services:** Step Functions, Redis/AppSync, Athena/QuickSight, Verified Permissions, and regional DR.

## Additional recommended features

- Duplicate detection using content checksums before paying to process the same file again.
- Explicit retention, deletion, legal-hold, and user data-export policies.
- Human review for low-confidence charts and page-level retry/re-extraction.
- Schema/model/prompt versioning so old outputs remain reproducible.
- Cost estimate and consent before a large batch starts.
- Tenant-level model allowlists and an emergency model/provider kill switch.
- Webhooks/email notifications for completion, partial failure, quota alerts, and access decisions.
- Accessibility and mobile responsiveness work before broad rollout.
- Automated unit, contract, integration, load, security, and disaster-recovery tests.

## Definition of done

- A pod restart or deployment loses no upload, job state, result, event history, or usage record.
- A user can access only tenant-owned resources and enabled features; UI hiding is not the security boundary.
- Bulk jobs are bounded, cancelable, retryable, idempotent, and fair across users.
- Admins can approve/invite/suspend users, apply full access or granular controls, and set quotas/budgets.
- Users and admins can view permitted usage/cost trends without scanning raw event history.
- Every stored artifact has ownership, checksum, provenance, retention, and an auditable creator/time.

## Official references

- [EKS pods and tightly coupled containers](https://docs.aws.amazon.com/eks/latest/userguide/kubernetes-concepts.html)
- [S3 presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html)
- [S3 multipart uploads](https://docs.aws.amazon.com/AmazonS3/latest/userguide/mpuoverview.html)
- [DynamoDB large-item/S3 guidance](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-use-s3-too.html)
- [DynamoDB transactions](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transactions.html)
- [Cognito admin-created users](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-admin-create-user-policy.html)
- [Cognito and Verified Permissions](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-authorization-with-avp.html)
- [EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [Step Functions Distributed Map](https://docs.aws.amazon.com/step-functions/latest/dg/state-map-distributed.html)
- [GuardDuty Malware Protection for S3](https://docs.aws.amazon.com/guardduty/latest/ug/gdu-malware-protection-s3.html)
