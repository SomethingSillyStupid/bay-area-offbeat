"use strict";

const DATA_URL = "data/current.json";
const PACIFIC_TIME_ZONE = "America/Los_Angeles";
const EXPECTED_SCHEMA_VERSION = 1;
const CANONICAL_EVENT_ID = /^evt_[0-9a-f]{16}$/;
const MAX_EDITION_AGE_MS = 36 * 60 * 60 * 1000;

const pacificDateParts = new Intl.DateTimeFormat("en-US", {
  timeZone: PACIFIC_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const fullDateFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: PACIFIC_TIME_ZONE,
  weekday: "long",
  month: "long",
  day: "numeric",
});

const localTimeFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: PACIFIC_TIME_ZONE,
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

const updatedFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: PACIFIC_TIME_ZONE,
  weekday: "short",
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZoneName: "short",
});

function parseInstant(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error("Invalid event date");
  }
  return parsed;
}

function assertFreshTimestamp(value, label, nowMs) {
  const timestamp = parseInstant(value).getTime();
  if (!Number.isFinite(nowMs) || nowMs - timestamp > MAX_EDITION_AGE_MS) {
    throw new Error(`${label} is stale`);
  }
}

function calendarDateFromInstant(value) {
  const parts = {};
  for (const part of pacificDateParts.formatToParts(parseInstant(value))) {
    if (part.type === "year" || part.type === "month" || part.type === "day") {
      parts[part.type] = Number(part.value);
    }
  }

  if (!parts.year || !parts.month || !parts.day) {
    throw new Error("Could not read Pacific calendar date");
  }

  // This UTC Date is a calendar-math container, not an instant in Pacific time.
  return new Date(Date.UTC(parts.year, parts.month - 1, parts.day));
}

function copyCalendarDate(calendarDate) {
  return new Date(calendarDate.getTime());
}

function addCalendarDays(calendarDate, days) {
  const result = copyCalendarDate(calendarDate);
  result.setUTCDate(result.getUTCDate() + days);
  return result;
}

