# M2 Plan 5: Registered Projects and Project-Bound Threads

**Date:** 2026-07-11
**Status:** Accepted for implementation
**Scope:** Local Agency Workbench project identity and safe file boundary

## Objective

Replace Workbench's global, browser-stored project-root string with durable local
project records. A Workbench thread belongs to exactly one registered project,
and that project belongs to exactly one existing workspace. Plan, Explore,
Build, approvals, staged changes, and the Docker task sandbox all resolve the
same canonical root from that server-side record.

This slice creates the identity spine that artifact browsing, project memory,
role agents, and activity history will use later. It does not add those later
features.

## Domain model

Workspaces keep their current meaning: local operator groupings such as a
client, internal operation, or product. Projects are working directories inside
those groupings. Threads are conversations and runs inside one project.

The SQLite store gains an immutable `projects` record with:

- a stable opaque ID;
- its workspace ID;
- a bounded display name and optional description;
- one canonical absolute root and a case-normalized root key;
- the root's filesystem device and file identity;
- active status plus creation/update timestamps.

`sessions.project_id` is nullable only for pre-Plan-5 data and non-Workbench
legacy callers. A project-bound session's `workspace` must equal its project's
workspace. Neither identity may be changed by transcript saves or chat resumes.
Project rename, root relocation, archive, and delete operations are deferred.

## Registration and root identity

`POST /api/workspaces/<workspace_id>/projects` registers an existing local
directory. Registration is loopback-only and must fail before mutation when:

- the workspace does not exist;
- name/root fields are missing, malformed, control-bearing, or over bounds;
- the root is relative, missing, not a directory, a filesystem/volume root, the
  user's home itself, LAC's private data root, or an ancestor of either;
- the root or any resolved root identity is a symlink, junction, mount-point
  indirection, or Windows reparse point;
- the canonical/root-key or physical filesystem identity is already registered,
  including case variants and concurrent requests.

Registration never creates, moves, edits, or deletes the external directory.
The record stores both the canonical path and stable filesystem identity. Every
project-bound operation revalidates both before provider construction or disk
access. A missing, replaced, moved, or identity-drifted root fails closed.

One physical directory cannot be registered to two workspaces. Workspace
deletion returns `409` while projects are registered and never touches project
directories.

## Thread binding

New Workbench threads require `project_id`. The server derives workspace and
root; the browser cannot also supply `cwd`, and a supplied workspace must match
the project. On resume:

- an omitted project ID may reuse the saved bound project;
- a different project, workspace, or caller-supplied root returns `409` before
  provider construction, run registration, events, or tools;
- transcript saves may update name, model, messages, and timestamps only;
- legacy `project_id = NULL` threads remain visible as `Legacy / Unassigned`,
  but are never silently guessed, migrated, or bound from the current picker;
- legacy raw-`cwd` API compatibility remains isolated from the registered
  Workbench path for this slice and cannot move an already bound thread.

Session listing accepts a project filter. Project-bound UI lists only that
project's threads; the explicit legacy view lists only null-project threads.

## Rooted execution

For a registered Workbench request, the root is resolved once from the project
record and becomes the single source for:

- project configuration and provider resolution;
- Plan/Explore read and list tools;
- Build permission scope, staged read/write overlays, and approval display;
- staged-change list/apply/reject/revert and batch apply;
- Docker task sandbox capability and snapshot preparation.

When `project_id` is present, `cwd` is rejected rather than compared. Workspace
or project mismatch returns `409`; invalid or drifted project state returns
`409`. These checks occur before a provider can receive data.

Project-bound staged rows must have `root == project.root`. Creation, listing,
detail, apply, reject, revert, batch apply, and sandbox-overlay preparation all
enforce that equality, including against manually corrupted database rows.
Legacy unbound sessions keep their existing exact-root behavior only for
backward compatibility.

## Hard sensitive-path boundary

Selecting a project is not consent to disclose or edit every file below it. A
shared, non-configurable denial applies to HTTP reads and agent read/list/staged
write handlers. Permission rules cannot override it.

At minimum it denies:

- `.env`, `.env.*`, `.env-*`, `.envrc*`;
- `.git`, `.hg`, `.svn`, `.ssh`, `.aws`, `.azure`, `.gcloud`, `.kube`,
  `.docker`, `.terraform`, `.pulumi`, `.secrets`, `secrets`, credential and
  token directories, `.apt`, and `.model-hub`;
