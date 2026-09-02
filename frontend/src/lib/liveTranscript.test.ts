import { describe, expect, it } from "vitest";
import { parseTranscriptPayload, TranscriptDeduper } from "./liveTranscript";

function payload(obj: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(obj));
}

describe("parseTranscriptPayload", () => {
  it("parses a finalized human transcript segment", () => {
    const segment = parseTranscriptPayload(
      payload({ object: "user.transcription", uid: 12345, text: "Payments are failing.", final: true, turn_id: 1 }),
      9999
    );
    expect(segment).toEqual({
      uid: 12345,
      text: "Payments are failing.",
      isFinal: true,
      turnId: "1",
      isAgent: false,
    });
  });

  it("marks a segment as the agent via the object field", () => {
    const segment = parseTranscriptPayload(
      payload({ object: "assistant.transcription", uid: 9999, text: "How can I help?", final: true }),
      9999
    );
    expect(segment?.isAgent).toBe(true);
  });

  it("falls back to comparing uid against agentUid when no object/type field is present", () => {
    const agentSegment = parseTranscriptPayload(payload({ uid: 9999, text: "hello", final: true }), 9999);
    const humanSegment = parseTranscriptPayload(payload({ uid: 42, text: "hello", final: true }), 9999);
    expect(agentSegment?.isAgent).toBe(true);
    expect(humanSegment?.isAgent).toBe(false);
  });

  it("fails closed (returns null) when speaker can't be determined at all", () => {
    const segment = parseTranscriptPayload(payload({ uid: 42, text: "hello", final: true }), null);
    expect(segment).toBeNull();
  });

  it("treats is_final and isFinal as equivalent to final", () => {
    expect(parseTranscriptPayload(payload({ uid: 1, text: "a", is_final: true }), 9999)?.isFinal).toBe(true);
    expect(parseTranscriptPayload(payload({ uid: 1, text: "a", isFinal: true }), 9999)?.isFinal).toBe(true);
  });

  it("marks an interim (non-final) segment accordingly rather than dropping it", () => {
    const segment = parseTranscriptPayload(payload({ uid: 1, text: "partial words", final: false }), 9999);
    expect(segment?.isFinal).toBe(false);
  });

  it("returns null for empty or whitespace-only text", () => {
    expect(parseTranscriptPayload(payload({ uid: 1, text: "   ", final: true }), 9999)).toBeNull();
    expect(parseTranscriptPayload(payload({ uid: 1, final: true }), 9999)).toBeNull();
  });

  it("returns null instead of throwing on malformed/non-JSON payloads", () => {
    expect(parseTranscriptPayload(new TextEncoder().encode("not json at all"), 9999)).toBeNull();
    expect(parseTranscriptPayload(new Uint8Array([0xff, 0xfe, 0x00, 0x01]), 9999)).toBeNull();
  });

  it("returns null for a JSON payload that isn't an object", () => {
    expect(parseTranscriptPayload(payload("just a string"), 9999)).toBeNull();
    expect(parseTranscriptPayload(payload(42), 9999)).toBeNull();
    expect(parseTranscriptPayload(payload(null), 9999)).toBeNull();
  });

  it("accepts alternate turn-id field names", () => {
    expect(parseTranscriptPayload(payload({ uid: 1, text: "a", final: true, turnId: 7 }), 9999)?.turnId).toBe("7");
    expect(parseTranscriptPayload(payload({ uid: 1, text: "a", final: true, msg_id: "m1" }), 9999)?.turnId).toBe(
      "m1"
    );
    // message_id (not msg_id) is the field name documented/observed for
    // Agora's actual RTM transcript payloads.
    expect(
      parseTranscriptPayload(payload({ uid: 1, text: "a", final: true, message_id: "m2" }), 9999)?.turnId
    ).toBe("m2");
    // turn_id takes priority over message_id when both are present, since
    // dedup is keyed per conversational turn, not per individual message.
    expect(
      parseTranscriptPayload(
        payload({ uid: 1, text: "a", final: true, turn_id: 3, message_id: "m3" }),
        9999
      )?.turnId
    ).toBe("3");
  });

  it("recognizes an underscore-separated object field the same as a dot-separated one", () => {
    // The real captured payload used "user_transcription" (underscore);
    // published docs/examples use "user.transcription" (dot). Neither
    // contains "assistant"/"agent" so both must classify as human.
    const underscore = parseTranscriptPayload(
      payload({ object: "user_transcription", uid: 1, text: "hello", final: true }),
      9999
    );
    const dotted = parseTranscriptPayload(
      payload({ object: "user.transcription", uid: 1, text: "hello", final: true }),
      9999
    );
    expect(underscore?.isAgent).toBe(false);
    expect(dotted?.isAgent).toBe(false);

    const agentUnderscore = parseTranscriptPayload(
      payload({ object: "assistant_transcription", uid: 9999, text: "hi", final: true }),
      9999
    );
    expect(agentUnderscore?.isAgent).toBe(true);
  });

  it("invokes the diagnostic callback with a reason at every failure point", () => {
    const calls: Array<[string, unknown]> = [];
    const diag = (stage: string, info?: Record<string, unknown>) => calls.push([stage, info]);

    parseTranscriptPayload("not json", 9999, diag);
    parseTranscriptPayload(payload(42), 9999, diag);
    parseTranscriptPayload(payload({ uid: 1, text: "  ", final: true }), 9999, diag);
    parseTranscriptPayload(payload({ uid: 42, text: "hi", final: true }), null, diag);

    expect(calls.map(([stage]) => stage)).toEqual([
      "parse-error",
      "not-an-object",
      "empty-text",
      "cannot-classify-speaker",
    ]);
  });

  it("invokes the diagnostic callback with the parsed result on success", () => {
    const calls: Array<[string, unknown]> = [];
    const diag = (stage: string, info?: Record<string, unknown>) => calls.push([stage, info]);

    parseTranscriptPayload(payload({ uid: 1, text: "hi", final: true, turn_id: 1 }), 9999, diag);

    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("parsed");
  });

  it("parses a plain string payload identically to an encoded Uint8Array one", () => {
    // agora-rtm-sdk's MessageEvent.message is `string | Uint8Array` - RTM
    // messages can arrive as either, unlike the RTC stream-message channel
    // which was always Uint8Array.
    const obj = { object: "user.transcription", uid: 42, text: "Rollback confirmed.", final: true, turn_id: 3 };
    const fromString = parseTranscriptPayload(JSON.stringify(obj), 9999);
    const fromBytes = parseTranscriptPayload(payload(obj), 9999);
    expect(fromString).toEqual(fromBytes);
    expect(fromString?.text).toBe("Rollback confirmed.");
  });

  it("returns null for a malformed string payload instead of throwing", () => {
    expect(parseTranscriptPayload("not json at all", 9999)).toBeNull();
  });
});

