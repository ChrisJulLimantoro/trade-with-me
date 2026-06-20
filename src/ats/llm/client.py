"""LLM client abstraction: a deterministic mock and a real OpenAI implementation.

Since Part 2 the LLM is a bounded *judge*, not an author. The live calls are
``adjudicate`` (plan-time ±0.20 veto/bias over the deterministic signal),
``observe_trade`` (tactical exit strategy on open trades) and ``reflect_trade``
(post-mortem). All return ``(parsed | None, LlmResult)`` so callers are mode-agnostic.
``get_client()`` returns the mock whenever ``settings.llm_mock`` is set or no API key is
present — the default — so a fresh checkout runs end to end with no secrets and no spend.

On any error or schema-validation failure the real client returns ``(None, result)``
with ``parse_ok=False``; callers then fall back to deterministic behaviour (delta 0)
rather than trusting unstructured output.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Protocol, runtime_checkable

from ats.config import settings
from ats.llm import mock_data, prompts
from ats.llm.schemas import (
    AdjudicationOutput,
    LlmResult,
    ObservationOutput,
    ReflectionOutput,
)
from ats.logging import get_logger

log = get_logger(__name__)


def _validate_json(schema: type, content: str) -> Any:
    """Validate ``content`` against ``schema``, repairing malformed JSON if needed.

    The strict path (``model_validate_json``) handles well-formed output. When a model
    garnishes the object — code fences, leading prose, trailing commas, smart quotes —
    that raises, and we fall back to ``json_repair`` to salvage the embedded object
    before validating the repaired dict. Repair is only attempted on the failure path,
    so clean responses pay no extra cost. A genuinely empty/non-JSON body re-raises the
    original error and the caller degrades to deterministic behaviour.
    """
    try:
        return schema.model_validate_json(content)
    except Exception:
        from json_repair import repair_json

        repaired = repair_json(content, return_objects=True)
        if not isinstance(repaired, (dict, list)) or repaired == "":
            raise
        log.info("llm_json_repaired", schema=getattr(schema, "__name__", str(schema)))
        return schema.model_validate(repaired)

# USD per 1M tokens (input, output). Rough public list prices; only used for the audit
# row. Unknown models cost 0 — the number is informational, not a billing source.
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
}


def _cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = _PRICING.get(model, (0.0, 0.0))
    return round(pin * tokens_in / 1_000_000 + pout * tokens_out / 1_000_000, 6)


# Interface segregation: each LLM role is its own narrow Protocol so a collaborator depends
# only on the call it actually makes — the planner on Adjudicator, the post-mortem on
# Reflector, the trade-review path on Observer. ``LlmClient`` composes all three for the
# runtime/factory that needs the whole surface.
@runtime_checkable
class Adjudicator(Protocol):
    async def adjudicate(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[AdjudicationOutput | None, LlmResult]: ...


@runtime_checkable
class Observer(Protocol):
    async def observe_trade(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[ObservationOutput | None, LlmResult]: ...


@runtime_checkable
class Reflector(Protocol):
    async def reflect_trade(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[ReflectionOutput | None, LlmResult]: ...


@runtime_checkable
class LlmClient(Adjudicator, Observer, Reflector, Protocol):
    """The full LLM surface (every role). Use a narrower role Protocol where only one is needed."""


class MockClient:
    """Deterministic, offline. Pure function of the envelope."""

    mock = True

    async def adjudicate(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[AdjudicationOutput, LlmResult]:
        adj = mock_data.canned_adjudication(envelope)
        result = LlmResult(
            parse_ok=True, model="mock", mock=True, raw=adj.model_dump(mode="json")
        )
        return adj, result

    async def observe_trade(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[ObservationOutput, LlmResult]:
        obs = mock_data.canned_observation(envelope)
        result = LlmResult(
            parse_ok=True, model="mock", mock=True, raw=obs.model_dump(mode="json")
        )
        return obs, result

    async def reflect_trade(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[ReflectionOutput, LlmResult]:
        reflection = mock_data.canned_reflection(envelope)
        result = LlmResult(
            parse_ok=True, model="mock", mock=True, raw=reflection.model_dump(mode="json")
        )
        return reflection, result


class OpenAIClient:
    """Real OpenAI implementation using the Responses API structured-output parser."""

    mock = False

    def __init__(self, api_key: str) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key,
            **({"base_url": settings.openai_base_url} if settings.openai_base_url else {}),
        )
        # Plan-time adjudication is cached by envelope hash: an identical signal + context
        # returns the same bounded delta without a second API call, so repeated identical
        # replays are reproducible and the only residual LLM variance is one ±0.20 delta
        # per distinct plan envelope.
        self._adjudication_cache: dict[str, AdjudicationOutput] = {}

    async def _parse(
        self, *, model: str, system: str, envelope: dict[str, Any], schema: type
    ) -> tuple[Any | None, LlmResult]:
        t0 = time.perf_counter()
        # Derive a stable seed from the bar timestamp so identical inputs produce
        # identical outputs on providers that honour it (OpenAI, most OpenRouter models).
        _as_of = envelope.get("as_of")
        seed = (
            int(_as_of.timestamp())
            if hasattr(_as_of, "timestamp")
            else abs(hash(str(_as_of))) % (2**31)
        )
        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompts.user_message(envelope)},
                ],
                response_format={"type": "json_object"},
                max_tokens=settings.llm_max_tokens,
                reasoning_effort="low",
                # opencode zen / OpenRouter convention: disable the reasoning trace so
                # the token budget goes to the JSON answer, not hidden chain-of-thought.
                temperature=0.0,
                seed=seed,
                extra_body={
                    "reasoning": {"enabled": False},
                    "thinking": {"type": "disabled"}
                },
            )
            choice = resp.choices[0]
            content = choice.message.content or ""
            if not content:
                finish = getattr(choice, "finish_reason", None)
                reasoning = getattr(choice.message, "reasoning_content", None)
                raise ValueError(
                    f"model returned no content (finish_reason={finish}, "
                    f"reasoning_len={len(reasoning or '')})"
                )
            parsed = _validate_json(schema, content)
            usage = getattr(resp, "usage", None)
            tin = int(getattr(usage, "prompt_tokens", 0) or 0)
            tout = int(getattr(usage, "completion_tokens", 0) or 0)
            latency = int((time.perf_counter() - t0) * 1000)
            result = LlmResult(
                parse_ok=True,
                model=model,
                mock=False,
                input_tokens=tin,
                output_tokens=tout,
                cost_usd=_cost_usd(model, tin, tout),
                latency_ms=latency,
                raw=parsed.model_dump(mode="json"),
            )
            return parsed, result
        except Exception as exc:  # noqa: BLE001 — boundary: degrade to deterministic
            latency = int((time.perf_counter() - t0) * 1000)
            log.warning("llm_parse_failed", model=model, error=str(exc))
            return None, LlmResult(
                parse_ok=False, model=model, mock=False, latency_ms=latency
            )

    async def adjudicate(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[AdjudicationOutput | None, LlmResult]:
        key = hashlib.sha256(
            (settings.llm_adjudicate_model + "|" + prompts.user_message(envelope)).encode()
        ).hexdigest()
        cached = self._adjudication_cache.get(key)
        if cached is not None:
            return cached, LlmResult(
                parse_ok=True,
                model=settings.llm_adjudicate_model,
                mock=False,
                cached=True,
                latency_ms=0,
                raw=cached.model_dump(mode="json"),
            )
        parsed, result = await self._parse(
            model=settings.llm_adjudicate_model,
            system=prompts.ADJUDICATE_SYSTEM_PROMPT,
            envelope=envelope,
            schema=AdjudicationOutput,
        )
        if result.parse_ok and parsed is not None:
            self._adjudication_cache[key] = parsed
        return parsed, result

    async def observe_trade(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[ObservationOutput | None, LlmResult]:
        parsed, result = await self._parse(
            model=settings.llm_observe_model,
            system=prompts.OBSERVE_SYSTEM_PROMPT,
            envelope=envelope,
            schema=ObservationOutput,
        )
        if not result.parse_ok:
            log.info("observe_trade_retry", symbol=symbol)
            parsed, result = await self._parse(
                model=settings.llm_observe_model,
                system=prompts.OBSERVE_SYSTEM_PROMPT,
                envelope=envelope,
                schema=ObservationOutput,
            )
        return parsed, result

    async def reflect_trade(
        self, envelope: dict[str, Any], *, symbol: str
    ) -> tuple[ReflectionOutput | None, LlmResult]:
        return await self._parse(
            model=settings.llm_reflect_model,
            system=prompts.REFLECT_SYSTEM_PROMPT,
            envelope=envelope,
            schema=ReflectionOutput,
        )


def get_client() -> LlmClient:
    """Return the mock unless real OpenAI calls are explicitly enabled with a key."""
    if settings.llm_mock or not settings.openai_api_key:
        return MockClient()
    return OpenAIClient(settings.openai_api_key)
