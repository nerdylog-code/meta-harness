import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRefusal,
  Approval,
  ApprovalConsumeResult,
  consumeApproval,
  denyApproval,
  fetchApprovals,
  formatTimestamp,
  grantApproval,
} from "../api";
import { EmptyState, Failure, KeyValues, Loading, Panel } from "../components/Panel";

interface Notice {
  kind: "ok" | "error" | "refusal";
  text: string;
}

function errorNotice(error: unknown): Notice {
  if (error instanceof ApiRefusal) {
    return { kind: "refusal", text: `${error.status} refused: ${error.detail}` };
  }
  return { kind: "error", text: error instanceof Error ? error.message : String(error) };
}

function riskClass(risk: Approval["risk_level"]): string {
  if (risk === "R0" || risk === "R1") return "badge badge-strong";
  if (risk === "R2") return "badge badge-moderate";
  if (risk === "R3" || risk === "R4") return "badge badge-weak";
  return "badge badge-unknown";
}

function stateClass(state: Approval["state"]): string {
  if (state === "granted" || state === "consumed") return "badge badge-strong";
  if (state === "pending") return "badge badge-moderate";
  if (state === "denied" || state === "expired") return "badge badge-weak";
  return "badge badge-unknown";
}

const EMPTY_APPROVALS: Approval[] = [];