- known credential/token/key files and names that clearly identify secrets;
- private key/certificate/state suffixes;
- alternate data streams, traversal, absolute/non-portable paths, symlink or
  reparse traversal, and paths outside the exact root.

Directory listings omit denied children and are bounded. File reads reject
denied, non-regular, oversized, or non-UTF-8 files rather than replacing bytes.
This policy is narrower than the Docker snapshot's performance/size exclusions;
normal dependency or build-source paths are not called secrets solely because
the snapshot excludes them.

## Local API

Plan 5 adds:

- `GET /api/workspaces/<workspace_id>/projects`
- `POST /api/workspaces/<workspace_id>/projects`
- `GET /api/projects/<project_id>`
- `GET /api/sessions?workspace=<id>&project_id=<id|unassigned>`

Existing agent chat and sandbox endpoints accept `project_id`. When present,
the server derives the root and rejects `cwd`. Project registration and
project-root disclosure are loopback-only and retain the existing Host/Origin
guards. Responses never disclose secrets or inspect file contents during
registration.

## Workbench UX

The left rail becomes `Workspace -> Project -> Threads`:

- workspace choice is Workbench UI state and no longer mutates the process-wide
  `AppConfig.workspace`;
- project choice is a registered-project selector with an inline local
  registration form for name, optional description, and root;
- the canonical root is displayed read-only; there is no global root text box
  and no project-root localStorage key;
- threads are filtered by the selected project and labelled `Threads`;
- legacy threads are reachable through an explicit `Legacy / Unassigned` view
  and cannot run until a real project is selected in a new thread;
- all Workbench modes require a selected project for new sends;
- switching workspace or project aborts/invalidate any active stream and clears
  approval, staged-change, sandbox, session-load, and stale-request state before
  loading the new context.

Sandbox status uses the project ID. Ask mode remains on its existing chat path
in this slice; durable Ask persistence is deferred rather than inventing a
second identity path during this migration.

## Migration and compatibility

Schema setup is additive and idempotent. Existing sessions, messages, events,
and staged rows survive with `sessions.project_id = NULL`. Foreign keys are
enabled for every connection. Expected duplicate-column/index conditions are
handled through schema introspection; unrelated migration errors are not
swallowed.

The current non-Workbench APIs may continue to create legacy unassigned
sessions. No migration infers a project from old staged rows because one legacy
thread may contain multiple roots.

## Acceptance boundary

- Project registration validates workspace, canonical path, reparse state,
  duplicate canonical key, and duplicate filesystem identity without changing
  the selected directory.
- Concurrent duplicate registration yields one record and one conflict.
- Bound sessions derive workspace/root and cannot move through create, save, or
  resume calls; legacy sessions are never auto-bound.
- Project-bound Plan/Explore/Build and sandbox checks use only the registered,
  freshly revalidated root; mismatch/drift fails before provider construction.
- Provider configuration resolution starts at the bound root.
- Sensitive paths fail closed for direct tools, staged overlays, and HTTP reads;
  configuration cannot grant access.
- Corrupted cross-project staged rows cannot be listed, applied, rejected,
  reverted, or included in a sandbox snapshot.
- Workspace deletion with registered projects returns `409` and leaves both
  metadata and external directories untouched.
- Browser QA proves Workspace -> Project -> Threads filtering and stale-context
  clearing; no editable/global root remains.
- Plan 4 tests, full Python/web gates, Host/Origin guards, and the no-`lac_pro`
  import boundary remain green.
- `lac-pro` remains untouched and remote-free. No push, publish, tag, upload, or
  deployment occurs.

## Deferred

- Artifact browser/export and container-output extraction.
- Project memory/RAG.
- Role-specific agents and delegation.
- Full run/activity timeline and event pagination.
- Project rename, relocation, archive, delete, or cross-workspace move.
- Multi-user tenancy claims; workspaces are local operator organization only.

## Rejected alternatives

- Keeping a mutable global root in browser storage.
- Storing only a path on each session without a durable project identity.
- Inferring project ownership for legacy multi-root sessions.
- Allowing callers to provide both a project ID and raw `cwd`.
- Treating a registered directory as blanket permission to read secrets.
- Building artifact history or memory before establishing stable project/thread
  identity.
