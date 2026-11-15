import test from "node:test";
import assert from "node:assert/strict";
import { execute, BridgeError } from "./bridge.mjs";
import { OAuth2Client } from "google-auth-library";

const proposal = "12345678-1234-1234-1234-123456789abc";
const event = {
  id: `tc${proposal.replaceAll("-", "")}`, summary: "Study session",
  start: { dateTime: "2030-11-17T16:00:00-08:00", timeZone: "America/Los_Angeles" },
  end: { dateTime: "2030-11-17T17:00:00-08:00", timeZone: "America/Los_Angeles" },
  description: "Review notes", location: "Library", reminders: { useDefault: false },
  extendedProperties: { private: { trojanclaw_proposal: proposal } },
};
const result = (data) => ({ content: [{ type: "text", text: JSON.stringify(data) }], structuredContent: data });

test("actual upstream MCP schemas expose only the allowlisted read tools", async () => {
  const { data } = await execute({ action: "tools", access_token: "fake" });
  assert.deepEqual(data.tools.map((tool) => tool.name).sort(), ["get-event", "get-freebusy", "list-calendars", "list-events"]);
});

test("connection verification only lists calendars and selects primary", async () => {
  const calls = [];
  const response = await execute({ action: "verify", access_token: "fake" }, {
    executeHandler: async (handler, args) => {
      calls.push([handler.constructor.name, args]);
      return result({ calendars: [{ id: "student@usc.edu", summary: "Student", primary: true }] });
    },
  });
  assert.equal(response.data.id, "student@usc.edu");
  assert.deepEqual(calls.map(([name]) => name), ["ListCalendarsHandler"]);
});

test("real upstream create schema receives only the approved event fields", async () => {
  let recorded;
  const response = await execute({ action: "create", access_token: "fake", approved_proposal: proposal, event }, {
    executeHandler: async (handler, args) => {
      assert.equal(handler.constructor.name, "CreateEventHandler");
      recorded = args;
      return result({ event });
    },
  });
  assert.deepEqual(response.data, event);
  assert.equal(recorded.eventId, event.id);
  assert.equal(recorded.summary, event.summary);
  assert.equal(recorded.start, "2030-11-17T16:00:00");
  assert.equal(recorded.end, "2030-11-17T17:00:00");
  assert.equal(recorded.timeZone, event.start.timeZone);
  assert.deepEqual(recorded.reminders, { useDefault: false });
  assert.deepEqual(recorded.extendedProperties, event.extendedProperties);
  assert.equal(recorded.sendUpdates, "none");
  assert.equal(recorded.attendees, undefined);
  assert.equal(recorded.recurrence, undefined);
});

test("availability calls the native free/busy MCP tool", async () => {
  const response = await execute({ action: "freebusy", access_token: "fake", start: event.start.dateTime, end: event.end.dateTime }, {
    executeHandler: async (handler, args) => {
      assert.equal(handler.constructor.name, "FreeBusyEventHandler");
      assert.deepEqual(args.calendars, [{ id: "primary" }]);
      assert.equal(args.timeMin, event.start.dateTime);
      assert.equal(args.timeMax, event.end.dateTime);
      return result({ calendars: { primary: { busy: [] } } });
    },
  });
  assert.deepEqual(response.data.calendars.primary.busy, []);
});

test("write adapter retains reviewed local clock times with named zones", async () => {
  for (const [start, end, zone, expected] of [
    ["2026-11-15T13:00:00-08:00", "2026-11-15T13:05:00-08:00", "America/Los_Angeles", "2026-11-15T13:00:00"],
    ["2030-07-15T09:00:00-07:00", "2030-07-15T09:15:00-07:00", "America/Los_Angeles", "2030-07-15T09:00:00"],
    ["2030-11-15T00:00:00Z", "2030-11-15T00:15:00Z", "UTC", "2030-11-15T00:00:00"],
    ["2030-11-15T09:00:00+05:30", "2030-11-15T09:15:00+05:30", "Asia/Kolkata", "2030-11-15T09:00:00"],
  ]) {
    const series = { ...event, start: { dateTime: start, timeZone: zone }, end: { dateTime: end, timeZone: zone },
      recurrence: ["RRULE:FREQ=DAILY;INTERVAL=1;COUNT=10"] };
    await execute({ action: "create", access_token: "fake", approved_proposal: proposal, event: series }, {
      executeHandler: async (_, args) => {
        assert.equal(args.start, expected);
        assert.equal(args.timeZone, zone);
        return result({ event: series });
      },
    });
  }
});

