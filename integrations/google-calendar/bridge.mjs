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

const READ_TOOLS = ["list-calendars", "list-events", "get-event", "get-freebusy"];

export class BridgeError extends Error {
  constructor(code, status = 502) { super(code); this.code = code; this.status = status; }
}

function validateWrite(request) {
  const event = request.event;
  const proposal = request.approved_proposal;
  if (!proposal || !event || !/^[0-9a-f-]{36}$/.test(proposal)
      || (request.action === "create" && event.id !== `tc${proposal.replaceAll("-", "")}`)
      || (request.action === "update" && (!/^[a-zA-Z0-9_-]{1,1024}$/.test(request.event_id || "") || typeof request.expected_etag !== "string" || !request.expected_etag))
      || event.extendedProperties?.private?.trojanclaw_proposal !== proposal) {
    throw new BridgeError("calendar_approval_required", 403);
  }
  const allowed = new Set(["id", "summary", "description", "location", "start", "end", "reminders", "recurrence", "extendedProperties"]);
  const reminders = event.reminders;
  const validReminders = reminders === undefined ? request.action === "update" : reminders && reminders.useDefault === false
    && Object.keys(reminders).every(key => ["useDefault", "overrides"].includes(key))
    && (reminders.overrides === undefined || (Array.isArray(reminders.overrides) && reminders.overrides.length === 1
      && reminders.overrides[0].method === "popup" && Number.isInteger(reminders.overrides[0].minutes)
      && reminders.overrides[0].minutes >= 0 && reminders.overrides[0].minutes <= 40320
      && Object.keys(reminders.overrides[0]).every(key => ["method", "minutes"].includes(key))));
  const validRecurrence = event.recurrence === undefined || (Array.isArray(event.recurrence) && event.recurrence.length <= 1
    && event.recurrence.every(rule => {
      if (!/^RRULE:FREQ=(DAILY|WEEKLY|MONTHLY);INTERVAL=\d+(;BYDAY=(MO|TU|WE|TH|FR|SA|SU)(,(MO|TU|WE|TH|FR|SA|SU))*)?;(COUNT=\d+|UNTIL=\d{8}T\d{6}Z)$/.test(rule)) return false;
      const interval = Number(rule.match(/INTERVAL=(\d+)/)?.[1]);
      const count = rule.match(/COUNT=(\d+)/)?.[1];
      return interval >= 1 && interval <= 12 && (!count || (Number(count) >= 1 && Number(count) <= 52));
    }));
  if (Object.keys(event).some((key) => !allowed.has(key))
      || !validReminders || !validRecurrence
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
    if (/precondition|conditionNotMet|412/i.test(message)) throw new BridgeError("calendar_event_changed", 409);
    if (/missing time zone definition|timezone.*required|time zone.*required/i.test(message)) throw new BridgeError("calendar_timezone_required", 409);
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

function writeTimeInput(value, timezone) {
  const normalized = dateInput(value);
  const match = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:Z|[+-]\d\d:\d\d)$/.exec(normalized);
  if (!match) throw new BridgeError("calendar_invalid_draft", 400);
  let local;
  try {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(new Date(normalized));
    const part = type => parts.find(value => value.type === type).value;
    local = `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}:${part("second")}`;
  } catch {
    throw new BridgeError("calendar_invalid_draft", 400);
  }
  if (local !== match[1]) throw new BridgeError("calendar_invalid_draft", 400);
  // Upstream createTimeObject drops timeZone for offset-bearing timestamps.
  // Give it the validated wall time plus IANA zone so Google receives both.
  // Read/free-busy inputs keep their offsets; only write arguments need this.
  return local;
}

export async function execute(request, options = {}) {
  const action = request.action;
  if (!["verify", "list", "get", "freebusy", "create", "update", "tools"].includes(action)) throw new BridgeError("calendar_operation_forbidden", 403);
  if (action === "create" || action === "update") validateWrite(request);
  if (process.env.NODE_ENV !== "test" && process.env.GOOGLE_CALENDAR_API_BASE_URL) throw new BridgeError("calendar_api_override_forbidden", 503);
  if (!options.accounts && (typeof request.access_token !== "string" || !request.access_token)) throw new BridgeError("calendar_reconnect", 409);
  const auth = new OAuth2Client();
  auth.setCredentials({ access_token: request.access_token });
  const accounts = options.accounts || new Map([["trojanclaw", auth]]);
  const enabled = action === "create" ? [...READ_TOOLS, "create-event"] : action === "update" ? [...READ_TOOLS, "update-event"] : READ_TOOLS;
  let originalEvent;
  const restore = [];
  // Keep the upstream handlers unchanged while preserving Google's concurrency
  // token and enforcing it on the one reviewed PATCH request.
  for (const clientAuth of accounts.values()) {
    const originalRequest = clientAuth.request;
    clientAuth.request = async function(options, ...rest) {
      const parsed = new URL(options.url);
      const eventPath = `/calendars/${request.calendar_id || "primary"}/events/${request.event_id}`;
      const pathname = decodeURIComponent(parsed.pathname).replace(/^\/calendar\/v3/, "");
      const method = (options.method || "GET").toUpperCase();
      if (action === "update" && method === "PATCH") {
        if (pathname !== eventPath) throw new BridgeError("calendar_operation_forbidden", 403);
        options = { ...options, headers: { ...options.headers, "If-Match": request.expected_etag } };
      }
      const response = await originalRequest.call(this, options, ...rest);
      if (action === "get" && method === "GET" && pathname === eventPath) originalEvent = response.data;
      return response;
    };
    restore.push(() => { clientAuth.request = originalRequest; });
  }
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
    if (action === "list") {
      const result = await call("list-events", { calendarId, timeMin: dateInput(request.start), timeMax: dateInput(request.end),
        timeZone: request.timezone, fields: ["id", "summary", "start", "end", "location", "status"] });
      if (!Array.isArray(result.data.events) || result.data.partialFailures?.length || result.data.warnings?.length) {
        throw new BridgeError("calendar_mcp_access");
      }
      // Upstream returns one Google results page. Never imply an exhaustive list
      // when the response is at its page size or our chat output limit.
      return { status: 200, data: { events: result.data.events.slice(0, 100), limited: result.data.events.length >= 100 } };
    }
    if (action === "get") {
      const result = await call("get-event", { calendarId, eventId: request.event_id });
      return result.status === 404 ? result : { status: 200, data: { ...result.data.event, ...(originalEvent?.etag ? { etag: originalEvent.etag } : {}) } };
    }
    if (action === "freebusy") {
      const result = await call("get-freebusy", { calendars: [{ id: calendarId }], timeMin: dateInput(request.start), timeMax: dateInput(request.end), timeZone: "UTC" });
      const availability = result.data.calendars?.[calendarId];
      if (!availability || availability.errors?.length || !Array.isArray(availability.busy)) throw new BridgeError("calendar_availability_failed", 502);
      return result;
    }
    const event = request.event;
    const result = await call(action === "update" ? "update-event" : "create-event", {
      calendarId, eventId: action === "update" ? request.event_id : event.id, summary: event.summary,
      description: event.description, location: event.location,
      start: writeTimeInput(event.start.dateTime, event.start.timeZone), end: writeTimeInput(event.end.dateTime, event.end.timeZone),
      timeZone: event.start.timeZone, ...(event.reminders ? { reminders: event.reminders } : {}),
      ...(event.recurrence ? { recurrence: event.recurrence } : {}),
      extendedProperties: event.extendedProperties, sendUpdates: "none",
    });
    return result.status === 409 ? result : { status: 200, data: result.data.event };
  } finally {
    restore.forEach(callback => callback());
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
