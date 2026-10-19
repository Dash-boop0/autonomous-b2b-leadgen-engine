// Typed API client. The base URL comes from NEXT_PUBLIC_API_URL (e.g. https://<repo>-api.vercel.app);
// it defaults to the local API on :8000.
import type { AdvanceResult, Run } from "./types";
import { shouldKeepAdvancing } from "./format";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const TOKEN_KEY = "leadgen.token";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function getToken(): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session-only */
  }
}

export const REQUEST_TIMEOUT_MS = 25_000;

export async function request<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    // Time-limited so a stalled request (e.g. a serverless cold start) never leaves the UI waiting forever.
    res = await fetch(`${API_URL}${path}`, { ...init, headers, body, signal: init.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
  } catch (e) {
    if (e instanceof DOMException && e.name === "TimeoutError")
      throw new ApiError(0, "The demo server is taking too long to respond (it may be waking up). Please try again in a few seconds.");
    throw new ApiError(0, `Cannot reach the API at ${API_URL}. Is the backend running?`);
  }
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/login")) {
    setToken(null);
    if (!window.location.pathname.startsWith("/login")) window.location.href = "/login";
  }
  const text = await res.text();
  const data = text ? safeJson(text) : null;
  if (!res.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : text;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data as T;
}

function safeJson(t: string): unknown {
  try {
    return JSON.parse(t);
  } catch {
    return t;
  }
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, json?: unknown) => request<T>(path, { method: "POST", json: json ?? {} }),
  patch: <T>(path: string, json: unknown) => request<T>(path, { method: "PATCH", json }),
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** The engine is request-driven: keep calling /advance while the run is running (budget or backoff). */
export async function driveRun(runId: string, maxCalls = 25): Promise<Run> {
  let last: { advance: AdvanceResult; run: Run } | null = null;
  for (let i = 0; i < maxCalls; i++) {
    last = await api.post<{ advance: AdvanceResult; run: Run }>(`/api/runs/${runId}/advance`);
    if (!shouldKeepAdvancing(last.advance.outcome, last.run.status)) break;
    await sleep(last.advance.outcome === "backoff" || last.advance.outcome === "busy" ? 1500 : 200);
  }
  return last ? last.run : api.get<Run>(`/api/runs/${runId}`);
}

export async function startAndDrive(path: string, json: unknown): Promise<Run> {
  const res = await api.post<{ advance: AdvanceResult; run: Run }>(path, json);
  if (shouldKeepAdvancing(res.advance.outcome, res.run.status)) return driveRun(res.run.id);
  return res.run;
}
