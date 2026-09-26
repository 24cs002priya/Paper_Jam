const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
const TOKEN_KEY = 'paperjam_access_token';
const LEGACY_TOKEN_KEY = 'paperjam_admin_token';

export type User = { id: string; full_name: string; email: string; phone?: string | null; role: 'applicant' | 'officer' | 'admin' | 'business_owner' | 'business_admin' | 'business_user'; status: string; business_ids: string[]; business_id?: string | null; department_id?: string | null; last_login_at?: string | null };
export type AuthResult = { access_token: string; token_type: string; expires_in: number; user: User };
export type Business = { id: string; business_name: string; legal_name?: string | null; business_type: string; industry: string; description: string; entity_type: string; registration_number?: string | null; contact: { email?: string | null; phone?: string | null }; address: { address_line_1: string; address_line_2?: string | null; city: string; district: string; state: string; pincode: string; country: string }; status: string; created_at: string; updated_at: string };

export type RegulationVersion = {
  id: string;
  regulation_id: string;
  version: string;
  file_name: string;
  status: 'processing' | 'ready' | 'failed' | 'archived';
  processing_error?: string | null;
  uploaded_at: string;
  effective_date?: string | null;
  published_date?: string | null;
  page_count: number;
  chunk_count: number;
};

export type Regulation = {
  id: string;
  name: string;
  slug: string;
  description?: string;
  department_name?: string | null;
  department_id?: string | null;
  business_types: string[];
  jurisdiction?: string | null;
  issuing_authority?: string | null;
  source_url?: string | null;
  document_type: string;
  status: string;
  active_version_id?: string | null;
  active_version?: RegulationVersion | null;
  created_at: string;
  updated_at: string;
};

export type BusinessProfile = { id: string; business_id?: string; legal_name: string; display_name: string; business_type: string; industry: string; entity_type: string; registration_number?: string | null; pan?: string | null; gstin?: string | null; email: string; phone: string; registered_address: Record<string,string>; operating_address: Record<string,string>; contact_person: Record<string,string>; business_activities: string[]; employee_count?:number|null; annual_turnover?: number | null; production_capacity_kg_per_day?: number | null; profile_completion?: number; completion?: { percentage: number; completed_fields: number; required_fields: number; missing_fields: string[] } };
export type BusinessApplication = { id: string; approval_type: string; approval_name: string; status: string; current_stage: string; department_name?: string | null; authority?: string | null; sla_days?: number | null; submitted_at?: string | null; created_at: string; updated_at: string; readiness_status?: string; readiness_percentage?: number; readiness?: ApplicationReadiness };
export type PassportDocument = { id:string; document_type:string; file_name:string; file_url:string; version:number; lifecycle_status:string; verification_status:'pending'|'verified'|'rejected'|'needs_update'; extraction_status:string; validation_status:string; validation_errors:string[]; extracted_data:Record<string,unknown>; uploaded_at:string; expiry_date?:string|null; used_for_approvals:string[] };
export type ApprovalPassport = { id:string; business_id:string; status:string; business_information:Record<string,unknown>; verification:{status:string;verified_documents:number;total_documents:number}; documents:PassportDocument[]; approval_links:Array<{approval_id:string;approval_name:string;status:string;application_ids:string[]}> };
export type ApplicationReadiness = { application_id:string; passport_id?:string; approval_name?:string; readiness_status:'not_ready'|'in_progress'|'ready'|'blocked'; readiness_percentage:number; required_information:Array<{field?:string|null;label:string;status:'complete'|'missing';value?:unknown;source?:string}>; required_documents:Array<{document_type:string;requirement_type:string;status:string;uploaded?:boolean;document_id?:string|null;version?:number|null;verification_status?:string|null;validation_status?:string|null;validation_errors?:string[];evidence?:string}>; checks?:Array<{key:string;label:string;status:'complete'|'incomplete'|'mismatch'|'not_checked';detail:string}>; missing_documents:string[]; mismatches:Array<{field:string;expected:string;actual:string;status:string;document_id:string}>; validation:{passed:boolean;mismatches:unknown[];errors:string[]}; can_submit:boolean };

