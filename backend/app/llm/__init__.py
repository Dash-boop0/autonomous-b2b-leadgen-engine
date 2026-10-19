"""LLM provider adapters. The LLM is used only to synthesise, classify and draft; it never makes decisions that
scoring, suppression, permissions or approvals depend on."""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..config import get_settings

log = logging.getLogger(__name__)


@dataclass
class LLMResult:
    text: str
    provider: str
    mock: bool
    data: dict = field(default_factory=dict)
    confidence: float | None = None


class LLMProvider(ABC):
    name: str = "base"
    mock: bool = False

    @abstractmethod
    def generate(self, task: str, payload: dict) -> LLMResult: ...


# ---------------------------------------------------------------- deterministic mock

REPLY_RULES: list[tuple[str, list[str], float]] = [
    (
        "unsubscribe",
        [r"\bunsubscribe\b", r"remove me", r"stop (emailing|contacting)", r"do not contact", r"opt[- ]?out"],
        0.95,
    ),
    (
        "out_of_office",
        [r"out of (the )?office", r"on (annual )?leave", r"auto(matic)?[- ]?reply", r"returning on"],
        0.92,
    ),
    ("bounce_notice", [r"undeliverable", r"delivery (has )?failed", r"mailbox unavailable"], 0.9),
    (
        "question_legal",
        [r"\bdpa\b", r"contract", r"\blegal\b", r"liabilit", r"gdpr", r"indemn", r"terms and conditions"],
        0.85,
    ),
    ("question_pricing", [r"pric", r"cost", r"how much", r"discount", r"budget", r"quote"], 0.85),
    ("referral", [r"(speak|talk) (to|with) my colleague", r"better person", r"cc'?d", r"forwarding (this|you)"], 0.8),
    ("not_interested", [r"not interested", r"no thanks", r"not a fit", r"already (use|have)", r"not for us"], 0.85),
    (
        "interested",
        [
            r"\binterested\b",
            r"(book|schedule|set up) a (call|meeting|demo)",
            r"sounds (good|useful)",
            r"let'?s talk",
            r"happy to (chat|talk)",
        ],
        0.82,
    ),
    (
        "question_product",
        [r"\bintegrat", r"does (it|northwind)", r"can (it|you)", r"\bsecurity\b", r"\bhow does\b", r"\bsupport\b"],
        0.75,
    ),
]


class MockLLM(LLMProvider):
    """Deterministic rules and templates. Labelled "mock LLM" wherever its output is shown."""

    name = "mock-llm"
    mock = True

    def generate(self, task: str, payload: dict) -> LLMResult:
        fn = getattr(self, f"_t_{task}", None)
        if fn is None:
            raise ValueError(f"MockLLM has no template for task {task}")
        return fn(payload)

    def _r(self, text: str, data: dict | None = None, confidence: float | None = None) -> LLMResult:
        return LLMResult(text=text, provider=self.name, mock=True, data=data or {}, confidence=confidence)

    def _t_summarise_segment(self, p: dict) -> LLMResult:
        return self._r(
            f"[mock LLM] {p['segment']}: {p['count']} matching account(s) in permitted sources. "
            f"Notes cite: {'; '.join(p.get('notes', [])[:2]) or 'no supporting notes found'}."
        )

    def _t_persona_summary(self, p: dict) -> LLMResult:
        return self._r(f"[mock LLM] {p['name']} ({p['buyer_role']}) cares about: {', '.join(p['pains'][:3])}.")

    def _t_competitor_summary(self, p: dict) -> LLMResult:
        return self._r(f"[mock LLM] Gap vs {p['competitor']}: {p['gap']}")

    def _t_icp_rationale(self, p: dict) -> LLMResult:
        return self._r(
            "[mock LLM] Draft ICP built from the strongest research segments ("
            + ", ".join(p["industries"])
            + f") in {', '.join(p['countries'])}; buyer roles prioritised: {', '.join(p['roles'])}. "
            "Weights are a starting point for human review, not learned from outcomes."
        )

    def _t_explain_score(self, p: dict) -> LLMResult:
        top = sorted(p["criteria"], key=lambda c: -c["contribution"])[:3]
        miss = [c["name"] for c in p["criteria"] if c["missing"]]
        dq = p.get("hard_disqualifiers") or []
        s = f"[mock LLM] Score {p['total']}/100 (tier {p['tier']}). Strongest: " + ", ".join(
            f"{c['name']} +{c['contribution']}" for c in top
        )
        if miss:
            s += f". Missing data: {', '.join(miss)}"
        if dq:
            s += ". Disqualified: " + "; ".join(d["reason"] for d in dq)
        return self._r(s + ".")

    def _t_draft_email(self, p: dict) -> LLMResult:
        first = p["first_name"]
        if p["step"] == 1:
            if p.get("evidence"):
                hook = p["evidence"][0]["claim"]
                subject = f"{p['company']}: {p['angle']}"
                opener = f"I noticed {hook.rstrip('.')}."
            else:
                subject = f"Demand planning at {p['company']}"
                opener = f"I work with {p['industry'] or 'operations'} teams on demand planning."
            body = (
                f"Hi {first},\n\n{opener} {p['value_prop']}\n\n"
                f"Would a 20-minute call in the next couple of weeks be useful to see whether it fits {p['company']}?\n\n"
                f"Best,\n{p['sender']}"
            )
        else:
            subject = f"Re: {p['prev_subject']}"
            body = (
                f"Hi {first},\n\nA short follow-up in case my last note got buried. "
                f"If demand planning isn't a priority for {p['company']} right now, just say so and I won't follow up again.\n\n"
                f"Best,\n{p['sender']}"
            )
        return self._r(body, {"subject": subject})

    def _t_classify_reply(self, p: dict) -> LLMResult:
        text = p["body"].lower()
        hits = []
        for label, pats, conf in REPLY_RULES:
            if any(re.search(pt, text) for pt in pats):
                hits.append((label, conf))
        if not hits:
            return self._r("unknown", {"label": "unknown", "alternatives": []}, 0.3)
        label, conf = hits[0]
        if len(hits) > 1 and label not in ("unsubscribe", "out_of_office", "bounce_notice"):
            conf = round(conf - 0.15, 2)  # mixed signals lower confidence
        return self._r(label, {"label": label, "alternatives": [h[0] for h in hits[1:]]}, conf)

    def _t_draft_response(self, p: dict) -> LLMResult:
        passages = p["passages"]
        lines = [f"Hi {p['first_name']},", "", "Thanks for the question."]
        for ps in passages[:2]:
            lines.append(f"{ps['text']} [{ps['id']}]")
        lines += ["", "Happy to go deeper on a short call.", "", f"Best,\n{p['sender']}"]
        return self._r("\n".join(lines))

    def _t_recommendation_summary(self, p: dict) -> LLMResult:
        return self._r(f"[mock LLM] {p['recommendation']} Evidence: {p['evidence']}.")

    def _t_analytics_narrative(self, p: dict) -> LLMResult:
        r = p["rates"]

        def pct(k):
            v = r[k]["value"]
            return "n/a" if v is None else f"{v * 100:.0f}%"

        return self._r(
            f"[mock LLM] Simulated campaign: {p['counts']['contacts_sent']} contacts sent; delivery {pct('delivery_rate')}, "
            f"reply {pct('reply_rate')}, positive {pct('positive_reply_rate')}, unsubscribe {pct('unsubscribe_rate')}. "
            "These are synthetic dry-run outcomes, not real results."
        )


