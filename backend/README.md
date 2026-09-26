# Paper Jam Regulatory API

FastAPI backend for dynamic regulatory PDF ingestion, Atlas Vector Search, and evidence-grounded explanations. All application code and local PDF storage live under `backend/`.

## Run locally

1. Create a virtual environment and install `requirements.txt`.
2. Copy `.env.example` to `.env` and provide MongoDB Atlas, Groq, embedding, JWT, and admin credentials.
3. Start from this directory with `uvicorn app.main:app --reload`.
4. Swagger UI is available at `/docs`; health is `/api/health`.

The configured admin is inserted into the existing `admins` collection only if that email is absent. Its password is Argon2 hashed. Existing records and collections are retained.

## Atlas Vector Search index

At startup the configured sentence-transformers model reports its output dimension. The API logs the complete Atlas Search index object using that dimension. For the Atlas UI **Edit Vector Search Index** JSON editor, paste only the `definition` object (`{"fields":[...]}`); the index name is selected separately. The collection is `paper_jam.regulation_chunks`, and the index name is configured by `VECTOR_INDEX_NAME`.

The JSON has this shape (the startup log supplies the actual integer in `numDimensions`):

```json
{
  "name": "regulation_chunks_vector",
  "type": "vectorSearch",
  "definition": {
    "fields": [
      {"type":"vector","path":"embedding","numDimensions":<configured model output dimension>,"similarity":"cosine"},
      {"type":"filter","path":"metadata.active"},
      {"type":"filter","path":"metadata.department"},
      {"type":"filter","path":"metadata.business_types"},
      {"type":"filter","path":"metadata.jurisdiction"},
      {"type":"filter","path":"metadata.document_type"},
      {"type":"filter","path":"metadata.issuing_authority"},
      {"type":"filter","path":"regulation_id"},
      {"type":"filter","path":"version_id"}
    ]
  }
}
```

When editing the index in the Atlas UI, use this definition shape (the startup log supplies the actual integer in `numDimensions`):

```json
{
  "fields": [
    {"type":"vector","path":"embedding","numDimensions":384,"similarity":"cosine"},
    {"type":"filter","path":"metadata.active"},
    {"type":"filter","path":"metadata.department"},
    {"type":"filter","path":"metadata.business_types"},
    {"type":"filter","path":"metadata.jurisdiction"},
    {"type":"filter","path":"metadata.document_type"},
    {"type":"filter","path":"metadata.issuing_authority"},
    {"type":"filter","path":"regulation_id"},
    {"type":"filter","path":"version_id"}
  ]
}
```

Create this Atlas-managed Vector Search index before using `/api/rag/search`, `/api/rag/answer`, or approval evaluation. Index provisioning is intentionally not silently attempted by regular MongoDB index creation. Verify the saved definition includes the filter fields; a vector-only index cannot serve the engine's active-regulation filter.

## Approval extraction

Approval retrieval performs complementary searches for authorization provisions and eligibility thresholds. The engine first checks the retrieved active chunks for explicit approval clauses. It calls Groq only if no usable clause is found; that request uses a bounded, source-diverse set of passages. `GROQ_MODEL` defaults to `openai/gpt-oss-20b` with strict structured output. If that model reports a constrained-generation failure, the engine uses `GROQ_FALLBACK_MODEL` (default `openai/gpt-oss-120b`) in JSON mode. Model results are validated against the Pydantic model and exact source quotations before publication. SDK retries are disabled so manual refresh bursts do not multiply rate-limited requests. Groq 429 responses still require waiting for the provider quota window to recover.

Groq is not the source of truth for approval rules. The deterministic, source-grounded extractor accepts only short clauses that pair a legal requirement with an explicit authorization verb or prohibition, and stores the exact clause and source chunk. It also recognizes the FSSAI Food Safety and Standards Act, 2006 section 31 clause and keeps the licence-versus-registration category conditional until retrieved eligibility evidence resolves it. This is dynamic over the active regulatory corpus and business profile, but intentionally conservative: document headings, checklists, and general duties are not approvals. If RAG returns no chunks, or neither the source patterns nor the LLM establish an approval, the roadmap stays empty instead of inventing one. The local corpus includes the Act at `data/regulations/605b2d944b084462ba8d0f8c6112b096.pdf` and eligibility guidance at `data/regulations/4035ad91740d4b42b83c9bdbb4f7cb08.pdf`.

For food businesses, the profile accepts optional annual turnover and installed production capacity (kg/day). These facts are used only when active retrieved FSSAI eligibility sources make the category depend on them; absent values leave the authorization marked as needing information.

## API outline

