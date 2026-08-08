/**
 * Contract QA API client.
 * Uses fetch + response.body.getReader() for SSE (NOT EventSource, per project rule).
 */
const API_BASE = '/api/v1';

export interface QaCitation {
  ref: string;
  source_id: string;
  article_no: string;
  chapter: string;
  collection: string;
  score: number;
  quote: string;
}

export interface QaMessage {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  citations: QaCitation[] | null;
  status: string;
  error_message: string | null;
  created_at: string;
}

export interface QaStreamHandlers {
  onDelta?: (text: string) => void;
  onCitations?: (items: QaCitation[]) => void;
  onDone?: () => void;
  onError?: (message: string) => void;
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

async function parseError(resp: Response, fallback: string): Promise<string> {
  try {
    const body = await resp.json();
    return body.detail || fallback;
  } catch {
    return fallback;
  }
}

/** Open a Q&A session for a contract. */
export async function createQaSession(
  contractId: number,
  token: string
): Promise<{ session_id: number; contract_id: number; title: string }> {
  const resp = await fetch(`${API_BASE}/contract/qa/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify({ contract_id: contractId }),
    credentials: 'include',
  });
  if (!resp.ok) throw new Error(await parseError(resp, 'Failed to create QA session'));
  return resp.json();
}

export interface QaSessionInfo {
  id: number;
  title: string;
  message_count: number;
  updated_at: string;
}

/**
 * Resolve the most recently active session of a contract (thread resume).
 * session_id is null when the contract has no session yet.
 */
export async function resumeQaSession(
  contractId: number,
  token: string
): Promise<{ session_id: number | null; contract_id: number; title: string | null }> {
  const resp = await fetch(`${API_BASE}/contract/qa/contract/${contractId}/resume`, {
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!resp.ok) throw new Error(await parseError(resp, 'Failed to resume QA session'));
  return resp.json();
}

/** List all sessions of a contract (newest activity first). */
export async function listQaSessions(
  contractId: number,
  token: string
): Promise<{ contract_id: number; sessions: QaSessionInfo[] }> {
  const resp = await fetch(`${API_BASE}/contract/qa/contract/${contractId}/sessions`, {
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!resp.ok) throw new Error(await parseError(resp, 'Failed to list QA sessions'));
  return resp.json();
}

/** Delete a session (messages cascade). Backend refuses while an answer is streaming. */
export async function deleteQaSession(sessionId: number, token: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/contract/qa/session/${sessionId}`, {
    method: 'DELETE',
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!resp.ok) throw new Error(await parseError(resp, 'Failed to delete QA session'));
}

/** List all messages of a session. */
export async function getQaMessages(
  sessionId: number,
  token: string
): Promise<{ session_id: number; messages: QaMessage[] }> {
  const resp = await fetch(`${API_BASE}/contract/qa/session/${sessionId}/messages`, {
    headers: authHeaders(token),
    credentials: 'include',
  });
  if (!resp.ok) throw new Error(await parseError(resp, 'Failed to load messages'));
  return resp.json();
}

/** Submit a question. Returns the assistant placeholder message_id for streaming. */
export async function askQuestion(
  sessionId: number,
  question: string,
  token: string
): Promise<{ session_id: number; message_id: number }> {
  const resp = await fetch(`${API_BASE}/contract/qa/session/${sessionId}/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders(token) },
    body: JSON.stringify({ question }),
    credentials: 'include',
  });
  if (!resp.ok) throw new Error(await parseError(resp, 'Failed to submit question'));
  return resp.json();
}

/**
 * Stream the grounded answer via SSE (fetch + getReader).
 * Frame parser tolerates both \r\n\r\n and \n\n separators (Q-2).
 */
export async function streamAnswer(
  messageId: number,
  token: string,
  handlers: QaStreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const url = `${API_BASE}/contract/qa/message/${messageId}/stream?token=${encodeURIComponent(token)}`;
  const resp = await fetch(url, { credentials: 'include', signal });

  if (!resp.ok) {
    throw new Error(await parseError(resp, `SSE connection failed: ${resp.status}`));
  }

  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const dispatch = (event: string, data: Record<string, unknown>) => {
    if (event === 'delta' && typeof data.text === 'string') {
      handlers.onDelta?.(data.text);
    } else if (event === 'citations' && Array.isArray(data.items)) {
      handlers.onCitations?.(data.items as QaCitation[]);
    } else if (event === 'done') {
      handlers.onDone?.();
    } else if (event === 'error') {
      handlers.onError?.(String(data.message || 'Unknown error'));
    }
  };

  const processFrame = (frame: string) => {
    let event = 'message';
    let dataRaw = '';
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) dataRaw += line.slice(5).trim();
    }
    if (!dataRaw) return;
    try {
      dispatch(event, JSON.parse(dataRaw));
    } catch {
      // skip unparseable frame
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, '\n'); // normalize CRLF (Q-2)

      let sep: number;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        if (frame.trim()) processFrame(frame);
      }
    }
    if (buffer.trim()) processFrame(buffer);
  } finally {
    reader.releaseLock();
  }
}
