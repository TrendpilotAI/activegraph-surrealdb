// OpenCode 2 adapter for Arize's Apache-2.0 coding-harness-tracing reconciler.
// The upstream V1 plugin is not compatible with OpenCode 2.
import { Plugin } from "@opencode/plugin"
import { DateTime } from "effect"
import { spawn } from "node:child_process"
import { homedir } from "node:os"
import { join } from "node:path"

function millis(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value
  if (DateTime.isDateTime(value)) return DateTime.toEpochMillis(value)
  if (value instanceof Date) return value.getTime()
  return undefined
}

export function legacyMessages(messages: readonly any[], sessionID: string) {
  return messages.flatMap((message) => {
    if (message.type !== "user" && message.type !== "assistant") return []
    const info = {
      id: message.id, sessionID, role: message.type,
      modelID: message.model?.id, providerID: message.model?.providerID,
      cost: message.cost, tokens: message.tokens,
      time: { created: millis(message.time?.created), completed: millis(message.time?.completed) },
    }
    const content = message.type === "user"
      ? [{ type: "text", text: message.text ?? "" }] : message.content ?? []
    const parts = content.map((part: any, index: number) => {
      const base = { id: `${message.id}:${index}`, sessionID, messageID: message.id }
      if (part.type !== "tool") return { ...base, type: part.type, text: part.text ?? "" }
      const state = part.state ?? {}
      return {
        ...base, type: "tool", tool: part.name, callID: part.id,
        state: {
          ...state,
          status: state.status === "streaming" ? "pending" : state.status,
          output: (state.content ?? []).filter((p: any) => p.type === "text")
            .map((p: any) => p.text).join("\n"),
          error: state.error ? JSON.stringify(state.error) : undefined,
          time: { start: millis(part.time?.ran ?? part.time?.created), end: millis(part.time?.completed) },
        },
      }
    })
    return [{ info, parts }]
  })
}

function forward(payload: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(join(homedir(), ".arize/harness/venv/bin/python"),
      ["/opt/mercor-tools/arize-reconcile.py"], {
      stdio: ["pipe", "ignore", "ignore"],
    })
    const timer = setTimeout(() => { child.kill(); reject(new Error("reconciler timed out")) }, 30_000)
    child.once("error", (error) => { clearTimeout(timer); reject(error) })
    child.once("exit", (code) => {
      clearTimeout(timer)
      code === 0 ? resolve() : reject(new Error(`reconciler exited ${code}`))
    })
    child.stdin.on("error", () => {})
    child.stdin.end(JSON.stringify(payload))
  })
}

export default Plugin.define({
  id: "mercor.arize-v2",
  async setup(ctx) {
    if (process.env.ARIZE_TRACE_ENABLED === "false") return
    const controller = new AbortController()
    async function snapshot(sessionID: string, close: boolean) {
      const info = await ctx.session.get({ sessionID: sessionID as any })
      if (info.parentID) return // Children are exported with their root.
      const messages = legacyMessages(await ctx.session.context({ sessionID: sessionID as any }), sessionID)
      const childSessions: any[] = []
      const seen = new Set([sessionID])
      async function children(parent: string, entries: ReturnType<typeof legacyMessages>) {
        for (const message of entries) for (const part of message.parts) {
          const metadata = (part as any).state?.metadata
          const id = metadata?.sessionId ?? metadata?.sessionID
          if ((part as any).type !== "tool" || !id || seen.has(id)) continue
          seen.add(id)
          const child = await ctx.session.get({ sessionID: id })
          if (child.parentID !== parent) continue
          const childMessages = legacyMessages(await ctx.session.context({ sessionID: id }), id)
          childSessions.push({ sessionID: id, parentSessionID: parent,
            parentCallID: (part as any).callID, info: { ...child,
              time: { created: millis(child.time.created), updated: millis(child.time.updated) } },
            messages: childMessages })
          await children(id, childMessages)
        }
      }
      await children(sessionID, messages)
      await forward({ type: close ? "close" : "reconcile", sessionID, messages, childSessions })
    }
    // One consumer serializes snapshots; the upstream reconciler handles deduplication.
    const consumer = (async () => {
      for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
        const close = ["session.execution.succeeded", "session.execution.failed",
          "session.execution.interrupted"].includes(event.type)
        if (!close && !["session.step.ended", "session.step.failed"].includes(event.type)) continue
        const sessionID = (event.data as any).sessionID
        if (!sessionID) continue
        try { await snapshot(sessionID, close) }
        catch { console.error("[mercor.arize-v2] trace export failed; inspect the Arize reconciler log") }
      }
    })().catch(() => {
      if (!controller.signal.aborted) console.error("[mercor.arize-v2] event subscription stopped unexpectedly")
    })
    return async () => { controller.abort(); await consumer }
  },
})
