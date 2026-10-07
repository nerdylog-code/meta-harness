/**
 * The live event stream, as a hook with an honest connection state.
 *
 * Three states, and the UI must be able to show all three (BOOK §82: never fake green):
 *
 *   connecting  the socket has not opened yet
 *   live        frames are arriving over the socket
 *   degraded    the socket is down and the caller is polling instead
 *
 * Reconnection uses capped exponential backoff. A closed socket is never treated as
 * "no events" -- that conflation is how a UI silently shows stale data as current.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { EventEnvelope, eventStreamUrl } from "../api";

export type StreamState = "connecting" | "live" | "degraded" | "unauthenticated";

const MAX_BACKLOG = 500;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 15000;

export interface EventStream {
  events: EventEnvelope[];
  state: StreamState;
  lastSeq: number;
  error: string | null;
  reconnectAttempts: number;
  clear: () => void;
}

export function useEventStream(): EventStream {
  const [events, setEvents] = useState<EventEnvelope[]>([]);
  const [state, setState] = useState<StreamState>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [attempts, setAttempts] = useState(0);
  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<number | null>(null);
  const closedRef = useRef(false);

  const connect = useCallback(() => {
    if (closedRef.current) return;
    let socket: WebSocket;
    try {
      socket = new WebSocket(eventStreamUrl());
    } catch (cause) {
      setState("degraded");
      setError(cause instanceof Error ? cause.message : "websocket construction failed");
      return;
    }
    socketRef.current = socket;
    let opened = false;

    socket.onopen = () => {
      opened = true;
      setState("live");
      setError(null);
      setAttempts(0);
    };
    socket.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data as string) as EventEnvelope;
        setEvents((current) => {
          const next = [...current, event];
          return next.length > MAX_BACKLOG ? next.slice(next.length - MAX_BACKLOG) : next;
        });
      } catch {
        // A malformed frame must not break the stream; it is dropped and visible as a gap.
      }
    };
    socket.onerror = () => {
      setError("the event socket reported an error");
    };
    socket.onclose = () => {
      if (closedRef.current) return;
      if (!opened) {
        setState("unauthenticated");
        setError("The event stream was rejected before connecting. Open the app through the daemon’s bootstrap link.");
        return;
      }
      setState("degraded");
      setAttempts((current) => {
        const next = current + 1;
        const delay = Math.min(BASE_DELAY_MS * 2 ** (next - 1), MAX_DELAY_MS);
        timerRef.current = window.setTimeout(connect, delay);
        return next;
      });
    };
  }, []);

  useEffect(() => {
    closedRef.current = false;
    connect();
    return () => {
      closedRef.current = true;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [connect]);

  const clear = useCallback(() => setEvents([]), []);
  const lastSeq = events.length ? events[events.length - 1].seq : 0;

  return { events, state, lastSeq, error, reconnectAttempts: attempts, clear };
}
