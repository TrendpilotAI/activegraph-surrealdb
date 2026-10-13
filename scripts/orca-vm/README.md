# Mercor Modal environment

This recipe is prepared for Modal workspace `mercor-work`, environment
`Development`, and repository `TrendpilotAI/activegraph-surrealdb`.
It uses 2 CPUs, 4096 MiB RAM, and a 24-hour maximum sandbox lifetime.
The lifetime is set when a VM is created; changing this setting does not
extend the lifetime of an already running VM.
Every Sandbox creation explicitly requests Modal's `runtime="vm"`; it never
falls back to gVisor. Images are compatible across runtimes.

## Prerequisites

- Authenticate the Modal CLI in the `mercor-work` workspace.
- Create Modal Secret `mercor-arize` in `Development`, containing
  `ARIZE_API_KEY` and `ARIZE_SPACE_ID`.
- Create Modal Secret `mercor-gateway` there, containing `DEVTRAFFIC_ACCESS_ID`.
- Keep secret values in Modal; do not put them in this repository.

OpenCode 2 is pinned to `2.0.27`. The base image carries Orca `1.4.224` program
files from the installed application, checked against its shipped hashes, and
its required Node runtime. No desktop settings, pairing identities, shell
history, GitHub token, or personal agent directories are uploaded.

## Setup sequence

Run these from the primary checkout on the Mac:

```bash
./scripts/orca-vm/modal-prepare.sh
orca vm recipe doctor mercor-modal --repo-path "$PWD" --json
```

`prepare` is local and does not provision compute. It stages program files under
the ignored `.local/` directory and records the inspected Mercor installer's
SHA-256. Set `ORCA_TEMPLATE_PATH` or `MERCOR_INIT_PATH` if their locations differ.
The launcher uses the Python interpreter from the installed Modal CLI's shebang;
set `MODAL_PYTHON` explicitly for another installation method.

After approval of the paid base-image build:

```bash
./scripts/orca-vm/modal-build.sh
```

This builds the image, snapshots a temporary sandbox, and terminates it. No
secrets are attached and Orca is never started in the reusable base snapshot.
Filesystem snapshot retention is 30 days. Rebuild before it expires.

After approval of the paid live self-test:

```bash
orca vm recipe doctor mercor-modal --repo-path "$PWD" --provision --json
```

The live test starts a sandbox, installs project dependencies in an isolated
Python environment, installs Arize tracing, runs the inspected Mercor installer,
converts its provider config to OpenCode 2, starts Orca, and terminates the
sandbox. The static doctor does not prove that this remote flow works.

The recipe appears in Orca's **Run on** picker only when `orca.yaml` and the
scripts exist in the primary checkout. Pick **Mercor Modal (Development)** for
a Mercor workspace. The recipe does not enforce all future desktop launches;
confirm the workspace execution host before starting an agent. No local
fallback is implemented by these scripts.
The setup hook rejects local workspace setup, and `wait-for-setup` gates normal
new-workspace agent startup on that hook. Direct shell launches or explicitly
skipped hooks are outside that guard.

## Contents and persistence

The main checkout is `/work/mercor-work/activegraph-surrealdb`. Each recipe
instance gets its own Modal Volume v2. Existing working directories are never
reset or automatically pulled. Suspend syncs the volume and terminates compute;
resume starts a new sandbox with the same volume and persistent Orca identity.
Destroy also terminates compute but retains project files. Volume deletion is a
separate, explicitly requested operation; retained volumes incur storage costs.
`/home/mercor` points to `/work/.mercor-home`. OpenCode history, Orca metadata,
worktrees, and user-installed plugins therefore survive VM replacement.

Exit snapshots are explicitly enabled. The trusted Modal app
`mercor-session-backup-scheduler` also takes filesystem snapshots every fifteen
minutes, retaining each for seven days. Filesystem snapshots exclude mounted
Volumes, so a consistent checkpoint archive is staged at
`/tmp/mercor-session-checkpoint/checkpoint.tar.gz` before each scheduled snapshot.
The snapshot must be restored only for its original VM, with that VM's Volume;
it contains instance-specific runtime state and is never a reusable base image.

Each VM writes consistent SQLite history checkpoints, readable JSON/Markdown
transcripts, and logs to `/work/.mercor-backups/checkpoints` every five minutes.
The fifteen-minute cloud checkpoint also adds Git bundles and worktree file
copies. It retains the newest 288 archives. SQLite backups are transactionally
consistent; worktree source files are copied while agents run and may reflect
concurrent edits. Slow Git bundling is recorded as a warning and does not block
the transcript backup. Dependency caches and credential configuration are
excluded. VM startup and replacement use a session checkpoint, keeping source
history compression out of the startup path.

