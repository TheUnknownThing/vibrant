"""Gatekeeper prompt construction, execution, and output parsing."""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from vibrant.config import DEFAULT_CONFIG_DIR, find_project_root, load_config
from vibrant.consensus import ConsensusParser, ConsensusWriter, RoadmapParser
from vibrant.models.agent import AgentRecord, AgentStatus, AgentType
from vibrant.models.consensus import ConsensusDocument, ConsensusStatus, DecisionAuthor
from vibrant.providers.base import CanonicalEvent, RuntimeMode
from vibrant.providers.codex.adapter import CodexProviderAdapter

logger = logging.getLogger(__name__)


class GatekeeperTrigger(str, enum.Enum):
    """Supported Gatekeeper invocation triggers."""

    PROJECT_START = "project_start"
    TASK_COMPLETION = "task_completion"
    TASK_FAILURE = "task_failure"
    MAX_RETRIES_EXCEEDED = "max_retries_exceeded"
    USER_CONVERSATION = "user_conversation"


@dataclass(slots=True)
class GatekeeperRequest:
    """Inputs required for one Gatekeeper invocation."""

    trigger: GatekeeperTrigger
    trigger_description: str
    agent_summary: str | None = None


@dataclass(slots=True)
class GatekeeperRunResult:
    """Outcome of one Gatekeeper run."""

    request: GatekeeperRequest
    prompt: str
    transcript: str
    verdict: str | None
    questions: list[str]
    consensus_updated: bool
    roadmap_updated: bool
    plan_modified: bool
    consensus_document: ConsensusDocument | None = None
    roadmap_document: Any | None = None
    events: list[CanonicalEvent] = field(default_factory=list)
    agent_record: AgentRecord | None = None
    error: str | None = None
    turn_result: Any | None = None


@dataclass(slots=True)
class GatekeeperRunHandle:
    """Handle for an in-flight Gatekeeper run."""

    request: GatekeeperRequest
    prompt: str
    agent_record: AgentRecord
    result_future: asyncio.Future[GatekeeperRunResult]

    def done(self) -> bool:
        return self.result_future.done()

    async def wait(self) -> GatekeeperRunResult:
        return await self.result_future


