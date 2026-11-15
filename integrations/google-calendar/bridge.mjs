/** Private gateway to nspady/google-calendar-mcp's unmodified tool registry.
 *
 * Uses the upstream MCP server definitions/handlers with an authenticated client
 * supplied in memory. The upstream interactive login and token-file manager are
 * deliberately unnecessary: TrojanClaw already owns OAuth and encrypted storage.
 */
import { pathToFileURL } from "node:url";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { OAuth2Client } from "google-auth-library";
import { ToolRegistry } from "./.generated/registry.mjs";

const READ_TOOLS = ["list-calendars", "get-event", "get-freebusy"];

export class BridgeError extends Error {
  constructor(code, status = 502) { super(code); this.code = code; this.status = status; }
}

function validateCreate(request) {
  const event = request.event;
  const proposal = request.approved_proposal;
  if (!proposal || !event || !/^[0-9a-f-]{36}$/.test(proposal)
      || event.id !== `tc${proposal.replaceAll("-", "")}`
      || event.extendedProperties?.private?.trojanclaw_proposal !== proposal) {
    throw new BridgeError("calendar_approval_required", 403);
  }
  const allowed = new Set(["id", "summary", "description", "location", "start", "end", "reminders", "extendedProperties"]);
  if (Object.keys(event).some((key) => !allowed.has(key))
      || JSON.stringify(event.reminders) !== JSON.stringify({ useDefault: false })
      || !event.start?.dateTime || !event.end?.dateTime
      || !event.start?.timeZone || event.start.timeZone !== event.end?.timeZone) {
    throw new BridgeError("calendar_invalid_draft", 400);
  }
}

function normalizeResult(result, operation) {
  let data = result.structuredContent;
  const text = (result.content || []).filter((part) => part.type === "text").map((part) => part.text).join("\n");
  if (!data) {
    try { data = JSON.parse(text); } catch { data = null; }
  }
  if (result.isError || data?.error || data?.success === false) {
    if (operation === "get-event" && /Event with ID .*not found in calendar/i.test(text)) return { status: 404, data: {} };
    if (operation === "create-event" && /Event ID .*already exists/i.test(text)) return { status: 409, data: {} };
    const message = `${text} ${JSON.stringify(data || {})}`;
    if (/SERVICE_DISABLED|accessNotConfigured|has not been used|API.*disabled/i.test(message)) throw new BridgeError("calendar_api_disabled", 409);
    if (/insufficient.*permission|insufficient.*scope|ACCESS_TOKEN_SCOPE_INSUFFICIENT/i.test(message)) throw new BridgeError("calendar_scope_missing", 409);
    if (/invalid_grant|invalid.token|unauthenticated|unauthorized/i.test(message)) throw new BridgeError("calendar_reconnect", 409);
    throw new BridgeError("calendar_mcp_access");
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) throw new BridgeError("calendar_mcp_response");
  return { status: 200, data };
}

function dateInput(value) {
  // The upstream ISO validator requires whole seconds. Removing a zero-only
  // fractional part preserves the reviewed instant; nonzero fractions fail validation.
  return value.replace(/\.0+(?=Z|[+-])/, "");
}

export async function execute(request, options = {}) {
  const action = request.action;
  if (!["verify", "get", "freebusy", "create", "tools"].includes(action)) throw new BridgeError("calendar_operation_forbidden", 403);
  if (action === "create") validateCreate(request);
  if (process.env.NODE_ENV !== "test" && process.env.GOOGLE_CALENDAR_API_BASE_URL) throw new BridgeError("calendar_api_override_forbidden", 503);
  if (!options.accounts && (typeof request.access_token !== "string" || !request.access_token)) throw new BridgeError("calendar_reconnect", 409);
  const auth = new OAuth2Client();
  auth.setCredentials({ access_token: request.access_token });
  const accounts = options.accounts || new Map([["trojanclaw", auth]]);
  const enabled = action === "create" ? [...READ_TOOLS, "create-event"] : READ_TOOLS;
  const server = new McpServer({ name: "trojanclaw-nspady-calendar", version: "2.8.1" });
  const client = new Client({ name: "trojanclaw-calendar-approval", version: "1.0.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  try {
    await ToolRegistry.registerAll(server, (handler, args) => options.executeHandler
      ? options.executeHandler(handler, args, accounts)
      : handler.runTool(args, accounts), { transport: { type: "stdio" }, enabledTools: enabled });
    await server.connect(serverTransport);
    await client.connect(clientTransport);
    const call = async (name, args) => normalizeResult(await client.callTool({ name, arguments: args }), name);
    const calendarId = request.calendar_id || "primary";
    if (action === "tools") return { status: 200, data: await client.listTools() };
    if (action === "verify") {
      const result = await call("list-calendars", {});
      const primary = result.data.calendars?.find((calendar) => calendar.primary === true);
      if (!primary?.id) throw new BridgeError("calendar_primary_missing", 409);
      return { status: 200, data: { id: primary.id, summary: primary.summary || primary.id } };
    }
    if (action === "get") {
      const result = await call("get-event", { calendarId, eventId: request.event_id,
        fields: ["id", "summary", "start", "end", "status", "htmlLink", "extendedProperties"] });
      return result.status === 404 ? result : { status: 200, data: result.data.event };
    }
    if (action === "freebusy") {
      const result = await call("get-freebusy", { calendars: [{ id: calendarId }], timeMin: dateInput(request.start), timeMax: dateInput(request.end), timeZone: "UTC" });
      const availability = result.data.calendars?.[calendarId];
      if (!availability || availability.errors?.length || !Array.isArray(availability.busy)) throw new BridgeError("calendar_availability_failed", 502);
      return result;
    }
    const event = request.event;
    const result = await call("create-event", {
      calendarId, eventId: event.id, summary: event.summary,
      description: event.description, location: event.location,
      start: dateInput(event.start.dateTime), end: dateInput(event.end.dateTime),
      timeZone: event.start.timeZone, reminders: { useDefault: false },
      extendedProperties: event.extendedProperties, sendUpdates: "none",
    });
    return result.status === 409 ? result : { status: 200, data: result.data.event };
  } finally {
    await Promise.allSettled([client.close(), server.close()]);
  }
}

async function main() {
  try {
    let input = "";
    process.stdin.setEncoding("utf8");
    for await (const chunk of process.stdin) {
      input += chunk;
      if (input.length > 64000) throw new BridgeError("calendar_invalid_request", 400);
    }
    const result = await execute(JSON.parse(input));
    process.stdout.write(JSON.stringify({ ok: true, ...result }));
  } catch (error) {
    // Never print raw upstream exceptions, which can contain request headers.
    process.stdout.write(JSON.stringify({ ok: false, code: error instanceof BridgeError ? error.code : "calendar_mcp_access", status: error instanceof BridgeError ? error.status : 502 }));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