The Mac's LaunchAgent `ai.trendpilot.mercor-session-backups` streams session
record changes and log bytes through the authenticated Modal API every five
seconds to `scripts/orca-vm/.local/session-backups`. It resumes after connection
interruptions and login; while the Mac is asleep or offline, VM checkpoints and
the Modal snapshot scheduler continue. The VM receives no Mac filesystem mount
or Modal management credentials. The receiver writes an append-only change
journal and current per-session JSON/Markdown transcripts. Credential tables
are excluded; conversation content and tool outputs can still contain sensitive
user-entered information, so these files stay local and excluded from Git.

Inspect receiver health with `python protect-sessions.py status` using the
Python interpreter that has the Modal SDK. Recovery metadata is also stored
in Modal Dict `mercor-session-snapshot-index` in `Development`.

## Docker-enabled VM image

`build-docker-image.py` derives a clean image from `dockerSourceSnapshotId`,
installs pinned packages from Docker's official Debian Bookworm repository,
and snapshots it before Orca or runtime credentials are used.
`validate-docker-image.py` starts a temporary VM without secrets or repository
volumes and checks the daemon, user socket access, and container execution.
Only successful validation updates the configured runtime image.

Docker runs as the VM entrypoint, with an execution readiness probe. Its UNIX
socket is accessible to `mercor` through the Docker group; Docker access grants
root-equivalent control inside this VM. It does not grant access to the Mac.
The daemon uses `overlay2`, a Google registry mirror, and `10.200/10.201` bridge
ranges to avoid Modal's control network. No Docker TCP endpoint is exposed.
Docker images and container state live in the VM's `/var/lib/docker`; recovery
snapshots capture them, but starting from the clean base recreates that state.

Orca environment IDs are preserved when pairing endpoints change.
`reconnect-all-vms.py` refreshes both saved pairings without removing the stable
IDs used by existing project views. `update-allowlist.py` applies the explicitly
observed blocked domains and resolves current peer VM tunnel hostnames.

Five root-owned reference clones live under `/opt/references`: `activegraph`,
`activegraph-packs`, `surrealdb`, `surrealdb.py`, and `surrealist`. Their commit
IDs are recorded in `modal-state.json`. They are registered as OpenCode local
references. They are source references, not built services. The project installs
its declared Python dependencies (`activegraph==1.10.0`, `surrealdb==2.0.0`).
The image does not include a Docker daemon or run SurrealDB automatically.
Its Docker-based qualification tests require additional isolated infrastructure.

## Tracing and gateway

Arize AX is a hosted service. The sandbox installs its coding-harness-tracing
package at the recorded commit and uses `arize-v2.ts` to translate native
OpenCode 2 session snapshots for the upstream Python reconciler. The upstream
Arize V1 plugin is replaced only when it has the expected Arize marker.
The adapter must be validated with an actual session and a received AX trace.

Content logging is off by default (`logContent: false`). Set it to `true` only
after the user selects prompt/response and tool content logging. Project name:
`activegraph-surrealdb-opencode2`.

Mercor's installer is run after tracing setup, in a temporary non-Git directory,
using the injected access ID on stdin. Its returned model list is used in a V2
provider config. The Mercor key is stored as
`/work/mercor-work/.devtraffic/key` with mode 600. It is visible to commands
running as the same `mercor` user. The gateway URL is unchanged.

## Isolation limits

Workers and the Orca server run as UID 1000 without sudo. No host filesystem or
Docker socket is mounted. Credentials are injected only into running sandboxes,
not into the reusable snapshot. The TLS tunnel exposes Orca's authenticated
runtime protocol. Pairing results contain credentials and should not be committed.

Runtime HTTPS egress is restricted to `outboundDomains` in the state file. Modal
documents unrestricted DNS and domain-fronting limitations for this filter. The
OpenCode provider policy restricts normal model selection to Mercor but is not
tamper-proof against code running as the same user. The Mac is not connected by
VPN or filesystem mounts. Add necessary dependency hosts deliberately.

## Validation still required

- Image build and native Orca startup on Modal.
- Successful Mercor authentication and an actual model request.
- Arize plugin loaded and a real trace received by AX.
- Project unit tests inside the sandbox.
- Suspend/resume with an uncommitted file preserved.
- Two-instance pairing uniqueness before enabling parallel workers.
