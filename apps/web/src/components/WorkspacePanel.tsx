import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiRefusal,
  Workspace,
  acquireWorkspaceLease,
  allocateWorkspace,
  fetchTaskWorkspace,
  releaseWorkspaceLease,
  removeTaskWorkspace,
  renewWorkspaceLease,
  formatTimestamp,
} from "../api";
import { EmptyState, KeyValues, Loading, Panel } from "./Panel";

function errorText(error: unknown): string {
  if (error instanceof ApiRefusal) return `${error.status} refused: ${error.detail}`;
  return error instanceof Error ? error.message : String(error);
}

export function WorkspacePanel({ taskId }: { taskId: string }) {
  const queryClient = useQueryClient();
  const [repository, setRepository] = useState("");
  const [baseRef, setBaseRef] = useState("HEAD");
  const [acquireRunId, setAcquireRunId] = useState("");
  const [leaseRunId, setLeaseRunId] = useState("");
  const [generation, setGeneration] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const queryKey = ["task-workspace", taskId];
  const workspaceQuery = useQuery({ queryKey, queryFn: () => fetchTaskWorkspace(taskId) });
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey });
    await queryClient.refetchQueries({ queryKey, type: "active" });
  };
  const action = useMutation({
    mutationFn: async (operation: () => Promise<unknown>) => operation(),
    onSuccess: async (result) => {
      const branchKept = result && typeof result === "object" && "branch_kept" in result && result.branch_kept === true;
      setNotice(`daemon action completed${branchKept ? "; branch kept" : ""}`);
      await refresh();
    },
    onError: (error) => setNotice(errorText(error)),
  });

  if (workspaceQuery.isLoading) return <Panel title={`Task workspace · ${taskId}`}><Loading label="reading task workspace from the daemon" /></Panel>;
  // A 404 is the daemon saying "this task has no working copy yet", which is the common case and not
  // a failure: it must fall through to the empty state below, not render as an error. Anything else
  // really is a failure and is shown with its detail.
  if (workspaceQuery.isError) {
    const refusal = workspaceQuery.error instanceof ApiRefusal ? workspaceQuery.error : null;
    if (refusal?.status !== 404) {
      return <Panel title={`Task workspace · ${taskId}`} variant="error"><p className="tight error" role="alert">{errorText(workspaceQuery.error)}</p><button type="button" onClick={() => void workspaceQuery.refetch()}>Retry workspace read</button></Panel>;
    }
  }

  const workspace = workspaceQuery.data ?? null;
  const onAction = (operation: () => Promise<unknown>) => {
    setNotice(null);
    action.mutate(operation);
  };
  const allocateForm = <form className="workspace-form" onSubmit={(event) => { event.preventDefault(); onAction(() => allocateWorkspace(taskId, repository.trim(), baseRef.trim() || "HEAD")); }}>
    <label>Repository path<input aria-label={`repository path for task ${taskId}`} value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="/absolute/path/to/repository" /></label>
    <label>Base ref<input aria-label={`base ref for task ${taskId}`} value={baseRef} onChange={(event) => setBaseRef(event.target.value)} placeholder="HEAD" /></label>
    <button type="submit" disabled={!repository.trim() || action.isPending}>Allocate working copy</button>
  </form>;

  if (!workspace) return <Panel title={`Task workspace · ${taskId}`}>
    <EmptyState title="No isolated working copy exists">Allocate a task-specific git worktree to create one.</EmptyState>
    {allocateForm}
    {notice ? <p className="tight error" role="alert">{notice}</p> : null}
  </Panel>;

  const writer = workspace.writer;
  const shortCommit = workspace.base_commit ? workspace.base_commit.slice(0, 7) : "—";
  return <Panel title={`Task workspace · ${taskId}`} hint={workspace.provider}>
    <KeyValues rows={[
      ["provider", workspace.provider],
      ["state", workspace.state],
      ["branch", <span className="mono workspace-value">{workspace.branch}</span>],
      ["base ref · commit", <span className="mono">{workspace.base_ref} · {shortCommit}</span>],
      ["locator", <span className="mono workspace-value">{workspace.locator}</span>],
      ["dirty", workspace.measured ? workspace.dirty === null ? "not measured" : workspace.dirty ? "dirty" : "clean" : "not measured"],
    ]} />
    <section className="workspace-section" aria-label={`writer lease for ${taskId}`}>
      <h3>Current writer</h3>
      {writer ? <KeyValues rows={[
        ["run id", <span className="mono workspace-value">{writer.run_id}</span>],
        ["generation", String(writer.generation)],
        ["lease expires", formatTimestamp(writer.expires_at)],
        ["active", writer.active ? "yes" : "no"],
        ["expired", writer.expired ? "yes" : "no"],
      ]} /> : <p className="tight">No writer lease is recorded.</p>}
    </section>
    <section className="workspace-section" aria-label={`workspace enforcement for ${taskId}`}>
      <h3>Enforcement</h3>
      <KeyValues rows={[
        ["repository write isolation", <>{workspace.enforcement.write_isolation} · {workspace.enforcement.write_isolation_scope}{workspace.enforcement.write_isolation_detail ? ` · ${workspace.enforcement.write_isolation_detail}` : ""}</>],
        ["sandbox filesystem isolation", <>{workspace.enforcement.filesystem_isolation} · {workspace.enforcement.filesystem_isolation_scope}</>],
      ]} />
      {workspace.enforcement.note ? <p className="tight faint">{workspace.enforcement.note}</p> : null}
    </section>
    {workspace.note ? <p className="tight warn">{workspace.note}</p> : null}
    <div className="workspace-actions">
      <form className="workspace-form" onSubmit={(event) => { event.preventDefault(); onAction(() => acquireWorkspaceLease(taskId, acquireRunId.trim())); }}>
        <label>Run ID<input aria-label={`run id to acquire workspace lease for ${taskId}`} value={acquireRunId} onChange={(event) => setAcquireRunId(event.target.value)} /></label>
        <button type="submit" disabled={!acquireRunId.trim() || action.isPending}>Acquire lease</button>
      </form>
      <form className="workspace-form" onSubmit={(event) => { event.preventDefault(); onAction(() => renewWorkspaceLease(taskId, leaseRunId.trim(), Number(generation))); }}>
        <label>Run ID<input aria-label={`run id to renew workspace lease for ${taskId}`} value={leaseRunId} onChange={(event) => setLeaseRunId(event.target.value)} /></label>
        <label>Generation<input aria-label={`lease generation to renew for ${taskId}`} inputMode="numeric" value={generation} onChange={(event) => setGeneration(event.target.value)} /></label>
        <button type="submit" disabled={!leaseRunId.trim() || !generation.trim() || action.isPending}>Renew lease</button>
      </form>
      <form className="workspace-form" onSubmit={(event) => { event.preventDefault(); onAction(() => releaseWorkspaceLease(taskId, leaseRunId.trim(), Number(generation))); }}>
        <label>Run ID<input aria-label={`run id to release workspace lease for ${taskId}`} value={leaseRunId} onChange={(event) => setLeaseRunId(event.target.value)} /></label>
        <label>Generation<input aria-label={`lease generation to release for ${taskId}`} inputMode="numeric" value={generation} onChange={(event) => setGeneration(event.target.value)} /></label>
        <button type="submit" disabled={!leaseRunId.trim() || !generation.trim() || action.isPending}>Release lease</button>
      </form>
      <button type="button" disabled={action.isPending} onClick={() => onAction(() => removeTaskWorkspace(taskId))}>Safe remove (branch is kept)</button>
    </div>
    {notice ? <p className={`tight ${action.isError ? "error" : "ok"}`} role="status">{notice}</p> : null}
  </Panel>;
}

export type { Workspace };
