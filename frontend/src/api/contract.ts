/**
 * Contract review API client.
 * Uses fetch + response.body.getReader() for SSE (NOT EventSource).
 */
import { useAuthStore } from '@/stores/auth';

const API_BASE = '/api/v1';

/**
 * Fetch wrapper that injects the bearer token and converts a 401
 * (expired/invalid token) into a global logout + redirect to /login.
 * Non-401 errors are returned as-is so each caller can render its own
 * domain-specific message (e.g. 「删除失败」).
 */
async function authedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const resp = await fetch(url, {
    ...init,
    credentials: 'include',
    headers: {
      ...authHeaders(),
      ...(init.headers as Record<string, string> | undefined),
    },
  });
  if (resp.status === 401) {
    useAuthStore().handle401();
    throw new Error('登录已过期，请重新登录');
  }
  return resp;
}

/** Get the JWT from localStorage. */
function getToken(): string {
  return localStorage.getItem('token') || '';
}

/** Common fetch options: include the JWT bearer token. */
function authHeaders(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export interface ContractUploadResponse {
  contract_id: number;
  filename: string;
}

export interface RunResponse {
  run_id: string;
  contract_id: number;
  status: string;
}

export interface Clause {
  id: number;
  review_id: number;
  clause_id: string;
  seq_no: number;
  type: string;
  title: string;
  content: string;
  page: number;
  char_start: number;
  char_end: number;
  span: Record<string, unknown>;
}

export interface Evidence {
  id: number;
  review_id: number;
  clause_id: string;
  source_id: string;
  source_collection: string;
  quote: string;
  relevance: string;
  confidence: number;
  is_human_review: boolean;
  href: string;
}

export interface Revision {
  id: number;
  review_id: number;
  clause_id: string;
  before_text: string;
  after_text: string;
  diff_html: string;
  evidence_ids: string[];
  status: string;
}

export interface ReviewCard {
  id: number;
  review_id: number;
  clause_id: string;
  dimension: string;
  score: number;
  level: string;
  span: string;
  suggestion: string;
  risk_type: string;
}

export interface ReviewReport {
  contract_id: number;
  status: string;
  contract_type: string;
  filename: string;
  disclaimer_accepted: boolean;
  lawyer_confirmed_at: string | null;
  clauses: Clause[];
  review_cards: ReviewCard[];
  evidence: Evidence[];
  revisions: Revision[];
  disclaimer: string;
  created_at: string;
  updated_at: string | null;
}

export interface ReviewListItem {
  id: number;
  filename: string;
  contract_type: string | null;
  status: string;
  created_at: string;
  updated_at: string | null;
  high_risk: number;
  medium_risk: number;
  low_risk: number;
}

export type ProgressCallback = (status: string, data: Record<string, unknown>) => void;

/**
 * Upload a contract file. Returns contract_id.
 */
export async function uploadContract(file: File): Promise<ContractUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  const resp = await authedFetch(`${API_BASE}/contract/upload`, {
    method: 'POST',
    body: formData,
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(err.detail || 'Upload failed');
  }

  return resp.json();
}

/**
 * Trigger async contract review. Returns run_id.
 */
export async function runContractReview(contractId: number): Promise<RunResponse> {
  const resp = await authedFetch(`${API_BASE}/contract/run/${contractId}`, {
    method: 'POST',
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Failed to start review' }));
    throw new Error(err.detail || 'Failed to start review');
  }

  return resp.json();
}

/**
 * Stream SSE progress using fetch + getReader().
 * Calls onProgress(status, data) for each event.
 * Rejects on error events, or when the review completes with status 'failed'.
 */
export async function streamProgress(
  contractId: number,
  token: string,
  onProgress: ProgressCallback,
  signal?: AbortSignal
): Promise<void> {
  const url = `${API_BASE}/contract/run/${contractId}/stream?token=${encodeURIComponent(token)}`;
  const resp = await fetch(url, {
    credentials: 'include',
    signal,
  });

  if (!resp.ok) {
    throw new Error(`SSE connection failed: ${resp.status}`);
  }

  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  // SSE frames are "event: <type>\ndata: <json>\n\n" — remember the type line
  // until the data line of the same frame arrives. (The old parser looked for
  // 'event:' inside data lines, never matched, and every event degraded to the
  // generic 'progress' status — the bar froze at 50% and jumped.)
  let currentEvent = 'progress';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const raw of lines) {
        const line = raw.trimEnd();
        if (line.startsWith('event:')) {
          currentEvent = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          let data: Record<string, unknown>;
          try {
            data = JSON.parse(line.slice(5).trim());
          } catch {
            continue; // skip unparseable frame
          }
          if (currentEvent === 'error') {
            throw new Error(String(data.message || 'Progress stream error'));
          }
          // progress/complete frames carry the pipeline status in data.status
          onProgress(String(data.status || currentEvent), data);
          if (currentEvent === 'complete' && data.status === 'failed') {
            throw new Error(String(data.error || '审查失败'));
          }
          currentEvent = 'progress';
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Fetch the complete review report.
 */
export async function getReport(contractId: number): Promise<ReviewReport> {
  const resp = await authedFetch(`${API_BASE}/contract/report/${contractId}`);

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Failed to fetch report' }));
    throw new Error(err.detail || 'Failed to fetch report');
  }

  return resp.json();
}

/**
 * List recent contract reviews (for sidebar + history page).
 */
export async function listReviews(): Promise<ReviewListItem[]> {
  const resp = await authedFetch(`${API_BASE}/contract/reviews`);

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Failed to list reviews' }));
    throw new Error(err.detail || 'Failed to list reviews');
  }

  const data = await resp.json();
  return data.reviews || [];
}

/**
 * Delete a historical review and all of its data.
 */
export async function deleteReview(contractId: number): Promise<void> {
  const resp = await authedFetch(`${API_BASE}/contract/review/${contractId}`, {
    method: 'DELETE',
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: '删除失败' }));
    throw new Error(err.detail || '删除失败');
  }
}

