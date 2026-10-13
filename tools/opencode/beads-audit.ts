/**
 * beads-audit — OpenCode V2 plugin that streams full session transcripts into
 * beads (bd) for durable, append-only capture.
 *
 * Capture map:
 *   user turn          -> session.hook("prompt")          -> kind "user_turn"
 *   tool call          -> tool.hook("execute.before/after") -> kind "tool_call"
 *   assistant response -> event stream (message.updated)  -> kind "llm_call"
 *   session/issue link -> bd provenance --ref-kind transcript (idempotent)
 *
 * Storage: entries are appended by `bd audit record --stdin` to
 * .beads/interactions.jsonl inside the repository (on the persistent /work
 * virtiofs mount of the Modal sandbox). Issue/dependency/provenance state
 * lives in the Dolt DB and syncs to refs/dolt/data on the git remote.
 *
 * Full-fidelity transcripts remain in OpenCode's own storage (opencode.db,
 * streamed by session-backup-agent); this sidecar mirrors turns in a
 * queryable, dataset-ready form and binds them to beads via provenance.
 *
 * No external imports: OpenCode V2 reads `id` and `setup()` from the default
 * export, so this file loads without the @opencode/plugin package.
 */
import { spawn } from "node:child_process"

const BD = process.env.BEADS_BD_PATH || `${process.env.HOME}/.local/bin/bd`
const ACTOR = process.env.BEADS_ACTOR || "opencode"
const SOURCE = "opencode-plugin"
const TOOL_CLIP = 20_000 // chars kept per tool input/result
const TEXT_CLIP = 200_000 // safety cap for user/assistant text
const PROVENANCE_THROTTLE_MS = 10 * 60 * 1000

function clip(value: unknown, max: number): string {
  if (value === undefined || value === null) return ""
  const text = typeof value === "string" ? value : JSON.stringify(value)
  if (text === undefined) return ""
  return text.length > max ? `${text.slice(0, max)}…[truncated ${text.length - max} chars]` : text
}

