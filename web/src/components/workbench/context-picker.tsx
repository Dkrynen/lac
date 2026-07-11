import { useEffect, useState } from "react";
import { Code2, FolderOpen, Plus, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  LEGACY_PROJECT_SELECTION,
  PROJECT_DESCRIPTION_MAX_LENGTH,
  PROJECT_NAME_MAX_LENGTH,
  PROJECT_ROOT_MAX_LENGTH,
  projectRegistrationDisabled,
  sanitizeProjectDescription,
} from "@/lib/agent-context";
import type { ProjectInfo, ProjectRegistrationInput, WorkspaceInfo } from "@/lib/types";

interface ContextPickerProps {
  workspaces: WorkspaceInfo[];
  workspacesLoading: boolean;
  workspaceId: string;
  projects: ProjectInfo[];
  projectsLoading: boolean;
  projectsError: string | null;
  projectSelection: string;
  selectedProject: ProjectInfo | null;
  registering: boolean;
  onWorkspaceChange: (workspaceId: string) => void;
  onProjectChange: (selection: string) => void;
  onRegister: (input: ProjectRegistrationInput) => Promise<boolean>;
}

export function ContextPicker({
  workspaces,
  workspacesLoading,
  workspaceId,
  projects,
  projectsLoading,
  projectsError,
  projectSelection,
  selectedProject,
  registering,
  onWorkspaceChange,
  onProjectChange,
  onRegister,
}: ContextPickerProps) {
  const [showRegistration, setShowRegistration] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [root, setRoot] = useState("");

  useEffect(() => {
    setShowRegistration(false);
    setName("");
    setDescription("");
    setRoot("");
  }, [workspaceId]);

  const submit = async () => {
    if (projectRegistrationDisabled(name, root, registering, description)) return;
    const created = await onRegister({
      name: name.trim(),
      ...(description.trim() ? { description: description.trim() } : {}),
      root: root.trim(),
    });
    if (!created) return;
    setName("");
    setDescription("");
    setRoot("");
    setShowRegistration(false);
  };

  return (
    <>
      <div className="border-b border-line p-3">
        <div className="mb-2 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
          <FolderOpen className="h-3.5 w-3.5" /> Workspace
        </div>
        {workspacesLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : (
          <Select value={workspaceId} onValueChange={onWorkspaceChange}>
            <SelectTrigger className="h-8" aria-label="Workspace">
              <SelectValue placeholder="Select workspace" />
            </SelectTrigger>
            <SelectContent>
              {workspaces.map((workspace) => (
                <SelectItem key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      <div className="border-b border-line p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
            <Code2 className="h-3.5 w-3.5" /> Project
          </div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 px-2"
            onClick={() => setShowRegistration((visible) => !visible)}
            disabled={!workspaceId || projectsLoading || registering}
            aria-expanded={showRegistration}
          >
            {showRegistration ? <X /> : <Plus />}
            {showRegistration ? "Cancel" : "Add"}
          </Button>
        </div>

        {projectsLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : (
          <Select value={projectSelection} onValueChange={onProjectChange} disabled={!workspaceId}>
            <SelectTrigger className="h-8" aria-label="Project">
              <SelectValue placeholder={projects.length ? "Select project" : "Register a project"} />
            </SelectTrigger>
            <SelectContent>
              {projects.map((project) => (
                <SelectItem key={project.id} value={project.id}>
                  {project.name}
                </SelectItem>
              ))}
              <SelectItem value={LEGACY_PROJECT_SELECTION}>Legacy / Unassigned</SelectItem>
            </SelectContent>
          </Select>
        )}

        {projectsError && (
          <div role="alert" className="mt-1.5 text-[11px] leading-relaxed text-danger">
            {projectsError}
          </div>
        )}

        {selectedProject ? (
          <div className="mt-2 rounded border border-line bg-panel-2 px-2.5 py-2">
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="text-[10.5px] font-semibold uppercase tracking-[0.08em] text-fg-faint">
                Canonical root
              </span>
              <Badge variant="success">Registered</Badge>
            </div>
            <code className="block break-all text-[11px] leading-relaxed text-fg-muted" title={selectedProject.root}>
              {selectedProject.root}
            </code>
          </div>
        ) : projectSelection === LEGACY_PROJECT_SELECTION ? (
          <div className="mt-2 rounded border border-warning/30 bg-warning-soft px-2.5 py-2 text-[11px] leading-relaxed text-warning">
            Legacy threads are unassigned. Select a registered project to start a new runnable thread.
          </div>
        ) : (
          <div className="mt-2 text-[11px] leading-relaxed text-fg-muted">
            Register an existing local folder to create project-bound threads.
          </div>
        )}

        {showRegistration && (
          <form
            className="mt-3 space-y-2 border-t border-line pt-3"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={PROJECT_NAME_MAX_LENGTH}
              placeholder="Project name"
              aria-label="Project name"
              autoComplete="off"
              className="h-8 text-[12.5px]"
              disabled={registering}
              required
            />
            <Input
              value={root}
              onChange={(event) => setRoot(event.target.value)}
              maxLength={PROJECT_ROOT_MAX_LENGTH}
              placeholder="C:\\path\\to\\existing-project"
              aria-label="Existing project root"
              autoComplete="off"
              className="h-8 text-[12.5px]"
              disabled={registering}
              required
            />
            <Input
              value={description}
              onChange={(event) => setDescription(sanitizeProjectDescription(event.target.value))}
              maxLength={PROJECT_DESCRIPTION_MAX_LENGTH}
              placeholder="Optional description"
              aria-label="Project description"
              autoComplete="off"
              disabled={registering}
              className="h-8 text-[12.5px]"
            />
            <div className="text-[10.5px] leading-relaxed text-fg-faint">
              Registration records this folder. It never creates, moves, or deletes it.
            </div>
            <Button
              type="submit"
              size="sm"
              className="w-full"
              disabled={projectRegistrationDisabled(name, root, registering, description)}
            >
              <Plus /> {registering ? "Registering..." : "Register project"}
            </Button>
          </form>
        )}
      </div>
    </>
  );
}
