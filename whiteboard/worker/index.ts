import handler from "vinext/server/fetch-handler";
import { runWithConnectorBinding } from "../lib/connector-context";
import type { ConnectorBinding } from "../lib/connector-contract.mjs";
import { reviewBoard, ReviewError, validateReviewRequest } from "../lib/review";

const responseHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
const recentRequests = new Map<string, { count: number; reset: number }>();

async function reviewRoute(request: Request, env: Cloudflare.Env) {
  if (request.method !== "POST") return Response.json({ error: "Use POST to check a board." }, { status: 405, headers: { ...responseHeaders, Allow: "POST" } });
  const origin = request.headers.get("origin");
  if (origin && origin !== new URL(request.url).origin) return Response.json({ error: "Please check from your whiteboard." }, { status: 403, headers: responseHeaders });
  if (!request.headers.get("content-type")?.includes("application/json")) return Response.json({ error: "Send your board as JSON." }, { status: 415, headers: responseHeaders });
  const identity = request.headers.get("oai-authenticated-user-id") || request.headers.get("cf-connecting-ip") || "local";
  const now = Date.now();
  for (const [key, item] of recentRequests) if (item.reset < now) recentRequests.delete(key);
  const limit = recentRequests.get(identity) || { count: 0, reset: now + 60_000 };
  if (limit.count >= 30) return Response.json({ error: "Take a short pause, then check again. Your board is saved." }, { status: 429, headers: { ...responseHeaders, "Retry-After": String(Math.ceil((limit.reset - now) / 1000)) } });
  limit.count++; if (recentRequests.size < 1000) recentRequests.set(identity, limit);
  try {
    // Bound the actual streamed body, including when Content-Length is absent.
    const reader = request.body?.getReader();
    if (!reader) throw new ReviewError("Add some working to your board first.", 400, "INVALID_REQUEST");
    const chunks: Uint8Array[] = []; let length = 0;
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      length += value.byteLength;
      if (length > 6 * 1024 * 1024) { await reader.cancel(); throw new ReviewError("This board is too large. Try a smaller one.", 413, "IMAGE_TOO_LARGE"); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    let body: unknown;
    try { body = JSON.parse(new TextDecoder().decode(bytes)); } catch { throw new ReviewError("The board could not be read. Please try again.", 400, "INVALID_REQUEST"); }
    const input = validateReviewRequest(body);
    const result = await reviewBoard(input, { apiKey: env.GEMINI_API_KEY, model: env.GEMINI_MODEL, signal: request.signal });
    return Response.json(result, { headers: responseHeaders });
  } catch (error) {
    if (error instanceof ReviewError) return Response.json({ error: error.message, code: error.code }, { status: error.status, headers: { ...responseHeaders, ...(error.retryAfter ? { "Retry-After": String(error.retryAfter) } : {}) } });
    return Response.json({ error: "The coach could not connect. Please try again." }, { status: 502, headers: responseHeaders });
  }
}

const worker = {
  fetch(request: Request, env: Cloudflare.Env, ctx: ExecutionContext<{ CONNECTORS?: ConnectorBinding }>) {
    const path = new URL(request.url).pathname;
    if (path === "/api/review") return reviewRoute(request, env);
    if (path === "/api/status") return Response.json({ configured: !!env.GEMINI_API_KEY }, { headers: responseHeaders });
    let binding = ctx.props?.CONNECTORS;
    // Local preview emulates the same request-scoped capability. This branch and
    // the auxiliary service binding are absent from production builds.
    if (import.meta.env.DEV && !binding && env.CONNECTORS) {
      const preview = env.CONNECTORS;
      const expiresAt = Date.now() + 60_000;
      binding = {
        async getContext() {
          if (Date.now() >= expiresAt) return { status: "request_context_expired" };
          return preview.getContext?.() ?? { status: "binding_unavailable" };
        },
        async invoke(connectorId, actionName, args) {
          if (Date.now() >= expiresAt) {
            return { status: "request_context_expired", message: "This request has expired. Please try again." };
          }
          return preview.invoke(connectorId, actionName, args);
        },
      };
    }
    return runWithConnectorBinding(binding, () => handler.fetch(request, env, ctx));
  },
};

export default worker;