export default {
  id: "beads-audit",
  async setup(ctx: any) {
    const cwd: string = ctx?.location?.directory || process.cwd()
    const env = { ...process.env, BD_DISABLE_METRICS: "1", BEADS_ACTOR: ACTOR }

    // Serialize bd invocations so concurrent events never interleave writes.
    let queue: Promise<void> = Promise.resolve()
    const run = (args: string[], stdin?: string): Promise<string> => {
      const task = new Promise<string>((resolve) => {
        try {
          const child = spawn(BD, args, { cwd, env, stdio: ["pipe", "pipe", "ignore"] })
          let out = ""
          child.stdout?.on("data", (d: Buffer) => {
            out += d.toString()
          })
          child.on("error", () => resolve(""))
          child.on("close", () => resolve(out.trim()))
          if (stdin !== undefined) {
            child.stdin?.end(stdin)
          } else {
            child.stdin?.end()
          }
        } catch {
          resolve("")
        }
      })
      queue = queue.then(() => task).catch(() => {})
      return task
    }

    const record = (entry: Record<string, unknown>) => {
      void run(
        ["audit", "record", "--stdin", "--quiet"],
        JSON.stringify({ actor: ACTOR, created_at: new Date().toISOString(), ...entry }),
      ).catch(() => {})
    }

    record({
      kind: "plugin_start",
      extra: {
        directory: cwd,
        app_version: ctx?.app?.version,
        project: ctx?.location?.project?.id,
      },
    })

    // --- Provenance: bind this session's transcript to claimed beads --------
    const lastProvenance = new Map<string, number>()
    const bindProvenance = (sessionID: string) => {
      const now = Date.now()
      const last = lastProvenance.get(sessionID) ?? 0
      if (now - last < PROVENANCE_THROTTLE_MS) return
      lastProvenance.set(sessionID, now)
      void (async () => {
        try {
          const out = await run(["list", "--status", "in_progress", "--json"])
          if (!out) return
          const issues = JSON.parse(out)
          const ids: string[] = Array.isArray(issues) ? issues.map((i: any) => i.id) : []
          for (const id of ids) {
            // Idempotent: deterministic id from source:issue:kind:ref.
            await run([
              "provenance", "record",
              "--issue", id,
              "--kind", "used",
              "--source", SOURCE,
              "--ref", sessionID,
              "--ref-kind", "transcript",
              "--actor", ACTOR,
            ])
          }
        } catch {
          /* best effort */
        }
      })()
    }

    // --- User turns ----------------------------------------------------------
    await ctx.session.hook("prompt", (event: any) => {
      try {
        const sessionID = event?.sessionID ?? event?.prompt?.sessionID ?? ""
        record({
          kind: "user_turn",
          prompt: clip(event?.prompt?.text, TEXT_CLIP),
          extra: {
            session_id: sessionID,
            delivery: event?.delivery,
            files: (event?.prompt?.files ?? []).map((f: any) => f?.uri),
          },
        })
        if (sessionID) bindProvenance(sessionID)
      } catch {
        /* best effort */
      }
    })

    // --- Tool calls -----------------------------------------------------------
    const toolStarts = new Map<string, number>()
    await ctx.tool.hook("execute.before", (event: any) => {
      try {
        const key = event?.callID ?? event?.id ?? `${event?.sessionID}:${event?.tool}`
        toolStarts.set(key, Date.now())
      } catch {
        /* best effort */
      }
    })
    await ctx.tool.hook("execute.after", (event: any) => {
      try {
        const key = event?.callID ?? event?.id ?? `${event?.sessionID}:${event?.tool}`
        const started = toolStarts.get(key)
        toolStarts.delete(key)
        const errored = event?.status === "error"
        record({
          kind: "tool_call",
          tool_name: String(event?.tool ?? "unknown"),
          error: errored ? clip(event?.error?.message ?? event?.error, 4000) : undefined,
          extra: {
            session_id: event?.sessionID,
            message_id: event?.messageID,
            call_id: event?.callID ?? event?.id,
            status: event?.status,
            duration_ms: started ? Date.now() - started : undefined,
            input: clip(event?.input, TOOL_CLIP),
            result: errored ? undefined : clip(event?.result, TOOL_CLIP),
          },
        })
      } catch {
        /* best effort */
      }
    })

    // --- Assistant responses (event stream) -----------------------------------
    const seenAssistant = new Set<string>()
    const controller = new AbortController()
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
          try {
            const type: string = (event as any)?.type ?? ""
            if (type !== "message.updated" && type !== "message.part.updated") continue
            const info = (event as any)?.properties?.info ?? (event as any)?.info
            if (!info || info.role !== "assistant") continue
            const sessionID: string = info.sessionID ?? (event as any)?.properties?.sessionID ?? ""
            const messageID: string = info.id ?? ""
            if (!messageID || seenAssistant.has(messageID)) continue
            if (!info.time?.completed) continue
            seenAssistant.add(messageID)
            // Best-effort text extraction from the session context.
            let text = ""
            try {
              const messages: any[] = await ctx.session.context({ sessionID })
              for (const m of messages ?? []) {
                const mi = m?.info ?? m
                if (mi?.id !== messageID) continue
                for (const part of m?.parts ?? mi?.parts ?? []) {
                  if (part?.type === "text" && typeof part.text === "string") text += part.text
                }
              }
            } catch {
              /* metadata-only entry is still valuable */
            }
            record({
              kind: "llm_call",
              model: info.modelID
                ? `${info.providerID ?? "?"}/${info.modelID}${info.variant ? `#${info.variant}` : ""}`
                : undefined,
              response: clip(text, TEXT_CLIP),
              extra: {
                session_id: sessionID,
                message_id: messageID,
                agent: info.agent,
                tokens: info.tokens ?? info.usage,
                cost: info.cost,
                time: info.time,
              },
            })
          } catch {
            /* keep the stream alive */
          }
        }
      } catch {
        /* stream aborted on unload */
      }
    })()

    return () => controller.abort()
  },
}