function calendarKey(calendarDate) {
  const year = String(calendarDate.getUTCFullYear()).padStart(4, "0");
  const month = String(calendarDate.getUTCMonth() + 1).padStart(2, "0");
  const day = String(calendarDate.getUTCDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function eventCalendarKey(event) {
  return calendarKey(calendarDateFromInstant(event.starts_at));
}

function mondayFor(calendarDate) {
  const daysSinceMonday = (calendarDate.getUTCDay() + 6) % 7;
  return addCalendarDays(calendarDate, -daysSinceMonday);
}

function compareEvents(left, right) {
  const timeDifference = parseInstant(left.starts_at) - parseInstant(right.starts_at);
  if (timeDifference) {
    return timeDifference;
  }
  // Canonical IDs are ASCII, so relational ordering matches the email renderer
  // independently of a browser's locale collation implementation.
  if (left.id < right.id) {
    return -1;
  }
  if (left.id > right.id) {
    return 1;
  }
  return 0;
}

function formatUpdatedAt(value) {
  return updatedFormatter.format(parseInstant(value));
}

function classifyEvents(documentValue) {
  const generatedDate = calendarDateFromInstant(documentValue.generated_at);
  const thisWeekStart = mondayFor(generatedDate);
  const thisWeekEnd = addCalendarDays(thisWeekStart, 6);
  const nextWeekStart = addCalendarDays(thisWeekStart, 7);
  const nextWeekEnd = addCalendarDays(thisWeekStart, 13);
  const thisStartKey = calendarKey(thisWeekStart);
  const thisEndKey = calendarKey(thisWeekEnd);
  const nextStartKey = calendarKey(nextWeekStart);
  const nextEndKey = calendarKey(nextWeekEnd);
  const buckets = { thisWeek: [], nextWeek: [], radar: [] };

  for (const event of documentValue.events) {
    const dateKey = eventCalendarKey(event);
    if (dateKey >= thisStartKey && dateKey <= thisEndKey) {
      buckets.thisWeek.push(event);
    } else if (dateKey >= nextStartKey && dateKey <= nextEndKey) {
      buckets.nextWeek.push(event);
    } else if (dateKey > nextEndKey && event.radar === true) {
      buckets.radar.push(event);
    }
  }

  buckets.thisWeek.sort(compareEvents);
  buckets.nextWeek.sort(compareEvents);
  buckets.radar.sort(compareEvents);
  return buckets;
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isOptionalString(value) {
  return value === null || typeof value === "string";
}

function safeOfficialUrl(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch (error) {
    throw new Error("Invalid organizer URL", { cause: error });
  }
  if (parsed.protocol !== "https:") {
    throw new Error("Organizer URL must use HTTPS");
  }
  return parsed;
}

function validateEvent(event, nowMs) {
  if (!event || typeof event !== "object" || Array.isArray(event)) {
    throw new Error("Invalid event record");
  }

  if (
    !isNonEmptyString(event.id) ||
    !CANONICAL_EVENT_ID.test(event.id) ||
    !isNonEmptyString(event.title) ||
    !isNonEmptyString(event.starts_at) ||
    typeof event.all_day !== "boolean" ||
    !isNonEmptyString(event.city) ||
    !isOptionalString(event.neighborhood) ||
    !isOptionalString(event.price_note) ||
    !isNonEmptyString(event.official_url) ||
    !isNonEmptyString(event.source_name) ||
    !isNonEmptyString(event.why) ||
    !Array.isArray(event.tags) ||
    !event.tags.every(isNonEmptyString) ||
    typeof event.radar !== "boolean" ||
    !isNonEmptyString(event.last_verified_at)
  ) {
    throw new Error("Event record does not match the expected schema");
  }

  const startsAt = parseInstant(event.starts_at);
  if (event.ends_at !== null) {
    if (!isNonEmptyString(event.ends_at)) {
      throw new Error("Invalid event end date");
    }
    const endsAt = parseInstant(event.ends_at);
    if (endsAt <= startsAt) {
      throw new Error("Event end date must follow its start date");
    }
  }
  safeOfficialUrl(event.official_url);
  assertFreshTimestamp(event.last_verified_at, "Event verification", nowMs);
}

function validateDocument(documentValue, nowMs) {
  if (!documentValue || typeof documentValue !== "object" || Array.isArray(documentValue)) {
    throw new Error("Event data must be an object");
  }
  if (documentValue.schema_version !== EXPECTED_SCHEMA_VERSION) {
    throw new Error("Unsupported event data schema");
  }
  if (!isNonEmptyString(documentValue.generated_at)) {
    throw new Error("Event data has no generation time");
  }
  assertFreshTimestamp(documentValue.generated_at, "Edition", nowMs);
  if (documentValue.timezone !== PACIFIC_TIME_ZONE) {
    throw new Error("Event data uses an unexpected timezone");
  }
  if (!Array.isArray(documentValue.events)) {
    throw new Error("Event data has no event list");
  }
  documentValue.events.forEach((event) => validateEvent(event, nowMs));
  return documentValue;
}

function groupByDate(events) {
  const groups = new Map();
  for (const event of events) {
    const key = eventCalendarKey(event);
    if (!groups.has(key)) {
      groups.set(key, []);
    }
    groups.get(key).push(event);
  }
  return groups;
}

function appendSchedule(container, event) {
  const start = parseInstant(event.starts_at);
  const startTime = document.createElement("time");
  startTime.dateTime = event.starts_at;

  if (event.all_day) {
    startTime.textContent = `${fullDateFormatter.format(start)} · All day`;
    container.append(startTime);
    return;
  }

  startTime.textContent = `${fullDateFormatter.format(start)} · ${localTimeFormatter.format(start)}`;
  container.append(startTime);

  if (event.ends_at !== null) {
    const end = parseInstant(event.ends_at);
    const sameDate = eventCalendarKey(event) === calendarKey(calendarDateFromInstant(event.ends_at));
    container.append(document.createTextNode(" – "));
    const endTime = document.createElement("time");
    endTime.dateTime = event.ends_at;
    endTime.textContent = sameDate
      ? localTimeFormatter.format(end)
      : `${fullDateFormatter.format(end)} · ${localTimeFormatter.format(end)}`;
    container.append(endTime);
  }
}

function createEventRow(event, headingLevel) {
  const article = document.createElement("article");
  article.className = "event-row";

  const schedule = document.createElement("p");
  schedule.className = "event-schedule";
  appendSchedule(schedule, event);
  article.append(schedule);

  const body = document.createElement("div");
  body.className = "event-row-body";

  const topline = document.createElement("div");
  topline.className = "event-row-topline";
  const title = document.createElement(`h${headingLevel}`);
  title.className = "event-title";
  title.textContent = event.title;
  topline.append(title);

  const detailsLine = document.createElement("p");
  detailsLine.className = "event-details";
  const details = document.createElement("a");
  details.href = safeOfficialUrl(event.official_url).href;
  details.target = "_blank";
  details.rel = "noopener noreferrer";
  details.textContent = `Details — ${event.source_name}`;
  detailsLine.append(details);
  topline.append(detailsLine);
  body.append(topline);

  const meta = document.createElement("p");
  meta.className = "event-meta";
  const location = document.createElement("span");
  location.className = "event-location";
  const locationParts = [];
  if (event.neighborhood) {
    locationParts.push(event.neighborhood);
  }
  locationParts.push(event.city);
  location.textContent = locationParts.join(" · ");
  meta.append(location);

  if (event.price_note) {
    const price = document.createElement("span");
    price.className = "event-price";
    price.textContent = event.price_note;
    meta.append(price);
  }
  body.append(meta);

  const why = document.createElement("p");
  why.className = "event-why";
  const whyLabel = document.createElement("span");
  whyLabel.className = "event-label";
  whyLabel.textContent = "Why it’s on the list: ";
  const whyText = document.createElement("span");
  whyText.textContent = event.why;
  why.append(whyLabel, whyText);
  body.append(why);

  if (event.tags.length > 0) {
    const tags = document.createElement("ul");
    tags.className = "tag-list";
    tags.setAttribute("aria-label", "Tags");
    for (const tag of event.tags) {
      const item = document.createElement("li");
      item.textContent = tag;
      tags.append(item);
    }
    body.append(tags);
  }

  article.append(body);
  return article;
}

function createEmptyState(message) {
  const empty = document.createElement("p");
  empty.className = "empty-state";
  empty.textContent = message;
  return empty;
}

function renderGroupedEvents(container, events) {
  container.replaceChildren();
  if (events.length === 0) {
    container.append(createEmptyState("No picks in this window."));
    return;
  }

  for (const [dateKey, dateEvents] of groupByDate(events)) {
    const group = document.createElement("div");
    group.className = "day-group";

    const heading = document.createElement("h3");
    heading.className = "day-heading";
    const headingTime = document.createElement("time");
    headingTime.dateTime = dateKey;
    headingTime.textContent = fullDateFormatter.format(parseInstant(dateEvents[0].starts_at));
    heading.append(headingTime);
    group.append(heading);

    const cards = document.createElement("div");
    cards.className = "event-list";
    for (const event of dateEvents) {
      cards.append(createEventRow(event, 4));
    }
    group.append(cards);
    container.append(group);
  }
}

function renderRadarEvents(container, events) {
  container.replaceChildren();
  if (events.length === 0) {
    container.append(createEmptyState("No picks on the radar right now."));
    return;
  }
  for (const event of events) {
    container.append(createEventRow(event, 3));
  }
}

function showError() {
  const status = document.querySelector("#load-status");
  status.hidden = false;
  status.className = "notice notice-error";
  status.setAttribute("role", "alert");
  status.setAttribute("aria-live", "assertive");

  const message = document.createElement("p");
  message.textContent = "We couldn’t load a current edition. The event data is unavailable, expired, or doesn’t match the expected format.";
  const recovery = document.createElement("p");
  recovery.append("You can ");
  const dataLink = document.createElement("a");
  dataLink.href = DATA_URL;
  dataLink.textContent = "inspect the current JSON directly";
  recovery.append(dataLink, ".");
  status.replaceChildren(message, recovery);

  document.querySelector("#this-week-events").replaceChildren();
  document.querySelector("#next-week-events").replaceChildren();
  document.querySelector("#radar-events").replaceChildren();
}

function renderGuide(documentValue) {
  const buckets = classifyEvents(documentValue);
  const updated = document.querySelector("#updated-at");
  updated.dateTime = documentValue.generated_at;
  updated.textContent = formatUpdatedAt(documentValue.generated_at);

  renderGroupedEvents(document.querySelector("#this-week-events"), buckets.thisWeek);
  renderGroupedEvents(document.querySelector("#next-week-events"), buckets.nextWeek);
  renderRadarEvents(document.querySelector("#radar-events"), buckets.radar);

  const status = document.querySelector("#load-status");
  status.replaceChildren();
  status.hidden = true;
}

function loadGuide() {
  const nowMs = Date.now();
  return fetch(DATA_URL, {
    headers: { Accept: "application/json" },
    cache: "no-cache",
  })
    .then((response) => {
      if (!response.ok) {
        throw new Error(`Event data request failed (${response.status})`);
      }
      return response.json();
    })
    .then((documentValue) => validateDocument(documentValue, nowMs))
    .then(renderGuide)
    .catch(() => {
      showError();
    });
}

const BayAreaOffbeat = Object.freeze({
  parseInstant,
  assertFreshTimestamp,
  calendarDateFromInstant,
  addCalendarDays,
  calendarKey,
  mondayFor,
  compareEvents,
  classifyEvents,
  formatUpdatedAt,
  validateDocument,
  validateEvent,
});

if (typeof globalThis !== "undefined") {
  globalThis.BayAreaOffbeat = BayAreaOffbeat;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = BayAreaOffbeat;
}

if (typeof document !== "undefined") {
  loadGuide();
}
