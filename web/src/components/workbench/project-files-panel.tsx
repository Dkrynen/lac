import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, FileText, Folder, RefreshCw } from "lucide-react";
import { ApiError, api } from "@/lib/api";
import {
  isCurrentProjectFileRequest,
  projectFileBreadcrumbs,
  projectFileChildPath,
  projectFileParentPath,
} from "@/lib/project-files";
import type { ProjectFileDetail, ProjectFilesResponse } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ProjectFilesPanelProps {
  projectId: string;
}

interface ActiveRequest {
  controller: AbortController;
  projectId: string;
  sequence: number;
  kind: "directory" | "file";
  path: string;
}

function requestErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return "This project registration is no longer valid because its folder identity changed. Restore the original registered folder, then refresh.";
    }
    if (error.status === 403) return "This project item is protected and cannot be previewed.";
    if (error.status === 404) return "This project item is no longer available.";
    if (error.status === 413) return "This file is too large to preview.";
    if (error.status === 415) return "This file is not supported as a text preview.";
    if (error.status === 400) return "That project path is not available.";
  }
  return "Project files are temporarily unavailable.";
}

function isProjectIdentityDrift(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function ProjectFilesPanel({ projectId }: ProjectFilesPanelProps) {
  const [directory, setDirectory] = useState<ProjectFilesResponse>({
    path: "",
    entries: [],
    truncated: false,
  });
  const [directoryLoading, setDirectoryLoading] = useState(false);
  const [directoryError, setDirectoryError] = useState("");
  const [previewPath, setPreviewPath] = useState("");
  const [preview, setPreview] = useState<ProjectFileDetail | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const projectIdRef = useRef(projectId);
  const sequenceRef = useRef(0);
  const activeRequestRef = useRef<ActiveRequest | null>(null);

  const beginRequest = useCallback((targetProjectId: string, kind: ActiveRequest["kind"], path: string) => {
    activeRequestRef.current?.controller.abort();
    const request: ActiveRequest = {
      controller: new AbortController(),
      projectId: targetProjectId,
      sequence: ++sequenceRef.current,
      kind,
      path,
    };
    activeRequestRef.current = request;
    return request;
  }, []);

  const requestIsCurrent = useCallback((request: ActiveRequest) => (
    activeRequestRef.current === request &&
    isCurrentProjectFileRequest(projectIdRef.current, sequenceRef.current, request)
  ), []);

  const loadDirectory = useCallback(async (targetProjectId: string, path: string) => {
    const request = beginRequest(targetProjectId, "directory", path);
    setDirectory({ path, entries: [], truncated: false });
    setDirectoryLoading(true);
    setDirectoryError("");
    setPreviewPath("");
    setPreview(null);
    setPreviewLoading(false);
    setPreviewError("");
    try {
      const result = await api.projectFiles(targetProjectId, path, request.controller.signal);
      if (!requestIsCurrent(request) || request.kind !== "directory" || request.path !== result.path) return;
      setDirectory(result);
    } catch (error) {
      if (!requestIsCurrent(request) || request.controller.signal.aborted) return;
      setDirectory({ path: isProjectIdentityDrift(error) ? "" : path, entries: [], truncated: false });
      setDirectoryError(requestErrorMessage(error));
    } finally {
      if (requestIsCurrent(request)) setDirectoryLoading(false);
    }
  }, [beginRequest, projectId, requestIsCurrent]);

  const loadFile = useCallback(async (path: string) => {
    const request = beginRequest(projectId, "file", path);
    setPreviewPath(path);
    setPreview(null);
    setPreviewLoading(true);
    setPreviewError("");
    try {
      const result = await api.projectFile(projectId, path, request.controller.signal);
      if (!requestIsCurrent(request) || request.kind !== "file" || request.path !== result.path) return;
      setPreview(result);
    } catch (error) {
      if (!requestIsCurrent(request) || request.controller.signal.aborted) return;
      if (isProjectIdentityDrift(error)) {
        setDirectory({ path: "", entries: [], truncated: false });
        setDirectoryError(requestErrorMessage(error));
        setPreviewPath("");
        setPreview(null);
        setPreviewError("");
        return;
      }
      setPreviewError(requestErrorMessage(error));
    } finally {
      if (requestIsCurrent(request)) setPreviewLoading(false);
    }
  }, [beginRequest, projectId, requestIsCurrent]);

  useEffect(() => {
    projectIdRef.current = projectId;
    activeRequestRef.current?.controller.abort();
    activeRequestRef.current = null;
    sequenceRef.current += 1;
    setDirectory({ path: "", entries: [], truncated: false });
    setDirectoryError("");
    setPreviewPath("");
    setPreview(null);
    setPreviewError("");
    setPreviewLoading(false);
    if (projectId) void loadDirectory(projectId, "");

    return () => {
      activeRequestRef.current?.controller.abort();
      activeRequestRef.current = null;
      sequenceRef.current += 1;
    };
  }, [loadDirectory, projectId]);

  const breadcrumbs = projectFileBreadcrumbs(directory.path);

  return (
    <div
      className="flex min-h-0 flex-1 flex-col"
      aria-busy={directoryLoading || previewLoading}
    >
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2">
        <span className="text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">Files</span>
        <Badge variant="outline">Read only</Badge>
      </div>

      {!projectId ? (
        <div className="px-3 py-8 text-center text-[12.5px] text-fg-muted">Select a registered project</div>
      ) : previewPath ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-2 border-b border-line px-2 py-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2"
              onClick={() => {
                activeRequestRef.current?.controller.abort();
                sequenceRef.current += 1;
                setPreviewPath("");
                setPreview(null);
                setPreviewError("");
                setPreviewLoading(false);
              }}
            >
              <ChevronLeft /> Folder
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-3">
            <div title={previewPath} className="break-all text-[12.5px] font-semibold text-fg">{previewPath}</div>
            {preview && (
              <div className="mt-1 text-[11px] text-fg-faint">
                {formatFileSize(preview.size)} · SHA-256 {preview.sha256.slice(0, 12)}…
              </div>
            )}
            {previewLoading ? (
              <div role="status" aria-live="polite" className="mt-4 text-[12px] text-fg-muted">
                Loading preview…
              </div>
            ) : previewError ? (
              <div role="alert" className="mt-4 rounded border border-danger/30 bg-danger-soft p-2 text-[12px] text-danger">
                {previewError}
              </div>
            ) : preview ? (
              <pre className="mt-3 overflow-auto whitespace-pre text-[11.5px] leading-relaxed text-fg-muted">
                <code>{preview.content}</code>
              </pre>
            ) : null}
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center gap-1 border-b border-line px-2 py-2">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label="Parent folder"
              disabled={!directory.path || directoryLoading}
              onClick={() => void loadDirectory(projectId, projectFileParentPath(directory.path))}
            >
              <ChevronLeft />
            </Button>
            <div className="flex min-w-0 flex-1 items-center overflow-hidden text-[11.5px] text-fg-muted">
              {breadcrumbs.map((crumb, index) => (
                <span key={crumb.path || "project"} className="flex min-w-0 items-center">
                  {index > 0 && <ChevronRight className="h-3 w-3 shrink-0 text-fg-faint" />}
                  <button
                    type="button"
                    title={crumb.label}
                    aria-current={index === breadcrumbs.length - 1 ? "page" : undefined}
                    className={cn("truncate rounded px-1 py-0.5 hover:bg-panel-3 hover:text-fg", index === breadcrumbs.length - 1 && "text-fg")}
                    disabled={directoryLoading || index === breadcrumbs.length - 1}
                    onClick={() => void loadDirectory(projectId, crumb.path)}
                  >
                    {crumb.label}
                  </button>
                </span>
              ))}
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label="Refresh project files"
              disabled={directoryLoading}
              onClick={() => void loadDirectory(projectId, directory.path)}
            >
              <RefreshCw className={cn(directoryLoading && "animate-spin")} />
            </Button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {directoryError ? (
              <div role="alert" className="rounded border border-danger/30 bg-danger-soft p-2 text-[12px] text-danger">
                {directoryError}
              </div>
            ) : directoryLoading && !directory.entries.length ? (
              <div role="status" aria-live="polite" className="px-2 py-6 text-center text-[12px] text-fg-muted">
                Loading files…
              </div>
            ) : directory.entries.length ? (
              <div className="space-y-0.5">
                {directory.entries.map((entry) => {
                  const path = projectFileChildPath(directory.path, entry.name);
                  const Icon = entry.type === "dir" ? Folder : FileText;
                  return (
                    <button
                      key={`${entry.type}:${entry.name}`}
                      type="button"
                      title={entry.name}
                      disabled={directoryLoading}
                      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12.5px] text-fg-muted hover:bg-panel-3 hover:text-fg disabled:opacity-50"
                      onClick={() => entry.type === "dir"
                        ? void loadDirectory(projectId, path)
                        : void loadFile(path)}
                    >
                      <Icon className={cn("h-4 w-4 shrink-0", entry.type === "dir" && "text-warning")} />
                      <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                      {entry.type === "file" && (
                        <span className="shrink-0 text-[10.5px] text-fg-faint">{formatFileSize(entry.size)}</span>
                      )}
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="px-2 py-8 text-center text-[12.5px] text-fg-muted">No previewable files</div>
            )}
            {directory.truncated && (
              <div role="status" aria-live="polite" className="mt-2 rounded border border-warning/30 bg-warning-soft p-2 text-[11.5px] text-warning">
                This folder is truncated to the first safe entries.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
