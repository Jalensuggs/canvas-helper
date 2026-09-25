import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, idOf, listFrom, streamChat, text } from "./api";

describe("text", () => {
  it("falls back for empty-ish values but keeps zero and false", () => {
    expect(text(null, "fallback")).toBe("fallback");
    expect(text(undefined, "fallback")).toBe("fallback");
    expect(text("", "fallback")).toBe("fallback");
    expect(text(0)).toBe("0");
    expect(text(false)).toBe("false");
  });
});

describe("listFrom", () => {
  it("returns arrays unchanged", () => {
    expect(listFrom([1, 2])).toEqual([1, 2]);
  });

  it("prefers the caller's keys over the generic ones", () => {
    const payload = { items: [{ id: "generic" }], planner: [{ id: "specific" }] };
    expect(listFrom(payload, ["planner"])).toEqual([{ id: "specific" }]);
  });

  it("falls back to the generic keys", () => {
    expect(listFrom({ results: [{ id: 1 }] })).toEqual([{ id: 1 }]);
  });

  it("returns an empty array for anything unusable", () => {
    expect(listFrom(null)).toEqual([]);
    expect(listFrom("nope")).toEqual([]);
    expect(listFrom({ items: "not an array" })).toEqual([]);
  });
});

describe("idOf", () => {
  it("prefers id, then the Canvas-specific fallbacks", () => {
    expect(idOf({ id: 1, canvas_id: 2 })).toBe("1");
    expect(idOf({ canvas_id: 2 })).toBe("2");
    expect(idOf({ course_id: 5 })).toBe("5");
    expect(idOf({})).toBe("");
  });
});

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

describe("streamChat", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("parses events split across network chunks", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: text_delta\ndata: {"type":"text_delta","delta":"Hel',
          'lo"}\n\n',
          'event: done\ndata: {"type":"done"}\n\n',
        ]),
      ),
    );
    const events: unknown[] = [];
    await streamChat("hi", {}, (event) => events.push(event));
    expect(events).toEqual([
      { type: "text_delta", delta: "Hello" },
      { type: "done" },
    ]);
  });

  it("raises ApiError with the server detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ detail: "AI is disabled" }), {
            status: 503,
            headers: { "content-type": "application/json" },
          }),
      ),
    );
    await expect(streamChat("hi", {}, () => undefined)).rejects.toBeInstanceOf(
      ApiError,
    );
  });

  it("stops reading once the caller aborts", async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          'event: text_delta\ndata: {"type":"text_delta","delta":"one"}\n\n',
          'event: text_delta\ndata: {"type":"text_delta","delta":"two"}\n\n',
        ]),
      ),
    );
    const events: unknown[] = [];
    await streamChat(
      "hi",
      {},
      (event) => {
        events.push(event);
        controller.abort();
      },
      controller.signal,
    );
    expect(events).toEqual([{ type: "text_delta", delta: "one" }]);
  });
});
