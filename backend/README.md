# Paper Jam Regulatory API

FastAPI backend for dynamic regulatory PDF ingestion, Atlas Vector Search, and evidence-grounded explanations. All application code and local PDF storage live under `backend/`.

## Run locally

1. Create a virtual environment and install `requirements.txt`.
2. Copy `.env.example` to `.env` and provide MongoDB Atlas, Groq, embedding, JWT, and admin credentials.
3. Start from this directory with `uvicorn app.main:app --reload`.
4. Swagger UI is available at `/docs`; health is `/api/health`.

The configured admin is inserted into the existing `admins` collection only if that email is absent. Its password is Argon2 hashed. Existing records and collections are retained.

## Atlas Vector Search index

At startup the configured sentence-transformers model reports its output dimension. The API logs the complete Atlas Search index JSON using that dimension; paste the emitted definition into Atlas for database `paper_jam`, collection `regulation_chunks`. The index name is configured by `VECTOR_INDEX_NAME`. No embedding dimension is assumed by code.

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

Create this Atlas-managed Vector Search index before using `/api/rag/search` or `/api/rag/answer`. Index provisioning is intentionally not silently attempted by regular MongoDB index creation.

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

PDF bytes are stored separately under `LOCAL_STORAGE_PATH`; only storage keys and metadata enter MongoDB. The storage service is the seam for adding an object-storage implementation.

## Identity and business data

Phase 4 uses the existing `paper_jam` database and adds `users`, `businesses`, and `audit_logs` collections without removing Phase 3 collections. Public registration always creates an `applicant`; officers are provisioned by an administrator. User email addresses are normalized to lowercase and indexed uniquely. Business ownership comes from the authenticated JWT identity, and role or ownership fields are controlled by the API. Passwords use the configured secure password hash and are never returned by profile endpoints.

Run the focused backend tests from this directory with `python -m pytest -q`. Frontend type checking is `npm run typecheck` from `frontend/`.

## Scope

The RAG explains supported regulatory evidence. It does not classify approvals or generate an approval roadmap. Approval processing, document verification, inspections, and SLA workflows remain outside Phase 4.
