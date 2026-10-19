"""Agent framework: typed I/O, scoped tools, evidence, confidence thresholds, retries and completion criteria.

Every agent declares:
  * input_model / output_model (Pydantic)   - validated on entry and exit
  * tools                                   - the only tool names it may call (ToolNotPermitted otherwise)
  * confidence_threshold                    - below it the output is marked needs_review (escalate) or abstained
  * max_retries                             - in-agent retries for TransientAgentError (the engine adds step retries)
  * completion_criteria                     - human-readable + check_completion() returns unmet criteria
Every run writes an audit event with the evidence ids and tool calls it made."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit
from ..llm import LLMProvider, LLMResult, get_llm
from ..models import Evidence, SourceRecord, utcnow
from ..normalise import raw_hash
from ..security import agent_actor


class ToolNotPermitted(PermissionError):
    pass


class TransientAgentError(RuntimeError):
    pass


class AgentIncomplete(RuntimeError):
    pass


class AgentOutput(BaseModel):
    status: Literal["completed", "needs_review", "abstained"] = "completed"
    confidence: float = Field(1.0, ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    tool_calls: list[str] = Field(default_factory=list)
    llm_provider: str | None = None


@dataclass
class AgentContext:
    session: Session
    run_id: str | None = None
    step_id: str | None = None
    initiated_by: str | None = None
    llm: LLMProvider = field(default_factory=get_llm)


class BaseAgent:
    name: ClassVar[str]
    group: ClassVar[str]
    description: ClassVar[str]
    tools: ClassVar[tuple[str, ...]]
    input_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[AgentOutput]]
    confidence_threshold: ClassVar[float] = 0.6
    max_retries: ClassVar[int] = 2
    completion_criteria: ClassVar[str] = ""
    abstain_below_threshold: ClassVar[bool] = False

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx
        self.session = ctx.session
        self.actor = agent_actor(self.name)
        self._tool_calls: list[str] = []
        self._evidence: list[str] = []
        self._llm_provider: str | None = None

    # ------------------------------------------------------------ tools

    def tool(self, name: str, **kwargs) -> Any:
        if name not in self.tools:
            audit.record(
                self.session,
                self.actor,
                "agent.tool_denied",
                "agent",
                self.name,
                after={"tool": name},
                run_id=self.ctx.run_id,
                step_id=self.ctx.step_id,
                severity="warn",
            )
            raise ToolNotPermitted(f"{self.name} may not call tool '{name}'")
        from .tools import TOOLS

        self._tool_calls.append(name)
        return TOOLS[name](self, **kwargs)

    def llm(self, task: str, payload: dict) -> LLMResult:
        r: LLMResult = self.tool("llm.generate", task=task, payload=payload)
        self._llm_provider = r.provider
        return r

    def add_evidence(
        self,
        entity_type: str,
        entity_id: str,
        claim: str,
        excerpt: str = "",
        source_id: str | None = None,
        confidence: float = 0.6,
        kind: str = "fact",
    ) -> str:
        ev = Evidence(
            entity_type=entity_type,
            entity_id=entity_id,
            claim=claim,
            excerpt=excerpt[:1000],
            source_id=source_id,
            confidence=confidence,
            created_by="agent",
            agent=self.name,
            kind=kind,
        )
        self.session.add(ev)
        self.session.flush()
        self._evidence.append(ev.id)
        return ev.id

    def source_record(
        self,
        adapter: str,
        source_type: str,
        ref: str,
        payload: dict,
        confidence: float,
        terms_note: str = "",
        retrieved_at=None,
    ) -> SourceRecord:
        """Idempotent provenance record keyed by (adapter, hash(payload))."""
        h = raw_hash(payload)
        from sqlalchemy import select

        rec = self.session.scalars(
            select(SourceRecord).where(SourceRecord.adapter == adapter, SourceRecord.raw_hash == h)
        ).first()
        if rec is None:
            rec = SourceRecord(
                adapter=adapter,
                source_type=source_type,
                url_or_ref=ref,
                confidence=confidence,
                raw_hash=h,
                terms_note=terms_note,
                payload=payload,
                retrieved_at=retrieved_at or utcnow(),
            )
            self.session.add(rec)
            self.session.flush()
        return rec

    # ------------------------------------------------------------ lifecycle

    def run(self, inp: BaseModel) -> AgentOutput:  # pragma: no cover - abstract
        raise NotImplementedError

    def check_completion(self, out: AgentOutput) -> list[str]:
        return []

    def execute(self, raw_input: dict | BaseModel) -> AgentOutput:
        inp = raw_input if isinstance(raw_input, BaseModel) else self.input_model.model_validate(raw_input)
        attempt = 0
        started = time.monotonic()
        while True:
            attempt += 1
            try:
                out = self.output_model.model_validate(self.run(inp).model_dump())
                break
            except TransientAgentError as e:
                if attempt > self.max_retries:
                    self._fail(e, attempt)
                    raise
                audit.record(
                    self.session,
                    self.actor,
                    f"agent.{self.name}.retry",
                    "agent",
                    self.name,
                    after={"attempt": attempt, "error": str(e)},
                    run_id=self.ctx.run_id,
                    step_id=self.ctx.step_id,
                    severity="warn",
                )
                time.sleep(min(0.05 * 2**attempt, 0.5))
            except Exception as e:
                self._fail(e, attempt)
                raise
        unmet = self.check_completion(out)
        if unmet:
            err = AgentIncomplete(f"{self.name} completion criteria not met: {'; '.join(unmet)}")
            self._fail(err, attempt)
            raise err
        if out.confidence < self.confidence_threshold and out.status == "completed":
            out.status = "abstained" if self.abstain_below_threshold else "needs_review"
            out.notes.append(
                f"confidence {out.confidence:.2f} < threshold {self.confidence_threshold:.2f}: {out.status}"
            )
        out.evidence_ids = list(dict.fromkeys([*out.evidence_ids, *self._evidence]))
        out.tool_calls = sorted(set(self._tool_calls))
        out.llm_provider = out.llm_provider or self._llm_provider
        audit.record(
            self.session,
            self.actor,
            f"agent.{self.name}.completed",
            "agent",
            self.name,
            after={
                "status": out.status,
                "confidence": out.confidence,
                "tools": out.tool_calls,
                "llm": out.llm_provider,
                "attempts": attempt,
                "ms": int((time.monotonic() - started) * 1000),
                "notes": out.notes[:5],
            },
            evidence_ids=out.evidence_ids[:200],
            run_id=self.ctx.run_id,
            step_id=self.ctx.step_id,
        )
        return out

    def _fail(self, e: Exception, attempt: int) -> None:
        audit.record(
            self.session,
            self.actor,
            f"agent.{self.name}.failed",
            "agent",
            self.name,
            after={"error": f"{e.__class__.__name__}: {e}", "attempts": attempt},
            run_id=self.ctx.run_id,
            step_id=self.ctx.step_id,
            severity="error",
        )

    @classmethod
    def describe(cls) -> dict:
        return {
            "name": cls.name,
            "group": cls.group,
            "description": cls.description,
            "tools": list(cls.tools),
            "confidence_threshold": cls.confidence_threshold,
            "max_retries": cls.max_retries,
            "completion_criteria": cls.completion_criteria,
            "input_schema": cls.input_model.model_json_schema(),
            "output_schema": cls.output_model.model_json_schema(),
        }