describe("TranscriptDeduper", () => {
  const segment = (overrides: Partial<{ turnId: string | null; uid: number | string; text: string }> = {}) => ({
    uid: 1,
    text: "hello",
    isFinal: true,
    turnId: "t1",
    isAgent: false,
    ...overrides,
  });

  it("allows the first occurrence of a segment", () => {
    const deduper = new TranscriptDeduper();
    expect(deduper.shouldSubmit(segment())).toBe(true);
  });

  it("rejects a repeated segment with the same turnId", () => {
    const deduper = new TranscriptDeduper();
    deduper.shouldSubmit(segment());
    expect(deduper.shouldSubmit(segment())).toBe(false);
  });

  it("falls back to uid+text identity when turnId is missing", () => {
    const deduper = new TranscriptDeduper();
    expect(deduper.shouldSubmit(segment({ turnId: null }))).toBe(true);
    expect(deduper.shouldSubmit(segment({ turnId: null }))).toBe(false);
    expect(deduper.shouldSubmit(segment({ turnId: null, text: "different" }))).toBe(true);
  });

  it("treats different turnIds as distinct even with identical text", () => {
    const deduper = new TranscriptDeduper();
    expect(deduper.shouldSubmit(segment({ turnId: "a" }))).toBe(true);
    expect(deduper.shouldSubmit(segment({ turnId: "b" }))).toBe(true);
  });

  it("allows a second, genuinely different finalized utterance within the same turnId", () => {
    // Regression test: a single conversational turn can produce more than
    // one finalized ASR segment (the speaker pauses mid-turn before the
    // agent responds). Keying dedup on turnId alone would silently drop the
    // second utterance as a false "repeat" of the first, losing real content.
    const deduper = new TranscriptDeduper();
    expect(deduper.shouldSubmit(segment({ turnId: "t1", text: "Payments are failing." }))).toBe(true);
    expect(deduper.shouldSubmit(segment({ turnId: "t1", text: "Checkout is down too." }))).toBe(true);
    // An exact repeat of either one (e.g. a redelivered final) is still a duplicate.
    expect(deduper.shouldSubmit(segment({ turnId: "t1", text: "Payments are failing." }))).toBe(false);
  });

  it("evicts the oldest entry once the bound is exceeded", () => {
    const deduper = new TranscriptDeduper(2);
    deduper.shouldSubmit(segment({ turnId: "1" }));
    deduper.shouldSubmit(segment({ turnId: "2" }));
    deduper.shouldSubmit(segment({ turnId: "3" })); // evicts "1"
    expect(deduper.shouldSubmit(segment({ turnId: "1" }))).toBe(true); // forgotten, allowed again
    expect(deduper.shouldSubmit(segment({ turnId: "3" }))).toBe(false); // still remembered
  });

  it("forgets everything after reset", () => {
    const deduper = new TranscriptDeduper();
    deduper.shouldSubmit(segment());
    deduper.reset();
    expect(deduper.shouldSubmit(segment())).toBe(true);
  });
});