test("write adapter rejects an offset that contradicts the reviewed named zone", async () => {
  let called = false;
  await assert.rejects(execute({ action: "create", access_token: "fake", approved_proposal: proposal,
    event: { ...event, start: { ...event.start, dateTime: "2030-11-17T16:00:00-07:00" } } }, {
    executeHandler: async () => { called = true; return result({ event }); },
  }), error => error.code === "calendar_invalid_draft");
  assert.equal(called, false);
});

test("partial or failed free/busy results never imply availability", async () => {
  await assert.rejects(execute({ action: "freebusy", access_token: "fake", start: event.start.dateTime, end: event.end.dateTime }, {
    executeHandler: async () => result({ calendars: { primary: { errors: [{ reason: "notFound" }] } } }),
  }), (error) => error.code === "calendar_availability_failed");
});

test("get-event retains the proposal marker needed for safe retries", async () => {
  const response = await execute({ action: "get", access_token: "fake", event_id: event.id }, {
    executeHandler: async (_, args) => {
      assert.equal(args.fields, undefined); // Full event read retains recurrence and reminders.
      return result({ event });
    },
  });
  assert.equal(response.data.extendedProperties.private.trojanclaw_proposal, proposal);
});

test("missing events and duplicate IDs normalize to recoverable statuses", async () => {
  const missing = await execute({ action: "get", access_token: "fake", event_id: event.id }, {
    executeHandler: async () => ({ isError: true, content: [{ type: "text", text: `Event with ID '${event.id}' not found in calendar 'primary'.` }] }),
  });
  assert.equal(missing.status, 404);
  const duplicate = await execute({ action: "create", access_token: "fake", approved_proposal: proposal, event }, {
    executeHandler: async () => ({ isError: true, content: [{ type: "text", text: `Event ID '${event.id}' already exists. Please use a different ID.` }] }),
  });
  assert.equal(duplicate.status, 409);
});

for (const [name, changes] of [
  ["missing approval", { approved_proposal: undefined }],
  ["changed retry ID", { event: { ...event, id: "different" } }],
  ["unreviewed guests", { event: { ...event, attendees: [{ email: "guest@example.com" }] } }],
  ["unreviewed recurrence", { event: { ...event, recurrence: ["RRULE:FREQ=DAILY"] } }],
  ["unreviewed reminders", { event: { ...event, reminders: { useDefault: true } } }],
]) {
  test(`${name} is rejected before an upstream handler runs`, async () => {
    let called = false;
    await assert.rejects(execute({ action: "create", access_token: "fake", approved_proposal: proposal, event, ...changes }, {
      executeHandler: async () => { called = true; return result({ event }); },
    }), BridgeError);
    assert.equal(called, false);
  });
}

test("unapproved update, delete and arbitrary tool actions remain unavailable", async () => {
  for (const action of ["update", "delete", "manage-accounts", "respond-to-event"]) {
    await assert.rejects(execute({ action, access_token: "fake" }), (error) => error.status === 403);
  }
});

test("provider failures become safe actionable errors without exposing tokens", async () => {
  await assert.rejects(execute({ action: "verify", access_token: "private-token" }, {
    executeHandler: async () => ({ isError: true, content: [{ type: "text", text: "Google Calendar API has not been used or is disabled. private-token" }] }),
  }), (error) => error.code === "calendar_api_disabled" && !error.message.includes("private-token"));
});

