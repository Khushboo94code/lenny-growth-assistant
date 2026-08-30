import type { Health, Message, ModelOption, SSEHandler, Session } from "../types";

// VITE_API_URL unset (e.g. `vite dev`) → talk to the backend directly on :8000.
// VITE_API_URL="" (the built image behind nginx / Cloudflare) → same-origin relative URLs,
// so the Cloudflare Access cookie applies to every request.
const _envApi = import.meta.env.VITE_API_URL as string | undefined;
const API = _envApi === undefined ? "http://localhost:8000" : _envApi;

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const getHealth = () => json<Health>("/health");
export const listSessions = () => json<Session[]>("/sessions");
export const createSession = (title = "New chat") =>
  json<Session>("/sessions", { method: "POST", body: JSON.stringify({ title }) });
export const getMessages = (id: string) => json<Message[]>(`/sessions/${id}/messages`);
export const getPolicy = () => json<{ policy: any }>("/artifacts/policy");
export const getModels = () =>
  json<{ options: ModelOption[]; current: string }>("/config/models");
export const setModel = (provider: string, model: string) =>
  json<{ current: string }>("/config/model", {
    method: "POST",
    body: JSON.stringify({ provider, model }),
  });

function parseSSE(raw: string): { event: string; data: any } {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  let data: any = null;
  const joined = dataLines.join("\n");
  if (joined) {
    try {
      data = JSON.parse(joined);
    } catch {
      data = joined;
    }
  }
  return { event, data };
}

/** POST /chat and dispatch SSE events (start, token, meta, error, done). */
export async function streamChat(
  sessionId: string,
  message: string,
  onEvent: SSEHandler,
): Promise<void> {
  const resp = await fetch(`${API}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, message }),
  });

  if (!resp.ok || !resp.body) {
    onEvent("error", { type: "http_error", message: `${resp.status} ${resp.statusText}` });
    onEvent("done", {});
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const chunk = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      if (chunk.trim()) {
        const { event, data } = parseSSE(chunk);
        onEvent(event, data);
      }
    }
  }
}
