/**
 * The agent surface: a real Pi session, driven from the browser (WP-019 + WP-020).
 *
 * What lives here and what does not:
 *
 * * **domain state is not here.** The roster, the session row and the transcript come from the
 *   daemon (`/v1/agents`, `/v1/sessions`, `/v1/sessions/{id}/events`), which reads them from the
 *   event log. The only thing kept in component state is the *in-flight* text assembled from
 *   transient deltas -- transport state, not truth, and it is replaced by the persisted
 *   `message.completed` the moment it arrives.
 * * **provenance is rendered.** Every usage number is shown with where it came from, and a
 *   metric the provider did not report renders as `unknown` rather than as `0`.
 * * **streaming, settled and cancelled are three different things**, and the surface says which
 *   one it is looking at.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  EventEnvelope,
  cancelSession,
  createSession,
  fetchAgents,
  fetchRuntime,
  fetchSessionEvents,
  fetchSessions,
  formatTimestamp,
  sendMessage,
} from "../api";
import { EmptyState, Failure, KeyValues, Loading, Panel } from "../components/Panel";
import { useStream } from "../stream";

//: Pi's own built-in tool names (from `pi --help`: "read, bash, edit, write tools"). Using a
//: name Pi does not know silently produces a session with no tools at all, which the first real
//: end-to-end run demonstrated: the model answered "my available tools list is empty".
const TOOLS = ["read", "bash", "edit", "write"];

function StreamingText({ text }: { text: string }) {
  if (!text) return null;
  return (
    <pre className="stream" aria-live="polite">
      {text}
    </pre>
  );
}

function UsagePanel({ events }: { events: EventEnvelope[] }) {
  const sample = useMemo(() => {
    const usage = [...events].reverse().find((event) => event.kind === "usage.sampled");
    return usage?.payload?.sample as Record<string, { value: number | null; provenance: string }> | undefined;
  }, [events]);

  if (!sample) {
    return (
      <EmptyState title="no usage yet">
        No usage sample has been recorded for this session. Nothing is drawn as zero: an
        unmeasured number is not the same as a free one.
      </EmptyState>
    );
  }

  const metric = (key: string) => {
    const entry = sample[key];
    if (!entry || entry.value === null || entry.value === undefined) {
      return <span className="faint">unknown</span>;
    }
    return (
      <>
        <span className="mono">{entry.value}</span> <span className="faint">· {entry.provenance}</span>
      </>
    );
  };

  return (
    <KeyValues
      rows={[
        ["input", metric("input_tokens")],
        ["output", metric("output_tokens")],
        ["reasoning", metric("reasoning_tokens")],
        ["cache read", metric("cache_read_tokens")],
        ["cache write", metric("cache_write_tokens")],
        ["cost", metric("provider_cost")],
        ["provider", <span className="mono">{String(sample.provider ?? "unknown")}</span>],
        ["model", <span className="mono">{String(sample.model ?? "unknown")}</span>],
      ]}
    />
  );
}

function ToolCallList({ events }: { events: EventEnvelope[] }) {
  const calls = useMemo(
    () =>
      events
        .filter((event) => event.kind === "tool.started" || event.kind === "tool.completed")
        .slice(-12)
        .reverse(),
    [events],
  );
  if (calls.length === 0) {
    return (
      <EmptyState title="no tool calls">
        This session has not called a tool. A call appears here with a bounded preview; the full
        output belongs in an artifact, not in the conversation.
      </EmptyState>
    );
  }
  return (
    <ul className="tools">
      {calls.map((event) => (
        <li key={event.id}>
          <span className={event.kind === "tool.completed" ? "ok" : "warn"}>
            {event.kind === "tool.completed" ? "done" : "start"}
          </span>{" "}
          <span className="mono">{String(event.payload.tool ?? "?")}</span>{" "}
          {event.kind === "tool.completed" ? (
            <span className="faint">
              {String(event.payload.duration_ms ?? "?")}ms · {String(event.payload.result_chars ?? 0)} chars
              {event.payload.truncated ? " (preview)" : ""}
            </span>
          ) : (
            <span className="faint">{String(event.payload.args_preview ?? "")}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function AgentPage({ agentId }: { agentId: string }) {
  const stream = useStream();
  const queryClient = useQueryClient();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [model, setModel] = useState("");
  const [tools, setTools] = useState<string[]>(["read_file"]);
  const [draftText, setDraftText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const bufferRef = useRef<string>("");

  const agents = useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
  const sessions = useQuery({ queryKey: ["sessions", agentId], queryFn: () => fetchSessions(agentId) });
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: fetchRuntime, refetchInterval: 5000 });
  const history = useQuery({
    queryKey: ["session-events", sessionId],
    queryFn: () => fetchSessionEvents(sessionId as string),
    enabled: Boolean(sessionId),
    refetchInterval: 4000,
  });

  const agent = agents.data?.agents.find((row) => row.id === agentId);
  const currentSession = sessions.data?.sessions.find((row) => row.id === sessionId);

  // Transient deltas for the session on screen. Transport state only: the persisted
  // message.completed is the record, and it clears the buffer.
  useEffect(() => {
    if (!sessionId) return;
    const relevant = stream.events.filter((event) => event.session_id === sessionId);
    const last = relevant[relevant.length - 1];
    if (!last) return;
    if (last.kind === "message.delta" && typeof last.payload.text === "string") {
      bufferRef.current += last.payload.text;
      setDraftText(bufferRef.current);
    }
    if (last.kind === "message.completed") {
      bufferRef.current = "";
      setDraftText("");
    }
  }, [stream.events, sessionId]);

  const startSession = useMutation({
    mutationFn: () => createSession(agentId, null, tools),
    onSuccess: async (data) => {
      setSessionId(data.session_id);
      bufferRef.current = "";
      setDraftText("");
      setNotice(`session ${data.session_id} started on ${data.runtime_id} (pid ${data.pid})`);
      await queryClient.invalidateQueries({ queryKey: ["sessions", agentId] });
    },
    onError: (error: Error) => setNotice(`could not start a session: ${error.message}`),
  });

  const send = useMutation({
    mutationFn: () => sendMessage(sessionId as string, draft),
    onSuccess: () => {
      setDraft("");
      setNotice("prompt accepted — completion is runtime.pi.settled, not this response");
    },
    onError: (error: Error) => setNotice(`send failed: ${error.message}`),
  });

  const cancel = useMutation({
    mutationFn: () => cancelSession(sessionId as string),
    onSuccess: async (data) => {
      setNotice(
        data.orphans_left === null
          ? "cancelled"
          : data.orphans_left
            ? "cancelled — WARNING: a process survived"
            : "cancelled cleanly, no orphan process",
      );
      await queryClient.invalidateQueries({ queryKey: ["sessions", agentId] });
    },
    onError: (error: Error) => setNotice(`cancel failed: ${error.message}`),
  });

  if (agents.isLoading) return <Loading label="reading the roster" />;
  if (agents.isError) return <Failure label="agents" error={agents.error} />;
  if (!agent) {
    return (
      <Panel title="unknown agent" variant="error">
        <p className="muted">No agent with id <span className="mono">{agentId}</span> exists.</p>
      </Panel>
    );
  }

  const events = history.data ?? [];
  const settled = events.some((event) => event.kind === "runtime.pi.settled");
  const state = currentSession?.state ?? "none";

  return (
    <>
      <Panel title={agent.display_name} hint={`${agent.id} · ${agent.role}`}>
        <KeyValues
          rows={[
            ["identity", <span className="mono">{agent.id}</span>],
            ["versions", String(agent.versions.length)],
            [
              "runtime policy",
              <span className="mono">{agent.versions.at(-1)?.runtime_preferred ?? "—"}</span>,
            ],
            ["model policy", <span className="mono">{agent.versions.at(-1)?.model_primary ?? "—"}</span>],
            [
              "runtime",
              runtime.data
                ? runtime.data.runtime.available
                  ? <span className="ok">available · {runtime.data.runtime.protocol}</span>
                  : <span className="error">unavailable · {runtime.data.runtime.detail}</span>
                : "…",
            ],
          ]}
        />
      </Panel>

      <Panel
        title="session"
        hint={sessionId ? `${sessionId} · ${state}${settled ? " · settled" : " · open"}` : "not started"}
      >
        <div className="toolbar">
          <input
            aria-label="model"
            placeholder="model (blank = runtime default)"
            value={model}
            onChange={(event) => setModel(event.target.value)}
          />
          <select
            aria-label="tools"
            value={tools[0] ?? ""}
            onChange={(event) => setTools(event.target.value ? [event.target.value] : [])}
          >
            <option value="">no tools</option>
            {TOOLS.map((tool) => (
              <option key={tool} value={tool}>
                {tool}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => startSession.mutate()} disabled={startSession.isPending}>
            {sessionId ? "new session" : "start session"}
          </button>
          <button
            type="button"
            onClick={() => cancel.mutate()}
            disabled={!sessionId || cancel.isPending}
          >
            cancel
          </button>
        </div>
        {notice ? <p className="tight faint">{notice}</p> : null}
        {currentSession?.reason ? (
          <p className="tight warn">reason: {currentSession.reason}</p>
        ) : null}
      </Panel>

      <div className="grid">
        <Panel title="conversation" hint={`${events.length} events persisted`}>
          <div className="toolbar">
            <input
              aria-label="message"
              placeholder={sessionId ? "message the agent…" : "start a session first"}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && sessionId && draft.trim() && !send.isPending) {
                  send.mutate();
                }
              }}
              disabled={!sessionId}
            />
            <button
              type="button"
              onClick={() => send.mutate()}
              disabled={!sessionId || !draft.trim() || send.isPending}
            >
              send
            </button>
          </div>
          <StreamingText text={draftText} />
          {events.length === 0 ? (
            <EmptyState title="nothing yet">
              Start a session and send a message. Every turn that completes is written to the log,
              so this transcript survives a restart.
            </EmptyState>
          ) : (
            <ul className="transcript">
              {events
                .filter((event) => event.kind === "message.completed")
                .map((event) => (
                  <li key={event.id} className={event.payload.role === "assistant" ? "assistant" : "user"}>
                    <span className="faint">
                      {String(event.payload.role)} · {formatTimestamp(event.ts)}
                    </span>
                    <pre>{String(event.payload.text ?? "").slice(0, 4000)}</pre>
                  </li>
                ))}
            </ul>
          )}
        </Panel>

        <Panel title="usage" hint="provenance is shown, never guessed">
          <UsagePanel events={events} />
        </Panel>

        <Panel title="tool calls" hint="previews only">
          <ToolCallList events={events} />
        </Panel>
      </div>
    </>
  );
}