export function getAdminToken() {
  return localStorage.getItem(TOKEN_KEY) || localStorage.getItem(LEGACY_TOKEN_KEY);
}

export function setAdminToken(token: string | null) {
  if (token) { localStorage.setItem(TOKEN_KEY, token); localStorage.removeItem(LEGACY_TOKEN_KEY); }
  else { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(LEGACY_TOKEN_KEY); }
}

export async function openPassportDocument(documentId: string) {
  const token = getAdminToken();
  const response = await fetch(`${API_BASE}/business/documents/${documentId}/content`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) throw new ApiError("Could not open this passport document.", response.status);
  const objectUrl = URL.createObjectURL(await response.blob());
  const opened = window.open(objectUrl, "_blank", "noopener,noreferrer");
  if (!opened) URL.revokeObjectURL(objectUrl);
  else window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getAdminToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError('Cannot reach the Paper Jam API. Make sure the backend is running.', 0);
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    if (response.status === 401 && getAdminToken()) {
      setAdminToken(null);
      window.dispatchEvent(new Event('paperjam:unauthorized'));
    }
    const detail = typeof body?.detail === 'string' ? body.detail : `Request failed (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export async function loginAdmin(email: string, password: string) {
  return apiRequest<AuthResult>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

export function registerApplicant(payload: { full_name: string; email: string; phone: string; password: string }) {
  return apiRequest<User>('/auth/register', { method: 'POST', body: JSON.stringify(payload) });
}
export function signupBusiness(payload: { full_name: string; business_name: string; email: string; phone: string; password: string }) { return apiRequest<AuthResult & { business: BusinessProfile }>('/auth/business/signup', { method: 'POST', body: JSON.stringify(payload) }); }
export function loginBusiness(email: string, password: string) { return apiRequest<AuthResult & { business: BusinessProfile }>('/auth/business/login', { method: 'POST', body: JSON.stringify({ email, password }) }); }
export function getBusinessProfile() { return apiRequest<BusinessProfile>('/business/profile'); }
export function updateBusinessProfile(payload: Partial<BusinessProfile>) { return apiRequest<BusinessProfile>('/business/profile', { method: 'PUT', body: JSON.stringify(payload) }); }
export function getBusinessHome() { return apiRequest<{ business: BusinessProfile; profile_completion: NonNullable<BusinessProfile['completion']>; applications: { total: number; active: number; items: BusinessApplication[] }; compliance: { total: number }; roadmap: { status: string; message: string; approval_count: number; retrieved_chunk_count: number } }>('/business/home'); }
export function getBusinessRoadmap() { return apiRequest<{ status: string; message: string }>('/business/roadmap'); }
export function listBusinessApplications() { return apiRequest<{ items: BusinessApplication[]; total: number }>('/business/applications'); }
export function getBusinessApplication(id: string) { return apiRequest<BusinessApplication>(`/business/applications/${id}`); }
export function getApplicationTimeline(id: string) { return apiRequest<{ application_id: string; current_stage: string; timeline: Array<{ stage: string; status: string; completed_at?: string | null; authority?:string|null; message?:string|null }> }>(`/business/applications/${id}/timeline`); }
export function getApplicationReadiness(id:string) { return apiRequest<ApplicationReadiness>(`/business/applications/${id}/readiness`); }
export function validateBusinessApplication(id:string) { return apiRequest<ApplicationReadiness>(`/business/applications/${id}/validate`,{method:'POST'}); }
export function createBusinessApplication(payload: { approval_id: string }) { return apiRequest<BusinessApplication>('/business/applications', { method: 'POST', body: JSON.stringify(payload) }); }
export function submitBusinessApplication(id: string) { return apiRequest<BusinessApplication>(`/business/applications/${id}/submit`, { method: 'POST' }); }
export function listBusinessActions() { return apiRequest<{ items: Array<Record<string, unknown>>; total: number }>('/business/actions'); }
export function listBusinessCompliance() { return apiRequest<{ items: Array<Record<string, unknown>>; total: number }>('/business/compliance'); }
export function listBusinessRegulationChanges() { return apiRequest<{ items: Array<Record<string, unknown>>; total: number }>('/business/regulation-changes'); }
export function listBusinessDocuments() { return apiRequest<{ items: Array<Record<string, unknown>>; total: number }>('/business/documents'); }
export function getApprovalPassport() { return apiRequest<ApprovalPassport>('/business/passport'); }
export function updateApprovalPassport(payload: Partial<BusinessProfile>) { return apiRequest<BusinessProfile>('/business/passport', { method: 'PATCH', body: JSON.stringify(payload) }); }
export function uploadBusinessDocument(file: File, documentType: string, options: {applicationId?:string;actionId?:string} = {}) { const body = new FormData(); body.set('file', file); body.set('document_type', documentType); if(options.applicationId)body.set('application_id',options.applicationId);if(options.actionId)body.set('action_id',options.actionId);return apiRequest<PassportDocument>('/business/documents', { method: 'POST', body }); }
export function resolveBusinessAction(id: string) { return apiRequest<Record<string, unknown>>(`/business/actions/${id}/resolve`, { method: 'POST' }); }
export type ApprovalResult = { approval_id: string; approval_name: string; authority?: string | null; category?: string | null; status: 'mandatory' | 'conditional' | 'not_applicable' | 'needs_information'; confidence: number; reason: string; conditions: Array<{condition:string;result:boolean|null;evidence?:string|null}>; required_documents: Array<{document_name:string;requirement_type:string;evidence:string}>; regulation_sources: Array<{document_title?:string|null;regulation_name?:string|null;authority?:string|null;section?:string|null;source_url?:string|null;page_number?:number|null;chunk_id:string}>; retrieved_chunks:Array<{chunk_id:string;text:string;score?:number;source:Record<string,unknown>}>; depends_on: string[] };
export type ApprovalEngineResponse = { business_id:string; generation_id?:string; engine_status:string; approvals:ApprovalResult[]; roadmap: { status:string; ordering_basis?:string; groups?:Record<string,string[]>; dependency_edges?:Array<{approval_id:string;must_precede:string}>; approval_names?:Record<string,string> }; missing_information:string[]; retrieved_chunk_count:number; generated_at?:string };
export function getApprovalEngine(businessId: string) { return apiRequest<ApprovalEngineResponse>(`/approval-engine/${businessId}`); }
export function refreshApprovalEngine(businessId: string) { return apiRequest<ApprovalEngineResponse>(`/approval-engine/${businessId}/refresh`, { method: 'POST' }); }
export function getCurrentUser() { return apiRequest<User>('/auth/me'); }
export function getMyProfile() { return apiRequest<User>('/users/me'); }
export function updateMyProfile(payload: { full_name?: string; phone?: string }) { return apiRequest<User>('/users/me', { method: 'PATCH', body: JSON.stringify(payload) }); }
export function listBusinesses() { return apiRequest<{ items: Business[]; total: number }>('/businesses'); }
export function createBusiness(payload: Omit<Business, 'id' | 'status' | 'created_at' | 'updated_at'>) { return apiRequest<Business>('/businesses', { method: 'POST', body: JSON.stringify(payload) }); }
export function updateBusiness(id: string, payload: Partial<Omit<Business, 'id' | 'status' | 'created_at' | 'updated_at'>>) { return apiRequest<Business>(`/businesses/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }); }
export function listAdminUsers() { return apiRequest<{ items: User[]; total: number }>('/admin/users'); }
export function updateAdminUser(id: string, payload: { role?: User['role']; status?: 'active' | 'inactive'; department_id?: string | null }) { return apiRequest<User>(`/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }); }
