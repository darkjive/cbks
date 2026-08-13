export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    super(`API-Fehler ${status}`);
    this.status = status;
    this.body = body;
  }
}

let apiKey: string | null = null;

export function setApiKey(key: string | null): void {
  apiKey = key;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs?: number,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  const controller = new AbortController();
  const timer = timeoutMs ? setTimeout(() => controller.abort(), timeoutMs) : null;
  // Externes init.signal an den Controller koppeln, damit ein vom Aufrufer
  // uebergebenes Abort auch bei gesetztem Timeout wirkt (statt verloren zu gehen).
  if (init.signal) {
    if (init.signal.aborted) controller.abort();
    else init.signal.addEventListener("abort", () => controller.abort(), { once: true });
  }
  const signal = controller.signal;

  try {
    const response = await fetch(path, { ...init, headers, signal });

    if (!response.ok) {
      let body: unknown = null;
      try {
        body = await response.json();
      } catch {
        // kein JSON-Body vorhanden
      }
      throw new ApiError(response.status, body);
    }

    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, { detail: "Zeitüberschreitung – Server antwortet nicht" });
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export async function apiFetchBlob(path: string): Promise<Blob> {
  const headers = new Headers();
  if (apiKey) {
    headers.set("X-API-Key", apiKey);
  }

  const response = await fetch(path, { headers });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // kein JSON-Body vorhanden
    }
    throw new ApiError(response.status, body);
  }

  return await response.blob();
}