export function ApprovalsPage() {
  const queryClient = useQueryClient();
  const [names, setNames] = useState<Record<string, string>>({});
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [payloadDrafts, setPayloadDrafts] = useState<Record<string, string>>({});
  const [notices, setNotices] = useState<Record<string, Notice>>({});
  const approvalsQuery = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: fetchApprovals,
    refetchInterval: 5000,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["approvals"] });
  const setNotice = (id: string, notice: Notice) =>
    setNotices((current) => ({ ...current, [id]: notice }));

  const grant = useMutation({
    mutationFn: (input: { id: string; by: string }) => grantApproval(input.id, input.by),
    onSuccess: (approval) => {
      setNotice(approval.id, { kind: "ok", text: `daemon returned state: ${approval.state}` });
      void refresh();
    },
    onError: (error, input) => setNotice(input.id, errorNotice(error)),
  });
  const deny = useMutation({
    mutationFn: (input: { id: string; by: string; reason?: string }) =>
      denyApproval(input.id, input.by, input.reason),
    onSuccess: (approval) => {
      setNotice(approval.id, { kind: "ok", text: `daemon returned state: ${approval.state}` });
      void refresh();
    },
    onError: (error, input) => setNotice(input.id, errorNotice(error)),
  });
  const consume = useMutation({
    mutationFn: (input: {
      id: string;
      action_type: string;
      action_payload: Record<string, unknown>;
      by?: string;
    }) => consumeApproval(input.id, {
      action_type: input.action_type,
      action_payload: input.action_payload,
      ...(input.by ? { by: input.by } : {}),
    }),
    onSuccess: (result: ApprovalConsumeResult) => {
      setNotice(result.approval_id, {
        kind: "ok",
        text: `consume returned authorised: ${String(result.authorised)} · payload hash: ${result.action_payload_hash}`,
      });
      void refresh();
    },
    onError: (error, input) => setNotice(input.id, errorNotice(error)),
  });

  if (approvalsQuery.isLoading) return <Loading label="reading approvals from the daemon" />;
  if (approvalsQuery.isError) return <Failure label="approvals" error={approvalsQuery.error} />;
  const approvals = approvalsQuery.data?.approvals ?? EMPTY_APPROVALS;

  const checkPayload = (approval: Approval) => {
    const text = payloadDrafts[approval.id] ?? JSON.stringify(approval.action_payload, null, 2);
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (error) {
      setNotice(approval.id, {
        kind: "error",
        text: `payload is not valid JSON: ${error instanceof Error ? error.message : String(error)}`,
      });
      return;
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      setNotice(approval.id, { kind: "error", text: "payload must be a JSON object" });
      return;
    }
    const by = (names[approval.id] ?? "").trim();
    consume.mutate({
      id: approval.id,
      action_type: approval.action_type,
      action_payload: parsed as Record<string, unknown>,
      ...(by ? { by } : {}),
    });
  };

  return (
    <>
      <Panel title="Approvals Inbox" hint={`${approvals.length} returned · refreshes every 5 seconds`}>
        <p className="tight">
          A grant covers only the exact action payload shown below. If the payload changes, its hash changes and this approval no longer covers that action.
        </p>
        <p className="tight faint">Review the summary, full payload and hash before deciding. The daemon is the system of record.</p>
      </Panel>

      {approvals.length === 0 ? (
        <Panel title="inbox">
          <EmptyState title="no approvals returned">There are no approvals in the daemon response for this inbox.</EmptyState>
        </Panel>
      ) : approvals.map((approval) => {
        const isPending = approval.state === "pending";
        const draft = payloadDrafts[approval.id] ?? JSON.stringify(approval.action_payload, null, 2);
        const notice = notices[approval.id];
        return (
          <Panel key={approval.id} title={approval.human_summary || "Approval request"} hint={<span className="mono">{approval.id}</span>}>
            <KeyValues rows={[
              ["action type", <span className="mono">{approval.action_type}</span>],
              ["risk level", <span className={riskClass(approval.risk_level)}>{approval.risk_level}</span>],
              ["requires human", approval.requires_human ? "yes" : "no"],
              ["reversibility", approval.reversibility],
              ["requested by", <span className="mono">{approval.requested_by}</span>],
              ["requested at", formatTimestamp(approval.requested_ts)],
              ["expires at", approval.expires_at === null ? "no expiry" : <span className={approval.state === "expired" ? "error" : ""}>{formatTimestamp(approval.expires_at)}{approval.state === "expired" ? " · EXPIRED" : ""}</span>],
              ["state", <span className={stateClass(approval.state)}>{approval.state}</span>],
              ["granted by", approval.granted_by ?? "—"],
              ["granted at", approval.granted_ts === null ? "—" : formatTimestamp(approval.granted_ts)],
              ["consumed at", approval.consumed_ts === null ? "—" : formatTimestamp(approval.consumed_ts)],
              ["reason", approval.reason ?? "—"],
              ["risk floor", approval.risk_floor],
            ]} />
            <div className="approval-payload">
              <h3>Exact action payload</h3>
              <pre className="approval-json">{JSON.stringify(approval.action_payload, null, 2)}</pre>
              <p className="tight">action_payload_hash</p>
              <code className="approval-hash mono">{approval.action_payload_hash}</code>
            </div>
            <div className="approval-controls">
              <label>
                Approver name
                <input aria-label={`approver name for ${approval.id}`} placeholder="your name" value={names[approval.id] ?? ""} onChange={(event) => setNames((current) => ({ ...current, [approval.id]: event.target.value }))} />
              </label>
              <label>
                Denial reason (optional)
                <input aria-label={`denial reason for ${approval.id}`} placeholder="reason" value={reasons[approval.id] ?? ""} onChange={(event) => setReasons((current) => ({ ...current, [approval.id]: event.target.value }))} />
              </label>
              <button type="button" disabled={!isPending || grant.isPending || !(names[approval.id] ?? "").trim()} onClick={() => grant.mutate({ id: approval.id, by: (names[approval.id] ?? "").trim() })}>Approve</button>
              <button type="button" disabled={!isPending || deny.isPending || !(names[approval.id] ?? "").trim()} onClick={() => deny.mutate({ id: approval.id, by: (names[approval.id] ?? "").trim(), ...(reasons[approval.id]?.trim() ? { reason: reasons[approval.id].trim() } : {}) })}>Deny</button>
            </div>
            {!isPending ? <p className="tight faint">This approval is {approval.state}; Approve and Deny are unavailable.</p> : null}
            <section className="approval-check" aria-label={`payload coverage check for ${approval.id}`}>
              <h3>Demonstrate payload coverage</h3>
              <p className="tight">Paste the payload to test against this approval. The daemon decides whether the exact action is authorised.</p>
              <textarea aria-label={`payload to check for ${approval.id}`} value={draft} onChange={(event) => setPayloadDrafts((current) => ({ ...current, [approval.id]: event.target.value }))} />
              <button type="button" disabled={consume.isPending} onClick={() => checkPayload(approval)}>Check with daemon (consume)</button>
            </section>
            {notice ? <p className={`tight ${notice.kind === "ok" ? "ok" : "error"}`} role="status">{notice.kind === "refusal" ? "refused by daemon — " : ""}{notice.text}</p> : null}
          </Panel>
        );
      })}
    </>
  );
}