/**
 * Accept/reject a revision suggestion.
 */
export async function acceptRevision(
  revisionId: number,
  status: 'accepted' | 'rejected' | 'needs_lawyer',
  idempotentKey: string
): Promise<Record<string, unknown>> {
  const resp = await authedFetch(`${API_BASE}/contract/revision/${revisionId}/accept`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, idempotent_key: idempotentKey }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Failed to accept revision' }));
    throw new Error(err.detail || 'Failed to accept revision');
  }

  return resp.json();
}

/**
 * Lawyer confirms a revision.
 */
export async function lawyerConfirm(
  revisionId: number,
  confirmed: boolean
): Promise<Record<string, unknown>> {
  const resp = await authedFetch(`${API_BASE}/contract/revision/${revisionId}/lawyer-confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirmed }),
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: 'Failed to confirm' }));
    throw new Error(err.detail || 'Failed to confirm');
  }

  return resp.json();
}

/**
 * Download the revised contract (.docx) after decisions are made.
 * Returns { ok, message? } — ok=false when there are still pending revisions (409).
 */
export async function downloadFinalContract(
  contractId: number
): Promise<{ ok: boolean; message?: string }> {
  const resp = await authedFetch(
    `${API_BASE}/contract/${contractId}/final-contract/download`
  );

  if (resp.status === 409) {
    const err = await resp.json().catch(() => ({ detail: '还有修订未决策' }));
    return { ok: false, message: err.detail || '还有修订未决策' };
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: '导出失败' }));
    return { ok: false, message: err.detail || '导出失败' };
  }

  const blob = await resp.blob();
  const disposition = resp.headers.get('Content-Disposition') || '';
  let filename = `contract_${contractId}_修订后.docx`;
  const m = disposition.match(/filename\*=UTF-8''(.+)/);
  if (m) {
    try {
      filename = decodeURIComponent(m[1]);
    } catch {
      /* keep fallback name */
    }
  }

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return { ok: true };
}
