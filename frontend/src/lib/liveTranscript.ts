/**
 * Parsing/dedup for Agora Conversational AI's live transcript stream (M6.1).
 *
 * Framework-agnostic on purpose: no React, no Agora SDK imports here, so this
 * can be unit tested directly. `useAgoraRoom.ts` is the only caller.
 *
 * TRANSPORT: live verification against a real Agora project found the RTC
 * `stream-message` data channel never fires for Conversational AI
 * transcripts in this project's configuration. Per Agora's Conversational AI
 * docs, live transcript delivery requires `advanced_features.enable_rtm` +
 * `parameters.data_channel: "rtm"` *and* `parameters.transcript: {enable:
 * true, protocol_version: "v2"}` on the agent join request (see
 * backend/app/agora.py's `build_agent_join_payload`) and is delivered over
 * Signaling (RTM) channel messages, not the RTC data channel —
 * `useAgoraRoom.ts` subscribes via `agora-rtm-sdk` instead of
 * `client.on("stream-message", ...)`. The `transcript` block was the
 * concretely missing piece: `data_channel: "rtm"` alone only routes delivery
 * onto Signaling, it doesn't turn transcription on or pin the protocol
 * version, which is consistent with transcript delivery having been
 * observed but unreliable.
 *
 * PAYLOAD FORMAT: a real captured RTM payload (`object: "user_transcription"`,
 * `final: false`) confirms this is flat JSON with recognizable field names —
 * not the chunked/base64 message-compaction scheme Agora uses for some other
 * data channels — matching what `parseTranscriptPayload` already assumes.
 * Confirmed field names (both by that capture and by Agora's docs/blog
 * examples): `object` (`"user.transcription"` / `"assistant.transcription"`,
 * dot- or underscore-separated — the isAgent check only substring-matches
 * "assistant"/"agent" so either separator works), `text`, `final`, `turn_id`,
 * `user_id`/`uid`, `stream_id`, `message_id`. `parseTranscriptPayload` still
 * fails closed (returns null, never throws) on anything unparseable, since a
 * Protobuf/legacy-v1 payload remains a live-verification risk if the
 * `protocol_version: "v2"` request above weren't honored for some reason.
 */

export interface ParsedTranscriptSegment {
  uid: number | string;
  text: string;
  isFinal: boolean;
  turnId: string | null;
  isAgent: boolean;
}

/**
 * Optional diagnostic sink for `parseTranscriptPayload`, called at each
 * decision point (both on failure and on success) so a caller can log why a
 * given RTM message was accepted, dropped, or misclassified - without
 * changing the function's return contract (still null-on-failure, never
 * throws) that the unit tests rely on.
 */
export type TranscriptParseDiagnostic = (stage: string, info?: Record<string, unknown>) => void;

/**
 * Best-effort decode of one Agora Conversational AI RTM transcript message.
 * Returns null (never throws) if the payload isn't parseable JSON, has no
 * usable text, or its speaker (human vs. EchoWard) can't be determined.
 *
 * `payload` is `string | Uint8Array` because that's exactly the type of
 * `agora-rtm-sdk`'s `MessageEvent.message` field - RTM messages can be sent
 * as either.
 */
export function parseTranscriptPayload(
  payload: string | Uint8Array,
  agentUid: number | string | null,
  diag?: TranscriptParseDiagnostic
): ParsedTranscriptSegment | null {
  let raw: unknown;
  try {
    const text = typeof payload === "string" ? payload : new TextDecoder().decode(payload);
    raw = JSON.parse(text);
  } catch (err) {
    diag?.("parse-error", { message: err instanceof Error ? err.message : String(err) });
    return null;
  }
  if (typeof raw !== "object" || raw === null) {
    diag?.("not-an-object", { typeOf: typeof raw });
    return null;
  }
  const obj = raw as Record<string, unknown>;

  const text = typeof obj.text === "string" ? obj.text.trim() : "";
  if (!text) {
    diag?.("empty-text", { object: obj.object ?? obj.type ?? null });
    return null;
  }

  const isFinal = obj.final === true || obj.is_final === true || obj.isFinal === true;

  const uidRaw = obj.uid ?? obj.user_id ?? null;
  const uid: number | string =
    typeof uidRaw === "number" || typeof uidRaw === "string" ? uidRaw : "unknown";

  // turn_id is Agora's documented field for the conversational turn a
  // segment belongs to; message_id/msg_id are defensive fallbacks for
  // per-message ids seen in some payload variants. Prefer turn_id when both
  // are present since dedup is keyed per-turn (see TranscriptDeduper).
  const turnIdRaw =
    obj.turn_id ?? obj.turnId ?? obj.message_id ?? obj.messageId ?? obj.msg_id ?? obj.msgId ?? null;
  const turnId = turnIdRaw === null || turnIdRaw === undefined ? null : String(turnIdRaw);

  const objectField =
    typeof obj.object === "string" ? obj.object : typeof obj.type === "string" ? obj.type : "";

  let isAgent: boolean;
  if (objectField) {
    isAgent = /assistant|agent/i.test(objectField);
  } else if (agentUid !== null) {
    isAgent = String(uid) === String(agentUid);
  } else {
    // No signal to tell human from agent speech - fail closed rather than
    // risk submitting EchoWard's own words as a human incident statement.
    diag?.("cannot-classify-speaker", { uid });
    return null;
  }

  diag?.("parsed", { uid, isFinal, isAgent, turnId, textLength: text.length });
  return { uid, text, isFinal, turnId, isAgent };
}

/**
 * Tracks which finalized transcript segments have already been submitted, so
 * a re-delivered "final" event for the same turn isn't sent to incident
 * intelligence twice. Purely in-memory/client-side and bounded, so a
 * long-running room can't leak memory - no external store needed.
 */
export class TranscriptDeduper {
  private seen = new Set<string>();
  private order: string[] = [];

  constructor(private readonly limit = 200) {}

  // Keyed on (turnId ?? uid) *and* text, not turnId alone: a single
  // conversational turn can carry more than one finalized ASR segment (e.g.
  // the speaker pauses mid-turn before the agent responds), so keying purely
  // on turnId would treat a second, genuinely-new final utterance in the same
  // turn as a repeat of the first and silently drop it.
  private keyFor(segment: ParsedTranscriptSegment): string {
    return `${segment.turnId ?? segment.uid}:${segment.text}`;
  }

  /** Returns true the first time a segment is seen, false on any repeat. */
  shouldSubmit(segment: ParsedTranscriptSegment): boolean {
    const key = this.keyFor(segment);
    if (this.seen.has(key)) return false;
    this.seen.add(key);
    this.order.push(key);
    if (this.order.length > this.limit) {
      const oldest = this.order.shift();
      if (oldest !== undefined) this.seen.delete(oldest);
    }
    return true;
  }

  reset(): void {
    this.seen.clear();
    this.order = [];
  }
}
