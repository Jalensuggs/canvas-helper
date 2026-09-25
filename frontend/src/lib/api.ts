export type JsonObject = Record<string, unknown>;

export class ApiError extends Error {
  status: number;
  detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

function cookie(name: string): string | undefined {
  const prefix = `${encodeURIComponent(name)}=`;
  return document.cookie
    .split("; ")
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (MUTATING.has(method)) {
    headers.set("X-Canvas-Helper", "1");
    const csrf = cookie("canvas_helper_csrf");
    if (csrf) headers.set("X-CSRF-Token", decodeURIComponent(csrf));
  }

  let response: Response;
  try {
    response = await fetch(path.startsWith("/api") ? path : `/api${path}`, {
      ...init,
      method,
      headers,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError("无法连接本地服务，请确认后端已启动。", 0);
  }

  const contentType = response.headers.get("content-type") ?? "";
  const body = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : await response.text().catch(() => "");

  if (!response.ok) {
    const record = body && typeof body === "object" ? (body as JsonObject) : {};
    const message = String(record.detail ?? record.message ?? body ?? `请求失败 (${response.status})`);
    throw new ApiError(message, response.status, body);
  }
  return body as T;
}

function queryString(params: Record<string, string | number | boolean | undefined>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  const output = query.toString();
  return output ? `?${output}` : "";
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) }),
  put: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: "PUT", body: data === undefined ? undefined : JSON.stringify(data) }),
  patch: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: "PATCH", body: data === undefined ? undefined : JSON.stringify(data) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),

  authMe: () => request<JsonObject>("/api/auth/me"),
  requestMagicLink: (email: string) =>
    request<JsonObject>("/api/auth/request-link", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  verifyMagicLink: (token: string) =>
    request<JsonObject>("/api/auth/verify", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  logout: () => request<void>("/api/auth/logout", { method: "POST", body: "{}" }),
  me: () => request<JsonObject>("/api/me"),
  setupToken: (canvasUrl: string, token: string) =>
    request<JsonObject>("/api/setup/token", {
      method: "POST",
      body: JSON.stringify({ canvas_url: canvasUrl, token }),
    }),
  dashboard: () => request<JsonObject>("/api/dashboard"),
  deadlines: (includeCompleted = false) =>
    request<unknown>(`/api/deadlines${queryString({ include_completed: includeCompleted || undefined })}`),
  courses: () => request<unknown>("/api/courses"),
  course: (id: string) => request<JsonObject>(`/api/courses/${encodeURIComponent(id)}`),
  assignment: (id: string) => request<JsonObject>(`/api/assignments/${encodeURIComponent(id)}`),
  materials: (courseId?: string, kind?: string) =>
    request<unknown>(`/api/materials${queryString({
      course_id: courseId,
      kind,
    })}`),
  material: (id: string) =>
    request<JsonObject>(`/api/materials/${encodeURIComponent(id)}`),
  materialContent: (id: string) =>
    request<string>(`/api/materials/${encodeURIComponent(id)}/content`),
  announcements: (courseId?: string) =>
    request<unknown>(`/api/announcements${queryString({ course_id: courseId })}`),
  search: (
    query: string,
    courseId?: string,
    kind?: string,
    dateFrom?: string,
    dateTo?: string,
  ) =>
    request<unknown>(`/api/search${queryString({
      q: query,
      course_id: courseId,
      kind,
      date_from: dateFrom,
      date_to: dateTo,
    })}`),
  todos: () => request<unknown>("/api/todos"),
  createTodo: (data: JsonObject) => request<JsonObject>("/api/todos", { method: "POST", body: JSON.stringify(data) }),
  updateTodo: (id: string, data: JsonObject) =>
    request<JsonObject>(`/api/todos/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  deleteTodo: (id: string) =>
    request<void>(`/api/todos/${encodeURIComponent(id)}`, { method: "DELETE" }),
  notes: (params: Record<string, string | number | boolean | undefined> = {}) =>
    request<unknown>(`/api/notes${queryString(params)}`),
  note: (id: string) => request<JsonObject>(`/api/notes/${encodeURIComponent(id)}`),
  createNote: (data: JsonObject) =>
    request<JsonObject>("/api/notes", { method: "POST", body: JSON.stringify(data) }),
  updateNote: (id: string, data: JsonObject) =>
    request<JsonObject>(`/api/notes/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteNote: (id: string, version: number) =>
    request<JsonObject>(`/api/notes/${encodeURIComponent(id)}`, { method: "DELETE", body: JSON.stringify({ version }) }),
  restoreNote: (id: string, version: number) =>
    request<JsonObject>(`/api/notes/${encodeURIComponent(id)}/restore`, { method: "POST", body: JSON.stringify({ version }) }),
  noteRevisions: (id: string) =>
    request<unknown>(`/api/notes/${encodeURIComponent(id)}/revisions`),
  restoreNoteRevision: (id: string, revision: number, version: number) =>
    request<JsonObject>(`/api/notes/${encodeURIComponent(id)}/revisions/${revision}/restore`, {
      method: "POST", body: JSON.stringify({ version }),
    }),
  previewAction: (action: string, data: JsonObject) =>
    request<JsonObject>(`/api/actions/${encodeURIComponent(action)}/preview`, {
      method: "POST", body: JSON.stringify(data),
    }),
  executeAction: (action: string, confirmToken: string) =>
    request<JsonObject>(`/api/actions/${encodeURIComponent(action)}/execute`, {
      method: "POST", body: JSON.stringify({ confirm_token: confirmToken }),
    }),
  syncStatus: () => request<JsonObject>("/api/sync/status"),
  sync: (job = "all") =>
    request<JsonObject>(`/api/sync/${encodeURIComponent(job)}`, { method: "POST", body: "{}" }),
  syncJob: (id: string) => request<JsonObject>(`/api/sync/jobs/${encodeURIComponent(id)}`),
  cancelSync: (id: string) =>
    request<JsonObject>(`/api/sync/jobs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  doctor: () => request<JsonObject>("/api/doctor"),
  aiChat: (message: string, context: JsonObject) =>
    request<JsonObject>("/api/ai/chat", {
      method: "POST",
      body: JSON.stringify({ message, context }),
    }),
};

export function syncEventSource(): EventSource {
  return new EventSource("/api/sync/events", { withCredentials: true });
}

export type ChatEvent =
  | { type: "text_delta"; delta: string }
  | { type: "citation"; citation: JsonObject }
  | { type: "usage"; usage: JsonObject; model?: string }
  | { type: "error"; error: string }
  | { type: "done" };

export async function streamChat(
  message: string,
  context: JsonObject,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const headers = new Headers({
    Accept: "text/event-stream",
    "Content-Type": "application/json",
    "X-Canvas-Helper": "1",
  });
  const csrf = cookie("canvas_helper_csrf");
  if (csrf) headers.set("X-CSRF-Token", decodeURIComponent(csrf));
  const response = await fetch("/api/ai/chat", {
    method: "POST",
    credentials: "same-origin",
    headers,
    body: JSON.stringify({ message, context, stream: true }),
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({})) as JsonObject;
    throw new ApiError(text(body.detail, "AI 请求失败"), response.status, body);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const data = block.split("\n").find((line) => line.startsWith("data: "));
      if (data) onEvent(JSON.parse(data.slice(6)) as ChatEvent);
    }
    if (done) break;
  }
}

export function listFrom<T = JsonObject>(value: unknown, keys: string[] = []): T[] {
  if (Array.isArray(value)) return value as T[];
  if (!value || typeof value !== "object") return [];
  const record = value as JsonObject;
  for (const key of [...keys, "items", "results", "data"]) {
    if (Array.isArray(record[key])) return record[key] as T[];
  }
  return [];
}

export function text(value: unknown, fallback = ""): string {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

export function idOf(value: JsonObject): string {
  return text(value.id ?? value.canvas_id ?? value.assignment_id ?? value.course_id);
}
