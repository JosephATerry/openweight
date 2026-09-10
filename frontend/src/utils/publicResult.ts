import type { JsonValue } from "../api/types";

const HIDDEN_KEYS = new Set([
  "reasoning",
  "reasoning_content",
  "chain_of_thought",
  "hidden_reasoning",
  "system_prompt",
  "raw_prompt",
  "authorization",
  "access_token",
  "secret",
  "database_url",
]);

export interface EvidenceItem {
  title: string;
  source?: string;
  excerpt?: string;
}

export interface PublicAgentResult {
  answer: string;
  evidence: EvidenceItem[];
}

function isRecord(value: JsonValue): value is Record<string, JsonValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function textValue(value: JsonValue | undefined): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function evidenceFrom(value: JsonValue | undefined): EvidenceItem[] {
  if (!Array.isArray(value)) return [];
  const evidence: EvidenceItem[] = [];
  value.forEach((entry, index) => {
    if (typeof entry === "string") {
      evidence.push({ title: `Evidence ${index + 1}`, excerpt: entry });
      return;
    }
    if (!isRecord(entry)) return;
    const title =
      textValue(entry.title) ??
      textValue(entry.name) ??
      textValue(entry.policy) ??
      `Evidence ${index + 1}`;
    const source = textValue(entry.source) ?? textValue(entry.uri);
    const excerpt =
      textValue(entry.excerpt) ??
      textValue(entry.snippet) ??
      textValue(entry.content);
    evidence.push({ title, source, excerpt });
  });
  return evidence;
}

function sanitize(value: JsonValue): JsonValue {
  if (Array.isArray(value)) return value.map(sanitize);
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !HIDDEN_KEYS.has(key.toLowerCase()))
      .map(([key, entry]) => [key, sanitize(entry)]),
  );
}

function safeFallback(value: JsonValue): string {
  if (typeof value === "string") return value;
  return JSON.stringify(sanitize(value), null, 2);
}

export function toPublicAgentResult(result: JsonValue): PublicAgentResult {
  if (!isRecord(result)) {
    return { answer: safeFallback(result), evidence: [] };
  }
  const answer =
    textValue(result.answer) ??
    textValue(result.response) ??
    textValue(result.content) ??
    safeFallback(result);
  const evidence = evidenceFrom(
    result.evidence ?? result.citations ?? result.sources ?? result.documents,
  );
  return { answer, evidence };
}
