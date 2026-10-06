/**
 * One websocket per application, shared through context.
 *
 * Two components needing the stream (the topbar's connection state and the event inspector)
 * must not open two sockets: the daemon counts subscribers, and a duplicate socket would make
 * that count a lie. The provider owns the single connection; consumers read it.
 */

import { ReactNode, createContext, useContext } from "react";
import { EventStream, useEventStream } from "./hooks/useEventStream";

const StreamContext = createContext<EventStream | null>(null);

export function EventStreamProvider({ children }: { children: ReactNode }) {
  const stream = useEventStream();
  return <StreamContext.Provider value={stream}>{children}</StreamContext.Provider>;
}

export function useStream(): EventStream {
  const stream = useContext(StreamContext);
  if (!stream) {
    throw new Error("useStream must be used inside EventStreamProvider");
  }
  return stream;
}