class Gatekeeper:
    """Spawn and supervise the Gatekeeper agent through the provider adapter."""

    VERDICT_PATTERN = re.compile(r"(?im)^(?:verdict|decision)\s*:\s*(?P<value>[a-z0-9_ -]+)$")
    QUESTIONS_SECTION_PATTERN = re.compile(r"^## Questions\s*(?P<body>.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    BLOCKING_QUESTION_PATTERN = re.compile(
        r"^-\s*(?:\[blocking\]\s*)?(?P<question>.+\?)\s*$",
        re.IGNORECASE,
    )
    PRIORITY_QUESTION_PATTERN = re.compile(
        r"^-\s*\*\*Priority\*\*:\s*blocking\s*\|\s*\*\*Question\*\*:\s*(?P<question>.+)$",
        re.IGNORECASE,
    )

    def __init__(
        self,
        project_root: str | Path,
        *,
        adapter_factory: Any | None = None,
        on_canonical_event: Callable[[CanonicalEvent], Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.project_root = find_project_root(project_root)
        self.vibrant_dir = self.project_root / DEFAULT_CONFIG_DIR
        self.consensus_path = self.vibrant_dir / "consensus.md"
        self.roadmap_path = self.vibrant_dir / "roadmap.md"
        self.agents_dir = self.vibrant_dir / "agents"

        self.config = load_config(start_path=self.project_root)
        self.adapter_factory = adapter_factory or CodexProviderAdapter
        self.on_canonical_event = on_canonical_event
        self.timeout_seconds = timeout_seconds or float(self.config.agent_timeout_seconds)
        self.consensus_parser = ConsensusParser()
        self.consensus_writer = ConsensusWriter(parser=self.consensus_parser)
        self.roadmap_parser = RoadmapParser()

    async def run(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
    ) -> GatekeeperRunResult:
        """Run the Gatekeeper for a single trigger and return the parsed outcome."""

        handle = await self.start_run(request, resume_latest_thread=resume_latest_thread)
        return await handle.wait()

    async def start_run(
        self,
        request: GatekeeperRequest,
        *,
        resume_latest_thread: bool | None = None,
        on_result: Callable[[GatekeeperRunResult], Any] | None = None,
    ) -> GatekeeperRunHandle:
        """Start a Gatekeeper run and return once the provider turn has been started."""

        agent_record = self._build_agent_record(request)
        prompt = self.render_prompt(request)
        before_consensus = _read_text(self.consensus_path)
        before_roadmap = _read_text(self.roadmap_path)

        events: list[CanonicalEvent] = []
        transcript_chunks: list[str] = []
        turn_finished = asyncio.Event()
        runtime_error: str | None = None
        adapter: Any | None = None

        async def handle_canonical_event(event: CanonicalEvent) -> None:
            nonlocal runtime_error
            event_copy = dict(event)
            event_copy.setdefault("agent_id", agent_record.agent_id)
            event_copy.setdefault("task_id", agent_record.task_id)
            provider_thread_id = (
                getattr(adapter, "provider_thread_id", None)
                or agent_record.provider.provider_thread_id
                or _extract_provider_thread_id(agent_record.provider.resume_cursor)
            )
            if provider_thread_id:
                event_copy.setdefault("provider_thread_id", provider_thread_id)

            events.append(event_copy)
            event_type = event_copy.get("type")
            if event_type == "content.delta":
                transcript_chunks.append(str(event_copy.get("delta", "")))
            elif event_type == "task.progress":
                text = _extract_text_from_progress_item(event_copy.get("item"))
                if text:
                    transcript_chunks.append(text)
            elif event_type == "runtime.error":
                runtime_error = _extract_error_message(event_copy)
                turn_finished.set()
            elif event_type == "turn.completed":
                turn_finished.set()

            await _maybe_forward_event(self.on_canonical_event, dict(event_copy))

        adapter = self.adapter_factory(
            cwd=str(self.project_root),
            codex_binary=self.config.codex_binary,
            codex_home=self.config.codex_home,
            agent_record=agent_record,
            on_canonical_event=handle_canonical_event,
        )

        turn_result: Any | None = None
        agent_record.started_at = datetime.now(timezone.utc)
        self._persist_agent_record(agent_record)
        should_resume = resume_latest_thread if resume_latest_thread is not None else request.trigger is GatekeeperTrigger.USER_CONVERSATION
        resume_thread_id = self._find_latest_gatekeeper_thread_id() if should_resume else None

        try:
            agent_record.transition_to(AgentStatus.CONNECTING)
            self._persist_agent_record(agent_record)

            await adapter.start_session(cwd=str(self.project_root))
            thread_kwargs = {
                "model": self.config.model,
                "cwd": str(self.project_root),
                "runtime_mode": RuntimeMode.FULL_ACCESS,
                "approval_policy": self.config.approval_policy,
                "reasoning_effort": self.config.reasoning_effort,
                "reasoning_summary": self.config.reasoning_summary,
                "extra_config": self.config.extra_config,
            }
            if resume_thread_id:
                await adapter.resume_thread(resume_thread_id, **thread_kwargs)
            else:
                await adapter.start_thread(**thread_kwargs)

            agent_record.transition_to(AgentStatus.RUNNING)
            self._persist_agent_record(agent_record)

            turn_result = await adapter.start_turn(
                input_items=[{"type": "text", "text": prompt, "text_elements": []}],
                runtime_mode=RuntimeMode.FULL_ACCESS,
                approval_policy=self.config.approval_policy,
            )
        except Exception as exc:
            error_text = runtime_error or str(exc)
            if agent_record.status not in AgentRecord.TERMINAL_STATUSES:
                agent_record.transition_to(AgentStatus.FAILED, error=error_text)
            else:
                agent_record.error = error_text
            self._persist_agent_record(agent_record)
            after_consensus = _read_text(self.consensus_path)
            after_roadmap = _read_text(self.roadmap_path)
            result = self.parse_run_artifacts(
                request=request,
                prompt=prompt,
                transcript="".join(transcript_chunks).strip(),
                before_consensus_text=before_consensus,
                after_consensus_text=after_consensus,
                before_roadmap_text=before_roadmap,
                after_roadmap_text=after_roadmap,
                events=events,
                agent_record=agent_record,
                error=error_text,
                turn_result=turn_result,
            )
            await _stop_adapter_safely(adapter)
            future: asyncio.Future[GatekeeperRunResult] = asyncio.get_running_loop().create_future()
            future.set_result(result)
            return GatekeeperRunHandle(
                request=request,
                prompt=prompt,
                agent_record=agent_record,
                result_future=future,
            )

        async def finalize_run() -> GatekeeperRunResult:
            try:
                await asyncio.wait_for(turn_finished.wait(), timeout=self.timeout_seconds)

                transcript = "".join(transcript_chunks).strip()
                if runtime_error:
                    agent_record.transition_to(AgentStatus.FAILED, error=runtime_error)
                else:
                    agent_record.summary = transcript or None
                    agent_record.transition_to(AgentStatus.COMPLETED)
                self._persist_agent_record(agent_record)

                after_consensus = _read_text(self.consensus_path)
                after_roadmap = _read_text(self.roadmap_path)
                result = self.parse_run_artifacts(
                    request=request,
                    prompt=prompt,
                    transcript=transcript,
                    before_consensus_text=before_consensus,
                    after_consensus_text=after_consensus,
                    before_roadmap_text=before_roadmap,
                    after_roadmap_text=after_roadmap,
                    events=events,
                    agent_record=agent_record,
                    error=runtime_error,
                    turn_result=turn_result,
                )
            except Exception as exc:
                error_text = runtime_error or str(exc)
                if agent_record.status not in AgentRecord.TERMINAL_STATUSES:
                    agent_record.transition_to(AgentStatus.FAILED, error=error_text)
                else:
                    agent_record.error = error_text
                self._persist_agent_record(agent_record)
                after_consensus = _read_text(self.consensus_path)
                after_roadmap = _read_text(self.roadmap_path)
                result = self.parse_run_artifacts(
                    request=request,
                    prompt=prompt,
                    transcript="".join(transcript_chunks).strip(),
                    before_consensus_text=before_consensus,
                    after_consensus_text=after_consensus,
                    before_roadmap_text=before_roadmap,
                    after_roadmap_text=after_roadmap,
                    events=events,
                    agent_record=agent_record,
                    error=error_text,
                    turn_result=turn_result,
                )
            finally:
                await _stop_adapter_safely(adapter)

            await self._emit_run_finished_event(result)
            try:
                await _maybe_forward_result(on_result, result)
            except Exception:
                logger.exception("Gatekeeper result callback failed")
            return result

        result_future = asyncio.create_task(
            finalize_run(),
            name=f"gatekeeper-run-{request.trigger.value}-{agent_record.agent_id}",
        )
        return GatekeeperRunHandle(
            request=request,
            prompt=prompt,
            agent_record=agent_record,
            result_future=result_future,
        )

    async def answer_question(self, question: str, answer: str) -> GatekeeperRunResult:
        """Forward a user answer back into the Gatekeeper conversation."""

        request = GatekeeperRequest(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description=f"Question: {question}\nUser Answer: {answer}",
            agent_summary=answer,
        )
        return await self.run(request, resume_latest_thread=True)

    async def start_answer_question(
        self,
        question: str,
        answer: str,
        *,
        on_result: Callable[[GatekeeperRunResult], Any] | None = None,
    ) -> GatekeeperRunHandle:
        """Start forwarding a user answer back into the Gatekeeper conversation."""

        request = GatekeeperRequest(
            trigger=GatekeeperTrigger.USER_CONVERSATION,
            trigger_description=f"Question: {question}\nUser Answer: {answer}",
            agent_summary=answer,
        )
        return await self.start_run(request, resume_latest_thread=True, on_result=on_result)

    def render_prompt(self, request: GatekeeperRequest) -> str:
        """Render the Gatekeeper prompt template from the spec."""

        consensus_text = _read_text(self.consensus_path) or "No consensus document exists yet."
        consensus_contract_text = _render_consensus_contract()
        skills_text = self._render_available_skills()
        summary_text = request.agent_summary.strip() if request.agent_summary else "N/A"

        return "\n".join(
            [
                f"You are the Gatekeeper for Project {self.project_root.name}. You are the sole authority over the project plan.",
                "## Your Responsibilities",
                "1. Evaluate agent output against the plan's acceptance criteria.",
                "2. Update .vibrant/consensus.md when tasks are completed or when the plan needs adjustment.",
                "3. If an agent failed, analyze the failure and modify the task's prompt or acceptance criteria.",
                "4. If you encounter a high-level decision (product direction, UX, architecture), ask the user",
                "   by adding a question to the Questions section of consensus.md.",
                "   Questions will block progress on their own, so only use a blocking question when the work truly cannot proceed",
                "   without a user-level decision.",
                "5. If the decision is purely technical, make it yourself and log it in the Decisions section.",
                "## Consensus Contract",
                consensus_contract_text,
                "## Current Consensus",
                consensus_text,
                "## Trigger",
                f"{request.trigger.value}: {request.trigger_description}",
                "## Agent Summary (if applicable)",
                summary_text,
                "## Rules",
                "1. Always update consensus.md directly — it is the source of truth.",
                "2. Increment the version number in META on every update.",
                "3. Never remove completed decisions from the log.",
                "4. When re-planning a failed task, keep the failure history in Gatekeeper Notes.",
                "5. You have read/write access to the .vibrant/ directory ONLY.",
                "## Available Skills",
                "The following skills are available for agents. Assign them to tasks as needed:",
                skills_text,
            ]
        )

    def parse_run_artifacts(
        self,
        *,
        request: GatekeeperRequest,
        prompt: str,
        transcript: str,
        before_consensus_text: str | None,
        after_consensus_text: str | None,
        before_roadmap_text: str | None,
        after_roadmap_text: str | None,
        events: list[CanonicalEvent],
        agent_record: AgentRecord,
        error: str | None,
        turn_result: Any,
    ) -> GatekeeperRunResult:
        """Interpret the Gatekeeper transcript and resulting file writes."""

        consensus_updated = before_consensus_text != after_consensus_text
        roadmap_updated = before_roadmap_text != after_roadmap_text
        consensus_document = self._parse_consensus_text(after_consensus_text)
        roadmap_document = self._parse_roadmap_text(after_roadmap_text)
        verdict = self._extract_verdict(transcript)
        questions = consensus_document.questions if consensus_document is not None else self._extract_blocking_questions(after_consensus_text)
        if verdict is None and questions:
            verdict = "needs_input"

        return GatekeeperRunResult(
            request=request,
            prompt=prompt,
            transcript=transcript,
            verdict=verdict,
            questions=questions,
            consensus_updated=consensus_updated,
            roadmap_updated=roadmap_updated,
            plan_modified=roadmap_updated,
            consensus_document=consensus_document,
            roadmap_document=roadmap_document,
            events=list(events),
            agent_record=agent_record,
            error=error,
            turn_result=turn_result,
        )

    def _render_available_skills(self) -> str:
        skills_dir = self.vibrant_dir / "skills"
        if not skills_dir.exists():
            return "- No project-specific skills available."

        entries: list[str] = []
        for path in sorted(item for item in skills_dir.iterdir() if item.is_file()):
            description = _extract_skill_description(path)
            entries.append(f"- {path.stem}: {description}")
        return "\n".join(entries) if entries else "- No project-specific skills available."

    def _build_agent_record(self, request: GatekeeperRequest) -> AgentRecord:
        agent_id = f"gatekeeper-{request.trigger.value}-{uuid4().hex[:8]}"
        task_id = f"gatekeeper-{request.trigger.value}"
        return AgentRecord(
            agent_id=agent_id,
            task_id=task_id,
            type=AgentType.GATEKEEPER,
            worktree_path=str(self.project_root),
            prompt_used=request.trigger_description,
        )

    def _persist_agent_record(self, agent_record: AgentRecord) -> None:
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        path = self.agents_dir / f"{agent_record.agent_id}.json"
        _atomic_write_text(path, agent_record.model_dump_json(indent=2) + "\n")

    def _parse_consensus_text(self, text: str | None) -> ConsensusDocument | None:
        if text is None or not text.strip():
            return None
        try:
            return self.consensus_parser.parse(text)
        except Exception:
            return None

    def _parse_roadmap_text(self, text: str | None) -> Any | None:
        if text is None or not text.strip():
            return None
        try:
            return self.roadmap_parser.parse(text)
        except Exception:
            return None

    def _extract_verdict(self, transcript: str) -> str | None:
        match = self.VERDICT_PATTERN.search(transcript)
        if match is None:
            return None
        return match.group("value").strip().lower().replace(" ", "_")

    def _extract_blocking_questions(self, consensus_text: str | None) -> list[str]:
        if consensus_text is None:
            return []
        section_match = self.QUESTIONS_SECTION_PATTERN.search(consensus_text)
        if section_match is None:
            return []

        questions: list[str] = []
        for raw_line in section_match.group("body").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            priority_match = self.PRIORITY_QUESTION_PATTERN.match(line)
            if priority_match is not None:
                questions.append(priority_match.group("question").strip())
                continue
            blocking_match = self.BLOCKING_QUESTION_PATTERN.match(line)
            if blocking_match is not None and "blocking" in line.lower():
                question = blocking_match.group("question").strip()
                question = re.sub(r"^\[blocking\]\s*", "", question, flags=re.IGNORECASE)
                questions.append(question)
        return questions

    def _find_latest_gatekeeper_thread_id(self) -> str | None:
        if not self.agents_dir.exists():
            return None

        latest_record: AgentRecord | None = None
        latest_sort_key: tuple[datetime, datetime] | None = None
        for path in self.agents_dir.glob("gatekeeper-*.json"):
            try:
                record = AgentRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            thread_id = record.provider.provider_thread_id or _extract_provider_thread_id(record.provider.resume_cursor)
            if not thread_id:
                continue
            started = record.started_at or datetime.min.replace(tzinfo=timezone.utc)
            finished = record.finished_at or started
            sort_key = (started, finished)
            if latest_sort_key is None or sort_key > latest_sort_key:
                latest_record = record
                latest_sort_key = sort_key

        if latest_record is None:
            return None
        return latest_record.provider.provider_thread_id or _extract_provider_thread_id(latest_record.provider.resume_cursor)

    async def _emit_run_finished_event(self, result: GatekeeperRunResult) -> None:
        event: CanonicalEvent = {
            "type": "gatekeeper.run.finished",
            "timestamp": _timestamp_now(),
            "provider": "codex",
            "request_trigger": result.request.trigger.value,
            "verdict": result.verdict,
            "questions": list(result.questions),
            "consensus_updated": result.consensus_updated,
            "roadmap_updated": result.roadmap_updated,
            "plan_modified": result.plan_modified,
            "error": result.error,
        }
        if result.agent_record is not None:
            event["agent_id"] = result.agent_record.agent_id
            event["task_id"] = result.agent_record.task_id
            provider_thread_id = (
                result.agent_record.provider.provider_thread_id
                or _extract_provider_thread_id(result.agent_record.provider.resume_cursor)
            )
            if provider_thread_id:
                event["provider_thread_id"] = provider_thread_id
        await _maybe_forward_event(self.on_canonical_event, event)


async def _stop_adapter_safely(adapter: Any) -> None:
    try:
        await adapter.stop_session()
    except Exception:
        return


def _extract_skill_description(path: Path) -> str:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        return line
    return "No description provided."


def _render_consensus_contract() -> str:
    status_values = ", ".join(status.value for status in ConsensusStatus)
    decision_authors = ", ".join(author.value for author in DecisionAuthor)
    return "\n".join(
        [
            "- `ConsensusDocument` fields: `project`, `created_at`, `updated_at`, `version`, `status`, `objectives`, `decisions`, `getting_started`, `questions`.",
            f"- `status` must be one of: {status_values}.",
            "- `decisions` must be a list of structured entries with: `title`, `date`, `made_by`, `context`, `resolution`, `impact`.",
            f"- `made_by` must be one of: {decision_authors}.",
            "- `questions` must contain only the user questions that still require an answer.",
            "- A blocking question is not a workflow state. It blocks execution by being unresolved, so use it only when necessary.",
            "- `ConsensusPool` is just a backward-compatible alias of `ConsensusDocument`; keep writing the same structure to `consensus.md`.", # TODO: maybe remove the alias and update the spec to just refer to ConsensusDocument
        ]
    )


def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def _extract_text_from_progress_item(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    if not _is_assistant_progress_item(item):
        return ""
    if isinstance(item.get("text"), str):
        return item["text"]
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [entry.get("text", "") for entry in content if isinstance(entry, dict)]
        return "".join(part for part in parts if part)
    return ""


def _is_assistant_progress_item(item: dict[str, Any]) -> bool:
    item_type = _normalize_progress_item_token(item.get("type"))
    if item_type in {"agentmessage", "assistantmessage"}:
        return True

    role = _normalize_progress_item_token(item.get("role"))
    if role in {"assistant", "agent", "model"}:
        return True

    author = _normalize_progress_item_token(item.get("author"))
    return author in {"assistant", "agent", "model"}


def _normalize_progress_item_token(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _extract_error_message(event: CanonicalEvent) -> str:
    error = event.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    if error is None:
        return "Gatekeeper runtime error"
    return str(error)


def _extract_provider_thread_id(resume_cursor: object) -> str | None:
    if not isinstance(resume_cursor, dict):
        return None
    thread_id = resume_cursor.get("threadId")
    return thread_id if isinstance(thread_id, str) and thread_id else None


async def _maybe_forward_event(
    callback: Callable[[CanonicalEvent], Any] | None,
    event: CanonicalEvent,
) -> None:
    if callback is None:
        return
    result = callback(event)
    if asyncio.iscoroutine(result):
        await result


async def _maybe_forward_result(
    callback: Callable[[GatekeeperRunResult], Any] | None,
    result: GatekeeperRunResult,
) -> None:
    if callback is None:
        return
    callback_result = callback(result)
    if asyncio.iscoroutine(callback_result):
        await callback_result


def _timestamp_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
