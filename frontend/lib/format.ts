// Pure helpers (unit-tested with Vitest).
import type { EmailStatus, Ratio, Tier } from "./types";

export const VERIFICATION_LEVELS: Record<EmailStatus, { label: string; meaning: string; tone: Tone }> = {
  unknown: { label: "Unknown", meaning: "Not checked yet.", tone: "neutral" },
  syntax_invalid: { label: "Syntax invalid", meaning: "The address does not parse. Never sent.", tone: "bad" },
  syntax_valid: { label: "Syntax valid", meaning: "Parses as an address. No domain or mailbox signal.", tone: "warn" },
  domain_has_mx: {
    label: "Domain has MX",
    meaning: "The domain accepts mail (DNS signal). Says nothing about this mailbox.",
    tone: "neutral",
  },
  provider_verified: {
    label: "Provider verified (mock)",
    meaning: "A verification provider said the mailbox looks valid. In this demo the provider is a MOCK.",
    tone: "good",
  },
  deliverable_confirmed: {
    label: "Delivery confirmed (simulated)",
    meaning: "A delivery event was received for a sent message. In this demo events are SIMULATED.",
    tone: "good",
  },
  bounced: { label: "Bounced", meaning: "A bounce event was received; the address is suppressed.", tone: "bad" },
};

export type Tone = "good" | "warn" | "bad" | "neutral" | "accent";

export function tierTone(tier: Tier | null | undefined): Tone {
  if (tier === "A") return "good";
  if (tier === "B") return "accent";
  if (tier === "C") return "neutral";
  if (tier === "disqualified") return "bad";
  return "neutral";
}

export function statusTone(status: string): Tone {
  if (["completed", "approved", "sent_simulated", "succeeded", "qualified", "active", "auto_handled", "resolved", "drafted", "interested"].includes(status))
    return "good";
  if (["failed", "rejected", "blocked", "cancelled", "bounced", "disqualified", "suppressed", "unsubscribed"].includes(status))
    return "bad";
  if (["escalated", "waiting_approval", "pending", "pending_approval", "retry_wait", "needs_review", "paused", "abstained", "deferred"].includes(status))
    return "warn";
  return "neutral";
}

export function pct(r: Ratio | null | undefined, digits = 0): string {
  if (!r || r.value === null || r.denominator === 0) return "n/a";
  return `${(r.value * 100).toFixed(digits)}%`;
}

export function ratioText(r: Ratio | null | undefined): string {
  if (!r) return "n/a";
  return `${r.numerator} / ${r.denominator}`;
}

export function humanize(s: string | null | undefined): string {
  if (!s) return "";
  const t = s.replace(/[_.]/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export function weightShare(weights: Record<string, number>, key: string): number {
  const total = Object.values(weights).reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
  if (total <= 0) return 0;
  return Math.round(((weights[key] ?? 0) / total) * 1000) / 10;
}

export function validateWeights(weights: Record<string, number>): string | null {
  const vals = Object.values(weights);
  if (vals.some((v) => !Number.isFinite(v) || v < 0)) return "Weights must be numbers ≥ 0.";
  if (vals.reduce((a, b) => a + b, 0) <= 0) return "At least one weight must be above 0.";
  return null;
}

/** Group drafts (or anything with contact_id) by contact while keeping step order. */
export function groupBy<T>(items: T[], key: (t: T) => string): [string, T[]][] {
  const m = new Map<string, T[]>();
  for (const it of items) {
    const k = key(it);
    if (!m.has(k)) m.set(k, []);
    m.get(k)!.push(it);
  }
  return [...m.entries()];
}

export function isTerminalRun(status: string): boolean {
  return ["completed", "failed", "cancelled"].includes(status);
}

/** Whether the UI should keep advancing a run (request-driven engine). */
export function shouldKeepAdvancing(outcome: string, status: string): boolean {
  if (status !== "running") return false;
  return ["budget_exhausted", "backoff", "retry_scheduled", "busy", "step_succeeded"].includes(outcome);
}

/** Human-readable criterion value: arrays joined, strings unquoted. */
export function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "-";
  if (Array.isArray(v)) return v.length ? v.map((x) => humanize(String(x))).join(", ") : "none";
  if (typeof v === "number") return String(v);
  return String(v);
}
