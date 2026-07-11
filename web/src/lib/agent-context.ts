import type { AgentChatPayload, ProjectInfo, SessionMessage } from "./types";

export const LEGACY_PROJECT_SELECTION = "__legacy_unassigned__";
export const PROJECT_NAME_MAX_LENGTH = 120;
export const PROJECT_DESCRIPTION_MAX_LENGTH = 1000;
export const PROJECT_ROOT_MAX_LENGTH = 4096;

export interface WorkbenchContextRequest {
  key: string;
  generation: number;
}

export interface ProjectRegistrationRequestIdentity {
  workspaceId: string;
  context: WorkbenchContextRequest;
  sequence: number;
}

export interface AgentChatPayloadInput {
  agent: AgentChatPayload["agent"];
  model: string;
  message: string;
  messages: SessionMessage[];
  sessionId?: string;
  projectId: string;
  name?: string;
}

export function projectFilterForSelection(selection: string): string | null {
  if (selection === LEGACY_PROJECT_SELECTION) return "unassigned";
  return selection.trim() || null;
}

export function projectIdForSelection(selection: string): string | null {
  if (selection === LEGACY_PROJECT_SELECTION) return null;
  return selection.trim() || null;
}

export function selectedProjectFor(
  projects: readonly ProjectInfo[],
  selection: string
): ProjectInfo | null {
  const projectId = projectIdForSelection(selection);
  return projectId ? projects.find((project) => project.id === projectId) ?? null : null;
}

export function projectSelectionAfterLoad(
  projects: readonly ProjectInfo[],
  currentSelection: string
): string {
  if (currentSelection) return currentSelection;
  return projects[0]?.id ?? LEGACY_PROJECT_SELECTION;
}

export function sanitizeProjectDescription(value: string): string {
  return [...value]
    .filter((character) => {
      const code = character.charCodeAt(0);
      return code >= 32 && code !== 127;
    })
    .join("")
    .slice(0, PROJECT_DESCRIPTION_MAX_LENGTH);
}

export function workbenchContextKey(workspaceId: string, projectSelection: string): string {
  return JSON.stringify([workspaceId, projectSelection]);
}

export function isCurrentWorkbenchContext(
  workspaceId: string,
  projectSelection: string,
  currentGeneration: number,
  request: WorkbenchContextRequest
): boolean {
  return (
    request.key === workbenchContextKey(workspaceId, projectSelection) &&
    request.generation === currentGeneration
  );
}

export function isCurrentProjectRegistration(
  workspaceId: string,
  projectSelection: string,
  contextGeneration: number,
  registrationSequence: number,
  request: ProjectRegistrationRequestIdentity
): boolean {
  return (
    request.workspaceId === workspaceId &&
    request.sequence === registrationSequence &&
    isCurrentWorkbenchContext(
      workspaceId,
      projectSelection,
      contextGeneration,
      request.context
    )
  );
}

export function shouldRefreshProjectsAfterRegistration(
  activeWorkspaceId: string,
  requestWorkspaceId: string
): boolean {
  return activeWorkspaceId === requestWorkspaceId;
}

export function buildAgentChatPayload(input: AgentChatPayloadInput): AgentChatPayload {
  return {
    agent: input.agent,
    model: input.model,
    message: input.message,
    messages: input.messages,
    ...(input.sessionId ? { session_id: input.sessionId } : {}),
    project_id: input.projectId,
    ...(input.name ? { name: input.name } : {}),
  };
}

export function projectRegistrationDisabled(
  name: string,
  root: string,
  submitting: boolean,
  description = ""
): boolean {
  return (
    submitting ||
    name.trim().length === 0 ||
    name.length > PROJECT_NAME_MAX_LENGTH ||
    description.length > PROJECT_DESCRIPTION_MAX_LENGTH ||
    root.trim().length === 0 ||
    root.length > PROJECT_ROOT_MAX_LENGTH
  );
}
