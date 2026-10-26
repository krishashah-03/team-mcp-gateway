import { describe, expect, it } from "vitest";
import { formatDateTime, formatDuration, pluralize, timeAgo } from "./format";

describe("formatDuration", () => {
  it.each([
    [0, "0 ms"],
    [42, "42 ms"],
    [999, "999 ms"],
    [1500, "1.50 s"],
    [9999, "10.00 s"],
    [15_000, "15.0 s"],
  ])("%i -> %s", (ms, expected) => expect(formatDuration(ms)).toBe(expected));
});

describe("timeAgo", () => {
  const now = new Date("2026-10-25T12:00:00Z");
  it.each([
    ["2026-10-25T11:59:58Z", "just now"],
    ["2026-10-25T11:59:30Z", "30s ago"],
    ["2026-10-25T11:55:00Z", "5m ago"],
    ["2026-10-25T09:00:00Z", "3h ago"],
    ["2026-10-22T12:00:00Z", "3d ago"],
  ])("%s -> %s", (value, expected) => expect(timeAgo(value, now)).toBe(expected));

  it("handles missing and invalid values", () => {
    expect(timeAgo(null, now)).toBe("never");
    expect(timeAgo("not a date", now)).toBe("-");
  });
});

describe("formatDateTime", () => {
  it("handles missing and invalid values", () => {
    expect(formatDateTime(null)).toBe("-");
    expect(formatDateTime(undefined)).toBe("-");
    expect(formatDateTime("garbage")).toBe("-");
  });
  it("formats a real date", () => {
    expect(formatDateTime("2026-10-25T12:00:00Z")).toMatch(/2026/);
  });
});

describe("pluralize", () => {
  it("handles one, zero and many", () => {
    expect(pluralize(1, "tool")).toBe("1 tool");
    expect(pluralize(0, "tool")).toBe("0 tools");
    expect(pluralize(5, "tool")).toBe("5 tools");
  });
});
