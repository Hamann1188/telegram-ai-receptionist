"""The agent loop: one patient message in, one reply out.

History is append-only. Every message goes to the API exactly as stored, and the
assistant's content blocks are stored exactly as the API returned them, thinking
blocks included. The system prompt and tool list are fixed per session; a change
starts a new session. Claude Opus 5.5 rejects edited history on accounts created
after 2026-08-31, and an untouched prefix keeps the prompt cache warm.
"""

import asyncio
import hashlib
import json
import logging
import re
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import anthropic

from receptionist.core.clinic import Clinic
from receptionist.core.ports import ConversationStore, SessionState
from receptionist.core.prompts import SUMMARY_SYSTEM, context_block, fallback_text
from receptionist.core.tools import ToolContext, run_tool, tool_definitions

logger = logging.getLogger(__name__)

# Fragments of the model's internal markup that occasionally leak into reply text,
# e.g. "antml_reply_Здравствуйте!". Once one appears in a session the model tends to
# repeat it, because it sees its own earlier replies.
_LEAKED_MARKUP = re.compile(r"</?antml[:_][^>\s]*>|antml_[a-z]+_?|antml:[a-z_]+")


def clean_reply_text(text: str) -> str:
    """Remove leaked internal markup from a reply before it reaches the patient.

    Only the delivered text is cleaned; stored history stays verbatim.
    """
    return _LEAKED_MARKUP.sub("", text).strip()


# USD per million tokens: input, output, cache read. Cache writes cost 1.25x input.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
}


@dataclass(frozen=True)
class AgentConfig:
    model: str
    effort: str = "low"
    max_tokens: int = 8000
    max_iterations: int = 6  # Claude calls per patient message
    idle_timeout: timedelta = timedelta(hours=12)
    context_budget_tokens: int = 30_000  # rotate the session with a summary above this


@dataclass
class AgentReply:
    text: str
    outcome: str  # answered | refusal | incomplete | error
    handoff: bool = False
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0


def block_to_param(block) -> dict:
    """A response content block as JSON, serialized the way the SDK sends it back."""
    return block.model_dump(
        mode="json",
        exclude_unset=True,
        by_alias=True,
        exclude=getattr(block, "__api_exclude__", None),
    )


