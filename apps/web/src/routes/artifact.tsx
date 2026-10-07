import { useEffect, useState } from "react";
import { useParams } from "@tanstack/react-router";
import { ApiRefusal, ArtifactPreview, ArtifactRecord, fetchArtifact, fetchArtifactContent, fetchArtifactPreview, formatTimestamp } from "../api";
import { EmptyState, KeyValues, Loading, Panel } from "../components/Panel";

const PREVIEW_LIMIT = 4096;

function errorText(error: unknown): string {
  if (error instanceof ApiRefusal) {
    return error.status === 409
      ? `integrity check failed (409): ${error.detail}`
      : `${error.status} refused: ${error.detail}`;
  }
  return error instanceof Error ? error.message : String(error);
}

function humanSize(size: number): string {
  if (size < 1024) return `${size} bytes`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = size;
  let unit = -1;
  do { value /= 1024; unit += 1; } while (value >= 1024 && unit < units.length - 1);
  return `${value.toFixed(1)} ${units[unit]}`;
}

function integrityClass(integrity: ArtifactRecord["integrity"]): string {
  if (integrity === "ok") return "badge badge-strong";
  if (integrity === "mismatch" || integrity === "missing") return "badge badge-weak";
  return "badge badge-unknown";
}

function ReadableValues({ title, values }: { title: string; values: Record<string, unknown> }) {
  const entries = Object.entries(values);
  return <Panel title={title}>{entries.length === 0 ? <EmptyState title={`no ${title}`}>The daemon returned no {title}.</EmptyState> : <KeyValues rows={entries.map(([key, value]) => [key, typeof value === "string" ? value : JSON.stringify(value) ?? String(value)])} />}</Panel>;
}

export function ArtifactPage({ artifactId }: { artifactId: string }) {
  const [artifact, setArtifact] = useState<ArtifactRecord | null>(null);
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [previewStart, setPreviewStart] = useState(0);
  const [pageError, setPageError] = useState<string | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    let active = true;
    setArtifact(null); setPreview(null); setPageError(null); setContentError(null); setPreviewStart(0);
    void (async () => {
      try {
        const record = await fetchArtifact(artifactId);
        if (!active) return;
        setArtifact(record);
        if (record.preview_kind !== "binary" && record.preview_available) {
          const result = await fetchArtifactPreview(artifactId, 0, PREVIEW_LIMIT);
          if (!active) return;
          setPreview(result);
        }
      } catch (error) {
        if (active) setPageError(errorText(error));
      }
    })();
    return () => { active = false; };
  }, [artifactId]);

  const reportImageFailure = async () => {
    try {
      await fetchArtifactContent(artifactId);
      setContentError("The artifact content endpoint could not render this image.");
    } catch (error) { setContentError(errorText(error)); }
  };

  const loadMore = async () => {
    if (!preview || loadingMore) return;
    setLoadingMore(true); setContentError(null);
    try {
      const nextStart = preview.start + preview.limit;
      const next = await fetchArtifactPreview(artifactId, nextStart, PREVIEW_LIMIT);
      setPreview(next); setPreviewStart(nextStart);
    } catch (error) { setContentError(errorText(error)); }
    finally { setLoadingMore(false); }
  };

  const download = async () => {
    setContentError(null);
    try {
      const response = await fetchArtifactContent(artifactId);
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = artifactId; anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) { setContentError(errorText(error)); }
  };

  if (pageError) return <Panel title="artifact unavailable" variant="error"><p role="alert">{pageError}</p></Panel>;
  if (!artifact) return <Loading label="reading artifact from the daemon" />;
  const origin = artifact.origin;
  const offset = preview?.start ?? previewStart;
  return <>
    <Panel title="Artifact Inspector" hint={<span className="mono">{artifact.id}</span>}>
      <h1 className="artifact-id">{artifact.id}</h1>
      <KeyValues rows={[
        ["locator", <span className="mono artifact-wrap">{artifact.locator}</span>],
        ["mime", artifact.mime],
        ["size", `${artifact.size} bytes (${humanSize(artifact.size)})`],
        ["sha256", <code className="artifact-sha mono" aria-label="full sha256" tabIndex={0}>{artifact.sha256}</code>],
        ["produced by · mission", origin.mission_id || "—"],
        ["produced by · task", origin.task_id || "—"],
        ["produced by · run", origin.run_id || "—"],
        ["produced by · agent", origin.agent_id || "—"],
        ["created", artifact.created_at === undefined ? "—" : formatTimestamp(artifact.created_at)],
        ["integrity", <span className={integrityClass(artifact.integrity)}>{artifact.integrity}</span>],
      ]} />
      {(artifact.integrity === "mismatch" || artifact.integrity === "missing") && artifact.integrity_detail ? <p className="tight error">{artifact.integrity_detail}</p> : null}
    </Panel>

    <Panel title="Preview">
      {artifact.preview_kind === "binary" ? <>
        <p className="tight">{artifact.preview_reason ?? preview?.reason ?? "Binary artifacts are not rendered as text."}</p>
        <a className="artifact-download" href={`/v1/artifacts/${encodeURIComponent(artifact.id)}/content`} onClick={(event) => { event.preventDefault(); void download(); }}>Download artifact</a>
      </> : artifact.preview_kind === "image" ? <img className="artifact-image" src={`/v1/artifacts/${encodeURIComponent(artifact.id)}/content`} aria-label={`Preview of artifact ${artifact.id}`} alt={`Artifact ${artifact.id}`} onError={() => void reportImageFailure()} /> : preview?.text !== null && preview?.text !== undefined ? <>
        <pre className="artifact-preview">{preview.text}</pre>
        {preview.truncated ? <><p className="tight warn">Preview truncated at byte offset {offset + preview.limit}; more content is available.</p><button type="button" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? "Loading…" : "Load next preview segment"}</button></> : null}
      </> : <p className="tight faint">{preview?.reason ?? artifact.preview_reason ?? "No preview is available."}</p>}
      {contentError ? <p className="tight error" role="alert">{contentError}</p> : null}
    </Panel>
    <ReadableValues title="metadata" values={artifact.metadata} />
    <ReadableValues title="provenance" values={artifact.provenance} />
  </>;
}

export function ArtifactRoute() {
  const { artifactId } = useParams({ from: "/artifacts/$artifactId" });
  return <ArtifactPage artifactId={artifactId} />;
}