test("upstream handlers work end-to-end through MCP with a mocked Google HTTP boundary", async () => {
  const requests = [];
  let listingEvents = true;
  const auth = new OAuth2Client();
  auth.setCredentials({ access_token: "fake" });
  auth.request = async (options) => {
    const path = decodeURIComponent(new URL(options.url).pathname);
    const method = (options.method || "GET").toUpperCase();
    requests.push({ path, method, data: options.data, headers: options.headers });
    let data;
    if (path.endsWith("/users/me/calendarList")) data = { items: [{ id: "student@usc.edu", summary: "Student", primary: true, accessRole: "owner", timeZone: "America/Los_Angeles" }] };
    else if (path.endsWith("/freeBusy")) data = { calendars: { "student@usc.edu": { busy: [] } } };
    else if (path.endsWith(`/events/${event.id}`) && method === "GET") data = { ...event, etag: '"version1"' };
    else if (path.endsWith(`/events/${event.id}`) && method === "PATCH") {
      assert.equal(options.data.start.timeZone, "America/Los_Angeles");
      assert.equal(options.data.end.timeZone, "America/Los_Angeles");
      data = { ...event, ...options.data, etag: '"version2"' };
    }
    else if (path.endsWith("/events") && method === "GET") data = { items: listingEvents ? [event, { id: "all-day", summary: "Holiday", start: { date: "2030-11-18" }, end: { date: "2030-11-19" } }] : [] };
    else if (path.endsWith("/events") && method === "POST") {
      // Google rejects recurring requests without explicit start/end timeZone.
      // The old adapter lost these fields even though tool args had a zone.
      assert.equal(options.data.start.timeZone, "America/Los_Angeles");
      assert.equal(options.data.end.timeZone, "America/Los_Angeles");
      assert.equal(options.data.start.dateTime, "2030-11-17T16:00:00");
      assert.equal(options.data.end.dateTime, "2030-11-17T17:00:00");
      data = { ...options.data, htmlLink: "https://calendar.google.com/calendar/event?eid=test" };
    }
    else if (/\/calendars\/[^/]+$/.test(path)) data = { id: "student@usc.edu", timeZone: "America/Los_Angeles" };
    else throw new Error(`Unexpected Google request: ${method} ${path}`);
    return { data, status: 200, statusText: "OK", headers: {}, config: options };
  };
  const options = { accounts: new Map([["trojanclaw", auth]]) };
  assert.equal((await execute({ action: "verify" }, options)).data.id, "student@usc.edu");
  assert.equal((await execute({ action: "get", calendar_id: "student@usc.edu", event_id: event.id }, options)).data.extendedProperties.private.trojanclaw_proposal, proposal);
  assert.equal((await execute({ action: "get", calendar_id: "student@usc.edu", event_id: event.id }, options)).data.etag, '"version1"');
  const listed = await execute({ action: "list", calendar_id: "student@usc.edu", start: event.start.dateTime, end: "2030-11-20T00:00:00-08:00", timezone: "America/Los_Angeles" }, options);
  assert.equal(listed.data.events.length, 2);
  assert.equal(listed.data.events[0].summary, "Study session");
  assert.equal(listed.data.events[1].start.date, "2030-11-18");
  assert.equal(listed.data.limited, false);
  listingEvents = false;
  assert.deepEqual((await execute({ action: "freebusy", calendar_id: "student@usc.edu", start: event.start.dateTime, end: event.end.dateTime }, options)).data.calendars["student@usc.edu"].busy, []);
  assert.equal(requests.filter((request) => request.method === "POST" && request.path.endsWith("/events")).length, 0);
  const created = await execute({ action: "create", calendar_id: "student@usc.edu", approved_proposal: proposal, event }, options);
  assert.equal(created.data.id, event.id);
  const writes = requests.filter((request) => request.method === "POST" && request.path.endsWith("/events"));
  assert.equal(writes.length, 1);
  assert.equal(writes[0].data.id, event.id);
  assert.deepEqual(writes[0].data.reminders, { useDefault: false });
  assert.equal(writes[0].data.attendees, undefined);
  assert.equal(writes[0].data.recurrence, undefined);
  const repeating = { ...event, recurrence: ["RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO;COUNT=4"], reminders: { useDefault: false, overrides: [{ method: "popup", minutes: 0 }] } };
  const series = await execute({ action: "create", calendar_id: "student@usc.edu", approved_proposal: proposal, event: repeating }, options);
  assert.deepEqual(series.data.recurrence, repeating.recurrence);
  const changed = await execute({ action: "update", calendar_id: "student@usc.edu", event_id: event.id,
    approved_proposal: proposal, expected_etag: '"version1"', event: { ...event, summary: "Updated study" } }, options);
  assert.equal(changed.data.summary, "Updated study");
  const patches = requests.filter(request => request.method === "PATCH");
  assert.equal(patches.length, 1);
  assert.equal(patches[0].headers["If-Match"], '"version1"');
  assert.equal(patches[0].data.extendedProperties.private.trojanclaw_proposal, proposal);
});