class Assistant:
    def __init__(
        self,
        client,
        config: AgentConfig,
        clinic: Clinic,
        system_prompt: str,
        store: ConversationStore,
        tool_context: Callable[[int], ToolContext],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._config = config
        self._clinic = clinic
        self._system = system_prompt
        self._tools = tool_definitions(clinic)
        self._store = store
        self._tool_context = tool_context
        self._clock = clock
        self._locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()
        self.fingerprint = hashlib.sha256(
            json.dumps(
                {"model": config.model, "system": system_prompt, "tools": self._tools},
                ensure_ascii=False,
            ).encode()
        ).hexdigest()

    def lock(self, chat_id: int) -> asyncio.Lock:
        """The per-chat lock that serializes turns. Hold it to change a chat's data
        without racing a reply (e.g. /forget)."""
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = self._locks[chat_id] = asyncio.Lock()
        return lock

    async def reset(self, chat_id: int) -> None:
        """End the chat's session, e.g. on /start; the next message starts fresh."""
        async with self.lock(chat_id):
            session = await self._store.current_session(chat_id)
            if session is not None:
                await self._store.end_session(session.id, None, self._clock())

    async def reply(self, chat_id: int, text: str, language: str | None = None) -> AgentReply:
        # One turn at a time per chat: messages are appended in order.
        async with self.lock(chat_id):
            return await self._reply(chat_id, text, language)

    async def _reply(self, chat_id: int, text: str, language: str | None) -> AgentReply:
        now = self._clock()
        usage = _Usage()
        session, summary = await self._session(chat_id, now, usage)

        user_content = []
        if summary:
            user_content.append(
                {
                    "type": "text",
                    "text": f"<previous_conversation_summary>{summary}"
                    "</previous_conversation_summary>",
                }
            )
        user_content.append({"type": "text", "text": context_block(self._clinic, now, language)})
        user_content.append({"type": "text", "text": text})
        messages = [*session.messages, {"role": "user", "content": user_content}]
        await self._store.append(session.id, "user", user_content, None, now)

        tools = self._tool_context(chat_id)
        handoff = False
        for _ in range(self._config.max_iterations):
            try:
                response = await self._client.messages.create(
                    model=self._config.model,
                    max_tokens=self._config.max_tokens,
                    system=self._system,
                    tools=self._tools,
                    messages=messages,
                    output_config={"effort": self._config.effort},
                    cache_control={"type": "ephemeral"},
                )
            except anthropic.APIError as exc:
                logger.error("Claude API error: %s", type(exc).__name__, exc_info=True)
                return self._finish("error", language, usage, handoff)
            usage.add(response)

            if response.stop_reason == "refusal":
                # A declined turn may hold partial blocks; don't keep it, start fresh.
                await self._store.end_session(session.id, None, self._clock())
                return self._finish("refusal", language, usage, handoff)

            content = [block_to_param(block) for block in response.content]
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "max_tokens" and tool_uses:
                # A tool call cut off mid-input must not run or be stored unanswered.
                await self._store.end_session(session.id, None, self._clock())
                return self._finish("incomplete", language, usage, handoff)

            messages.append({"role": "assistant", "content": content})
            await self._store.append(
                session.id, "assistant", content, _usage_dict(response), self._clock()
            )

            if response.stop_reason != "tool_use" or not tool_uses:
                raw = "\n\n".join(
                    b.text.strip() for b in response.content if b.type == "text" and b.text.strip()
                )
                reply_text = clean_reply_text(raw)
                if reply_text != raw.strip():
                    logger.warning("Removed leaked internal markup from a reply")
                if not reply_text:
                    # A reply holding only thinking is no use to the patient, and an
                    # assistant turn without text or tool calls is risky to replay.
                    await self._store.end_session(session.id, None, self._clock())
                    return self._finish("incomplete", language, usage, handoff)
                return self._finish("answered", language, usage, handoff, reply_text)

            results = []
            for block in tool_uses:
                result = await run_tool(block.name, block.input, tools)
                handoff = handoff or result.handoff
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                )
            messages.append({"role": "user", "content": results})
            await self._store.append(session.id, "user", results, None, self._clock())

        logger.warning("Agent loop hit %d iterations", self._config.max_iterations)
        return self._finish("incomplete", language, usage, handoff)

    async def _session(
        self, chat_id: int, now: datetime, usage: "_Usage"
    ) -> tuple[SessionState, str | None]:
        """The open session, or a new one when it is stale. Returns (session, summary)."""
        current = await self._store.current_session(chat_id)
        summary = None
        if current is not None:
            if current.fingerprint != self.fingerprint:
                reason = "prompt changed"
            elif now - current.last_activity > self._config.idle_timeout:
                reason = "idle"
            elif current.context_tokens > self._config.context_budget_tokens:
                reason = "context budget"
                summary = await self._summarize(current, usage)
            else:
                return current, None
            logger.info("Rotating session %s: %s", current.id, reason)
            await self._store.end_session(current.id, summary, now)
        return await self._store.start_session(chat_id, self.fingerprint, now), summary

    async def _summarize(self, session: SessionState, usage: "_Usage") -> str | None:
        try:
            response = await self._client.messages.create(
                model=self._config.model,
                max_tokens=2000,
                system=SUMMARY_SYSTEM,
                messages=[{"role": "user", "content": render_transcript(session.messages)}],
                output_config={"effort": "low"},
            )
        except anthropic.APIError:
            logger.warning("Session summary failed", exc_info=True)
            return None
        usage.add(response)
        if response.stop_reason == "refusal":
            return None
        text = " ".join(b.text.strip() for b in response.content if b.type == "text")
        return text or None

    def _finish(
        self,
        outcome: str,
        language: str | None,
        usage: "_Usage",
        handoff: bool,
        text: str | None = None,
    ) -> AgentReply:
        reply = AgentReply(
            text=text or fallback_text(outcome, language, self._clinic),
            outcome=outcome,
            handoff=handoff,
            usage=usage.totals,
            cost_usd=usage.cost,
        )
        logger.info(
            "Turn %s: calls=%d in=%d cache_read=%d out=%d cost=$%.4f",
            outcome,
            usage.calls,
            usage.totals["input_tokens"],
            usage.totals["cache_read_input_tokens"],
            usage.totals["output_tokens"],
            usage.cost,
        )
        return reply


def render_transcript(messages: list[dict], limit: int = 1500) -> str:
    """Plain-text transcript of a session for the summary call."""
    lines = []
    for message in messages:
        for block in message["content"]:
            kind = block.get("type")
            if kind == "text" and block["text"].strip():
                speaker = "Patient" if message["role"] == "user" else "Receptionist"
                lines.append(f"{speaker}: {block['text'].strip()[:limit]}")
            elif kind == "tool_use":
                arguments = json.dumps(block.get("input", {}), ensure_ascii=False)
                lines.append(f"[tool call {block['name']} {arguments[:limit]}]")
            elif kind == "tool_result":
                lines.append(f"[tool result {str(block.get('content'))[:limit]}]")
    return "\n".join(lines) or "(empty conversation)"


def _usage_dict(response) -> dict:
    u = response.usage
    return {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_input_tokens": u.cache_read_input_tokens or 0,
        "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
    }


class _Usage:
    def __init__(self) -> None:
        self.calls = 0
        self.cost = 0.0
        self.totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

    def add(self, response) -> None:
        self.calls += 1
        current = _usage_dict(response)
        for key, value in current.items():
            self.totals[key] += value
        self.cost += usage_cost(response.model, current)


def usage_cost(model: str, usage: dict) -> float:
    """USD for one call's usage; 0 for a model without a price entry."""
    prices = PRICES.get(model)
    if not prices:
        return 0.0
    input_price, output_price, cache_read_price = prices
    return (
        usage.get("input_tokens", 0) * input_price
        + usage.get("cache_creation_input_tokens", 0) * input_price * 1.25
        + usage.get("cache_read_input_tokens", 0) * cache_read_price
        + usage.get("output_tokens", 0) * output_price
    ) / 1_000_000


def context_tokens(usage: dict | None) -> int:
    """Size of the conversation as of a stored assistant message's usage."""
    if not usage:
        return 0
    return sum(
        usage.get(key, 0)
        for key in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
        )
    )