# ---------------------------------------------------------------- optional real providers (untested)


def _prompt(task: str, payload: dict) -> str:
    return (
        f"Task: {task}\nInput JSON:\n{json.dumps(payload, default=str)[:12000]}\n\n"
        "Respond with a single JSON object with keys: text (string), data (object), confidence (0-1). "
        "Only use facts present in the input. If the input is insufficient, say so in text and set confidence below 0.5."
    )


def _parse(raw: str, name: str) -> LLMResult | None:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return LLMResult(
        text=str(obj.get("text", "")),
        provider=name,
        mock=False,
        data=obj.get("data") or {},
        confidence=obj.get("confidence"),
    )


class _FallbackMixin:
    def _fallback(self, task: str, payload: dict, why: str) -> LLMResult:
        log.warning("LLM provider %s failed for %s (%s); falling back to mock", getattr(self, "name", "?"), task, why)
        r = MockLLM().generate(task, payload)
        r.data = {**r.data, "fallback_reason": why}
        return r


class OllamaProvider(_FallbackMixin, LLMProvider):  # pragma: no cover - optional, not exercised in tests
    name = "ollama"
    mock = False

    def generate(self, task: str, payload: dict) -> LLMResult:
        import httpx

        s = get_settings()
        try:
            r = httpx.post(
                f"{s.ollama_url}/api/generate",
                json={"model": s.ollama_model, "prompt": _prompt(task, payload), "stream": False, "format": "json"},
                timeout=60,
            )
            r.raise_for_status()
            return _parse(r.json().get("response", ""), self.name) or self._fallback(task, payload, "unparseable")
        except Exception as e:  # noqa: BLE001
            return self._fallback(task, payload, str(e))


class AnthropicProvider(_FallbackMixin, LLMProvider):  # pragma: no cover - optional, not exercised in tests
    """Uses the official `anthropic` SDK (install the `anthropic` extra). Refusals fall back server-side
    (fallbacks="default"); a final refusal or any error falls back to the mock provider and is labelled."""

    name = "anthropic"
    mock = False

    def generate(self, task: str, payload: dict) -> LLMResult:
        s = get_settings()
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=s.anthropic_api_key)
            resp = client.beta.messages.create(
                model=s.anthropic_model,
                max_tokens=2048,
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},
                output_config={"effort": "low"},
                system="You assist a B2B sales-ops tool. Never invent facts, prices, customers or results.",
                messages=[{"role": "user", "content": _prompt(task, payload)}],
            )
            if resp.stop_reason == "refusal":
                return self._fallback(task, payload, "refusal")
            text = "".join(b.text for b in resp.content if b.type == "text")
            return _parse(text, self.name) or self._fallback(task, payload, "unparseable")
        except Exception as e:  # noqa: BLE001
            return self._fallback(task, payload, str(e))


def get_llm() -> LLMProvider:
    p = get_settings().llm_provider
    if p == "ollama":
        return OllamaProvider()
    if p == "anthropic" and get_settings().anthropic_api_key:
        return AnthropicProvider()
    return MockLLM()
