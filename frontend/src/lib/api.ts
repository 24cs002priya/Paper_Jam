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

export type BusinessProfile = { id: string; business_id?: string; legal_name: string; display_name: string; business_type: string; industry: string; entity_type: string; registration_number?: string | null; pan?: string | null; gstin?: string | null; email: string; phone: string; registered_address: Record<string,string>; operating_address: Record<string,string>; contact_person: Record<string,string>; business_activities: string[]; profile_completion?: number; completion?: { percentage: number; completed_fields: number; required_fields: number; missing_fields: string[] } };
export type BusinessApplication = { id: string; approval_type: string; approval_name: string; status: string; current_stage: string; department_name?: string | null; sla_days?: number | null; submitted_at?: string | null; created_at: string; updated_at: string };

export function getAdminToken() {
  return localStorage.getItem(TOKEN_KEY) || localStorage.getItem(LEGACY_TOKEN_KEY);
}

export function setAdminToken(token: string | null) {
  if (token) { localStorage.setItem(TOKEN_KEY, token); localStorage.removeItem(LEGACY_TOKEN_KEY); }
  else { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(LEGACY_TOKEN_KEY); }
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
export function getBusinessHome() { return apiRequest<{ business: BusinessProfile; profile_completion: NonNullable<BusinessProfile['completion']>; applications: { total: number; active: number; items: BusinessApplication[] }; compliance: { total: number }; roadmap: { status: string; message: string } }>('/business/home'); }
export function getBusinessRoadmap() { return apiRequest<{ status: string; message: string }>('/business/roadmap'); }
export function listBusinessApplications() { return apiRequest<{ items: BusinessApplication[]; total: number }>('/business/applications'); }
export function getBusinessApplication(id: string) { return apiRequest<BusinessApplication>(`/business/applications/${id}`); }
export function getApplicationTimeline(id: string) { return apiRequest<{ application_id: string; current_stage: string; timeline: Array<{ stage: string; status: string; completed_at?: string | null }> }>(`/business/applications/${id}/timeline`); }
export function createBusinessApplication(payload: { approval_type: string; approval_name: string }) { return apiRequest<BusinessApplication>('/business/applications', { method: 'POST', body: JSON.stringify(payload) }); }
export function submitBusinessApplication(id: string) { return apiRequest<BusinessApplication>(`/business/applications/${id}/submit`, { method: 'POST' }); }
export function listBusinessActions() { return apiRequest<{ items: Array<Record<string, unknown>>; total: number }>('/business/actions'); }
export function listBusinessCompliance() { return apiRequest<{ items: Array<Record<string, unknown>>; total: number }>('/business/compliance'); }
export function listBusinessRegulationChanges() { return apiRequest<{ items: Array<Record<string, unknown>>; total: number }>('/business/regulation-changes'); }
export function listBusinessDocuments() { return apiRequest<{ items: Array<Record<string, unknown>>; total: number }>('/business/documents'); }
export function uploadBusinessDocument(file: File, documentType: string) { const body = new FormData(); body.set('file', file); body.set('document_type', documentType); return apiRequest<Record<string, unknown>>('/business/documents', { method: 'POST', body }); }
export function resolveBusinessAction(id: string) { return apiRequest<Record<string, unknown>>(`/business/actions/${id}/resolve`, { method: 'POST' }); }
export function getCurrentUser() { return apiRequest<User>('/auth/me'); }
export function getMyProfile() { return apiRequest<User>('/users/me'); }
export function updateMyProfile(payload: { full_name?: string; phone?: string }) { return apiRequest<User>('/users/me', { method: 'PATCH', body: JSON.stringify(payload) }); }
export function listBusinesses() { return apiRequest<{ items: Business[]; total: number }>('/businesses'); }
export function createBusiness(payload: Omit<Business, 'id' | 'status' | 'created_at' | 'updated_at'>) { return apiRequest<Business>('/businesses', { method: 'POST', body: JSON.stringify(payload) }); }
export function updateBusiness(id: string, payload: Partial<Omit<Business, 'id' | 'status' | 'created_at' | 'updated_at'>>) { return apiRequest<Business>(`/businesses/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }); }
export function listAdminUsers() { return apiRequest<{ items: User[]; total: number }>('/admin/users'); }
export function updateAdminUser(id: string, payload: { role?: User['role']; status?: 'active' | 'inactive'; department_id?: string | null }) { return apiRequest<User>(`/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }); }