- `POST /api/auth/register`: creates a public applicant account. Role assignment is not accepted from registration input.
- `POST /api/auth/login`: JSON `{ "email": "...", "password": "..." }` authenticates applicants, officers, and the existing Phase 3 admin account.
- `GET /api/auth/me` and `GET/PATCH /api/users/me`: return or update the authenticated account's safe profile fields.
- `POST/GET /api/businesses`: create a draft business profile or list only businesses visible to the authenticated account.
- `GET/PATCH /api/businesses/{business_id}`: read or update a business after checking ownership or explicit authorization.
- `GET/POST /api/admin/users`, `GET/PATCH /api/admin/users/{user_id}`: admin-only account management. Admin-created accounts use the officer role.
- `GET /api/admin/audit`: admin-only account and business audit events.
- `GET/POST /api/admin/regulations`: list and create regulation metadata (JWT required).
- `GET /api/admin/regulations/{id}`: details and active version.
- `POST /api/admin/regulations/{id}/versions`: multipart `file`, `version`, optional `effective_date` and `published_date`; validates and ingests the actual PDF.
- `GET /api/admin/regulations/{id}/versions`, `POST .../{version_id}/activate`, `POST .../{version_id}/reprocess`, `GET .../{version_id}/status`.
- `GET /api/admin/regulations/metrics`: database counts for regulatory metrics.
- `POST /api/rag/search`: vector retrieval only; optional metadata filters.
- `POST /api/rag/answer`: JWT-protected grounded Groq explanation and retrieved citations.
- `GET /api/business/passport`: create-once/read the authenticated business's Approval Passport, current business profile reference, current Phase 5 approval links, verification aggregate, and reusable document versions.
- `PATCH /api/business/passport`: update the canonical business profile through the existing profile schema; passport information is not copied into a second store.
- `GET/POST /api/business/documents`: list or upload a business document to its reusable passport vault. Uploads accept readable PDFs up to 15 MB, extract conservative text fields, check selected type evidence, start with pending verification, and create a new immutable version for replacements.
- `GET /api/business/documents/{document_id}/content`: stream a document only after checking authenticated business ownership; MongoDB storage keys are never returned.
- `GET /api/business/applications`, `POST /api/business/applications`: list actual tenant applications or create one from an approval in the current Phase 5 generation. The application references the passport and approval record.
- `GET /api/business/applications/{application_id}/readiness`, `POST .../validate`: recalculate required information and documents from the current approval result and usable passport documents. Drafts follow updated Phase 5 rules; submitted applications retain their readiness and document-version snapshot.
- `POST /api/business/applications/{application_id}/documents/{document_id}`: link an owned active vault document to a draft application.
- `POST /api/business/applications/{application_id}/submit`: server-side submission gate; returns 409 until readiness is `ready` at 100% with no validation errors or mismatches.
- `GET /api/business/applications/{application_id}/timeline`: return only events actually recorded for that application; no future review stages or SLA values are fabricated.
- `PATCH /api/admin/passports/{business_id}/documents/{document_id}/verification` and `/validation`: admin review operations for document state. Upload alone never changes verification to verified.

PDF bytes are stored separately under `LOCAL_STORAGE_PATH`; only storage keys and metadata enter MongoDB. The storage service is the seam for adding an object-storage implementation.

## Identity and business data

Phase 4 uses the existing `paper_jam` database and adds `users`, `businesses`, and `audit_logs` collections without removing Phase 3 collections. Public registration always creates an `applicant`; officers are provisioned by an administrator. User email addresses are normalized to lowercase and indexed uniquely. Business ownership comes from the authenticated JWT identity, and role or ownership fields are controlled by the API. Passwords use the configured secure password hash and are never returned by profile endpoints.

Phase 6 adds one `approval_passports` record per business; its unique business index makes creation idempotent. Business facts stay in `businesses`, while existing `business_documents` records form the vault with version, extraction, validation, verification, expiry, and approval-link metadata. The `business_document_counters` collection allocates per-type versions atomically. Previous files are retained so submitted applications can keep references to the exact versions used.

Phase 7 reads the current Phase 5 approval result for each draft, checks profile facts and active, non-expired, validated passport documents, and calculates readiness on the server. PDF field extraction is pattern-based and intentionally leaves uncertain fields empty. Mismatch detection raises a review blocker and does not reject a document or claim authenticity. Type evidence that cannot be confirmed from the filename/text remains `needs_review` until an admin review. Only a passing basic file/text/type validation can satisfy a required document. `sla_days` stays unavailable unless a source-backed value is supplied.

Run the focused backend tests from this directory with `python -m pytest -q`. Frontend type checking is `npm run typecheck` from `frontend/`.

## Scope

Regulatory approval applicability is generated by Phase 5 from the active RAG corpus and source-grounded extraction. Phase 6 provides a reusable structured passport and versioned document vault. Phase 7 computes readiness and protects submission. These checks establish basic application completeness only; they do not certify legal authenticity, issue an authority approval, or invent department-review events or SLA dates.
