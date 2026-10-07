import { ReactNode, createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

export interface SessionActor {
  kind: string;
  principal: string;
  authentication: string;
  display_name: string | null;
}

export interface BrowserSession {
  actor: SessionActor;
  csrf: string;
}

export class SessionError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(`Session request refused ${status}: ${detail}`);
    this.name = "SessionError";
    this.status = status;
    this.detail = detail;
  }
}

let session: BrowserSession | null = null;
let sessionRequest: Promise<BrowserSession> | null = null;

export function csrfToken(): string | null {
  return session?.csrf ?? null;
}

export function resetSession(): void {
  session = null;
  sessionRequest = null;
}

export function fetchSession(): Promise<BrowserSession> {
  if (!sessionRequest) {
    sessionRequest = fetch("/v1/session", { headers: { accept: "application/json" } })
      .then(async (response) => {
        if (!response.ok) {
          const text = await response.text();
          let detail = text || response.statusText;
          try {
            const body: unknown = JSON.parse(text);
            if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
              detail = body.detail;
            }
          } catch {
            // Keep the response text as the daemon's refusal detail.
          }
          throw new SessionError(response.status, detail);
        }
        const result = (await response.json()) as BrowserSession;
        session = result;
        return result;
      })
      .catch((error: unknown) => {
        sessionRequest = null;
        throw error;
      });
  }
  return sessionRequest;
}

export interface ApiNotice {
  status: number;
  detail: string;
}

interface SessionContextValue {
  actor: SessionActor | null;
  loading: boolean;
  error: SessionError | null;
  notice: ApiNotice | null;
  retry: () => void;
  clearNotice: () => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [actor, setActor] = useState<SessionActor | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<SessionError | null>(null);
  const [notice, setNotice] = useState<ApiNotice | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    void fetchSession().then(
      (result) => {
        setActor(result.actor);
        setError(null);
        setLoading(false);
      },
      (cause: unknown) => {
        setActor(null);
        setError(cause instanceof SessionError ? cause : new SessionError(0, cause instanceof Error ? cause.message : "unknown session error"));
        setLoading(false);
      },
    );
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const onRefusal = (event: Event) => {
      const refusal = (event as CustomEvent<ApiNotice>).detail;
      setNotice(refusal);
      if (refusal.status === 401) {
        resetSession();
        setActor(null);
        setError(new SessionError(401, refusal.detail));
      }
    };
    window.addEventListener("api-refusal", onRefusal);
    return () => window.removeEventListener("api-refusal", onRefusal);
  }, []);

  const clearNotice = useCallback(() => setNotice(null), []);
  const value = useMemo(() => ({ actor, loading, error, notice, retry: load, clearNotice }), [actor, loading, error, notice, load, clearNotice]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function SessionNotice() {
  const { loading, error, notice, retry, clearNotice } = useSession();
  if (loading) return <p className="session-notice" role="status">Checking operator session…</p>;
  if (error?.status === 401 || notice?.status === 401) {
    return (
      <section className="session-notice unauthenticated" role="alert">
        <strong>Session is not authenticated.</strong>
        <span>Open this app through the daemon’s bootstrap link, then retry.</span>
        <button type="button" onClick={retry}>Retry session</button>
      </section>
    );
  }
  if (error) {
    return <section className="session-notice unauthenticated" role="alert"><strong>Could not check the session.</strong><span>{error.message}</span><button type="button" onClick={retry}>Retry session</button></section>;
  }
  if (notice?.status === 403) {
    return (
      <section className="session-notice refused" role="alert">
        <strong>Daemon refused the request (403):</strong><span>{notice.detail}</span>
        <button type="button" onClick={clearNotice}>Dismiss</button>
      </section>
    );
  }
  return null;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
