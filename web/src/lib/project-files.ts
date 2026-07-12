import type {
  ProjectFileDetail,
  ProjectFileEntry,
  ProjectFilesResponse,
} from "./types";

const MAX_PROJECT_PATH_LENGTH = 512;
const MAX_PROJECT_FILE_NAME_LENGTH = 255;
const MAX_PROJECT_FILE_ENTRIES = 1_000;
const MAX_PROJECT_FILE_BYTES = 1024 * 1024;
const WINDOWS_RESERVED_NAME = /^(?:con|prn|aux|nul|clock\$|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?$/i;
const INVALID_PROJECT_FILE_NAME_CHARACTER = /[<>:"\\|?*\u0000-\u001f\u007f\u061c\u200b\u200e\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]/;

export interface ProjectFileRequest {
  projectId: string;
  sequence: number;
  kind?: "directory" | "file";
  path?: string;
}

export interface ProjectFileBreadcrumb {
  label: string;
  path: string;
}

function invalidProjectFilesResponse(): never {
  throw new Error("Invalid project files response");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(record: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(record).sort();
  const wanted = [...expected].sort();
  return actual.length === wanted.length && actual.every((key, index) => key === wanted[index]);
}

function assertProjectFileName(name: string): void {
  if (
    !name ||
    name.length > MAX_PROJECT_FILE_NAME_LENGTH ||
    name === "." ||
    name === ".." ||
    name.includes("/") ||
    INVALID_PROJECT_FILE_NAME_CHARACTER.test(name) ||
    name.endsWith(".") ||
    name.endsWith(" ") ||
    WINDOWS_RESERVED_NAME.test(name)
  ) {
    throw new Error("Invalid project file name");
  }
}

export function normalizeProjectFilePath(path: string, allowProject = true): string {
  if (typeof path !== "string" || path.length > MAX_PROJECT_PATH_LENGTH) {
    throw new Error("Invalid project file path");
  }
  if (path === "") {
    if (allowProject) return "";
    throw new Error("Invalid project file path");
  }
  if (path.startsWith("/") || path.endsWith("/") || path.includes("//")) {
    throw new Error("Invalid project file path");
  }
  const parts = path.split("/");
  for (const part of parts) assertProjectFileName(part);
  return parts.join("/");
}

export function projectFileChildPath(path: string, name: string): string {
  const parent = normalizeProjectFilePath(path);
  assertProjectFileName(name);
  return normalizeProjectFilePath(parent ? `${parent}/${name}` : name, false);
}

export function projectFileParentPath(path: string): string {
  const normalized = normalizeProjectFilePath(path);
  if (!normalized) return "";
  const separator = normalized.lastIndexOf("/");
  return separator === -1 ? "" : normalized.slice(0, separator);
}

export function projectFileBreadcrumbs(path: string): ProjectFileBreadcrumb[] {
  const normalized = normalizeProjectFilePath(path);
  const breadcrumbs: ProjectFileBreadcrumb[] = [{ label: "Project", path: "" }];
  if (!normalized) return breadcrumbs;
  let current = "";
  for (const part of normalized.split("/")) {
    current = current ? `${current}/${part}` : part;
    breadcrumbs.push({ label: part, path: current });
  }
  return breadcrumbs;
}

export function isCurrentProjectFileRequest(
  activeProjectId: string,
  currentSequence: number,
  request: ProjectFileRequest
): boolean {
  return Boolean(activeProjectId) &&
    activeProjectId === request.projectId &&
    currentSequence === request.sequence;
}

function decodeEntry(value: unknown): ProjectFileEntry {
  if (!isRecord(value) || !hasExactKeys(value, ["name", "type", "size"])) {
    return invalidProjectFilesResponse();
  }
  if (typeof value.name !== "string") return invalidProjectFilesResponse();
  try {
    assertProjectFileName(value.name);
  } catch {
    return invalidProjectFilesResponse();
  }
  if (
    (value.type !== "dir" && value.type !== "file") ||
    typeof value.size !== "number" ||
    !Number.isSafeInteger(value.size) ||
    value.size < 0
  ) return invalidProjectFilesResponse();
  return { name: value.name, type: value.type, size: value.size };
}

export function decodeProjectFilesResponse(
  value: unknown,
  expectedPath?: string
): ProjectFilesResponse {
  if (!isRecord(value) || !hasExactKeys(value, ["path", "entries", "truncated"])) {
    return invalidProjectFilesResponse();
  }
  if (
    typeof value.path !== "string" ||
    !Array.isArray(value.entries) ||
    value.entries.length > MAX_PROJECT_FILE_ENTRIES ||
    typeof value.truncated !== "boolean"
  ) return invalidProjectFilesResponse();

  let path: string;
  try {
    path = normalizeProjectFilePath(value.path);
  } catch {
    return invalidProjectFilesResponse();
  }
  if (expectedPath !== undefined && path !== normalizeProjectFilePath(expectedPath)) {
    return invalidProjectFilesResponse();
  }
  const entries = value.entries.map(decodeEntry);
  const names = new Set<string>();
  for (const entry of entries) {
    const identity = entry.name.toLocaleLowerCase("en-US");
    if (names.has(identity)) return invalidProjectFilesResponse();
    names.add(identity);
    try {
      projectFileChildPath(path, entry.name);
    } catch {
      return invalidProjectFilesResponse();
    }
  }
  return { path, entries, truncated: value.truncated };
}

export function decodeProjectFileDetail(
  value: unknown,
  expectedPath?: string
): ProjectFileDetail {
  if (!isRecord(value) || !hasExactKeys(value, ["path", "content", "sha256", "size"])) {
    return invalidProjectFilesResponse();
  }
  if (
    typeof value.path !== "string" ||
    typeof value.content !== "string" ||
    typeof value.sha256 !== "string" ||
    !/^[0-9a-f]{64}$/.test(value.sha256) ||
    typeof value.size !== "number" ||
    !Number.isSafeInteger(value.size) ||
    value.size < 0 ||
    value.size > MAX_PROJECT_FILE_BYTES
  ) return invalidProjectFilesResponse();

  let path: string;
  try {
    path = normalizeProjectFilePath(value.path, false);
  } catch {
    return invalidProjectFilesResponse();
  }
  if (expectedPath !== undefined && path !== normalizeProjectFilePath(expectedPath, false)) {
    return invalidProjectFilesResponse();
  }
  if (new TextEncoder().encode(value.content).byteLength !== value.size) {
    return invalidProjectFilesResponse();
  }
  const contentCharacters = Array.from(value.content);
  for (let index = 0; index < contentCharacters.length; index += 1) {
    const character = contentCharacters[index];
    const codepoint = character.codePointAt(0) ?? 0;
    if (codepoint === 0xfeff && index === 0) continue;
    if (character === "\t" || character === "\n" || character === "\r") continue;
    if (
      codepoint < 0x20 ||
      (codepoint >= 0x7f && codepoint <= 0x9f) ||
      codepoint === 0x061c ||
      codepoint === 0x200b ||
      codepoint === 0x200e ||
      codepoint === 0x200f ||
      (codepoint >= 0x202a && codepoint <= 0x202e) ||
      codepoint === 0x2060 ||
      (codepoint >= 0x2066 && codepoint <= 0x2069) ||
      codepoint === 0xfeff
    ) {
      return invalidProjectFilesResponse();
    }
  }
  return { path, content: value.content, sha256: value.sha256, size: value.size };
}
