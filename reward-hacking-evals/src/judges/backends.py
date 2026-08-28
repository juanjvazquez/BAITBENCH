"""Backend dispatchers for the reward-hacking judges.

Three backends are supported:

- ``openai``     -> OpenAI Responses API (default model: gpt-5.4)
- ``anthropic``  -> Anthropic Messages API with tool-use structured output
                    (default model: claude-opus-4-6)
- ``openrouter`` -> OpenRouter chat-completions, OpenAI-compatible
                    (default model: z-ai/glm-5.1)

Each backend exposes the same call signature so the judge scripts can stay thin:

    call_judge(
        backend=...,
        model=...,
        api_key=...,
        system=...,
        user=...,
        schema=...,
        schema_name=...,
    ) -> dict[str, Any]
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


DEFAULT_MODELS = {
    "openai": "gpt-5.4",
    "anthropic": "claude-opus-4-6",
    "openrouter": "z-ai/glm-5.1",
}

API_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

SUPPORTED_BACKENDS = tuple(DEFAULT_MODELS.keys())


def resolve_model(backend: str, model: str | None) -> str:
    if model and model != "auto":
        return model
    return DEFAULT_MODELS[backend]


def resolve_api_key(backend: str) -> str:
    env_var = API_KEY_ENV[backend]
    key = os.environ.get(env_var)
    if not key:
        raise RuntimeError(f"{env_var} is not set (required for backend={backend})")
    return key


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive, got {value}")
    return value


# ---------------------------------------------------------------------------
# HTTP helper with retries
# ---------------------------------------------------------------------------


def _http_post_json(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float = 240.0,
    retries: int = 3,
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    last_exc: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            # Retry on 429 / 5xx; raise on 4xx client errors
            if exc.code == 429 or 500 <= exc.code < 600:
                last_exc = RuntimeError(f"HTTP {exc.code}: {err_body}")
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP {exc.code}: {err_body}") from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            time.sleep(2 ** attempt)
            continue
    raise RuntimeError(f"Request failed after {retries} attempts: {last_exc}")


# ---------------------------------------------------------------------------
# Post-hoc response repair / validation
# ---------------------------------------------------------------------------


def _walk_repair(value: Any, schema: dict[str, Any]) -> Any:
    """Recursively repair the response shape to match ``schema``.

    Defends against two observed failure modes from frontier judge backends:
    1. Anthropic tool-use occasionally returns a stringified JSON array where
       the schema says ``type: array`` (notably when ``max_tokens`` truncates
       mid-array). We try ``json.loads`` and an incremental ``raw_decode``
       walk to recover whatever array prefix is parseable.
    2. Same pattern for nested objects: if we expect a dict and got a string,
       try ``json.loads``.

    On any irrecoverable mismatch we raise ``ValueError`` so the pipeline
    records the failure and the bad response doesn't masquerade as data.
    """
    if not isinstance(schema, dict):
        return value

    # Resolve simple unions so type-narrowing can apply. We don't try to be
    # smart about full anyOf/oneOf — if those show up we just pass value through.
    expected = schema.get("type")
    if isinstance(expected, list):
        # e.g. ["integer", "null"] — we can't narrow further without more info
        return value

    if expected == "array":
        if isinstance(value, list):
            item_schema = schema.get("items") or {}
            return [_walk_repair(v, item_schema) for v in value]
        if isinstance(value, str):
            recovered = _recover_array_from_string(value)
            item_schema = schema.get("items") or {}
            return [_walk_repair(v, item_schema) for v in recovered]
        raise ValueError(f"Expected array, got {type(value).__name__}: {str(value)[:200]}")

    if expected == "object":
        if isinstance(value, dict):
            properties = schema.get("properties") or {}
            return {
                k: _walk_repair(v, properties[k]) if k in properties else v
                for k, v in value.items()
            }
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Expected object, got unparseable string: {exc}") from exc
            return _walk_repair(parsed, schema)
        raise ValueError(f"Expected object, got {type(value).__name__}")

    # Scalars and unknown types: pass through.
    return value


def _recover_array_from_string(raw: str) -> list[Any]:
    """Best-effort recovery of a JSON array that was returned as a string.

    First tries a clean ``json.loads``. On failure, walks the string with
    ``json.JSONDecoder.raw_decode`` and accumulates as many top-level items
    as parse cleanly. Used for truncated tool-use responses where the
    closing ``]`` was lost to ``max_tokens``.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return parsed
    raw = raw.strip()
    if not raw.startswith("["):
        raise ValueError(f"Expected array, got string not starting with [: {raw[:200]}")
    items: list[Any] = []
    decoder = json.JSONDecoder()
    i = 1
    while i < len(raw):
        while i < len(raw) and raw[i] in " \n\t,":
            i += 1
        if i >= len(raw) or raw[i] == "]":
            break
        try:
            obj, end = decoder.raw_decode(raw, i)
        except json.JSONDecodeError:
            break
        items.append(obj)
        i = end
    if not items:
        raise ValueError(f"Could not recover any array items from: {raw[:200]}")
    return items


def _strip_numeric_bounds(schema: Any) -> Any:
    """Return a schema copy without bounds unsupported by some routers."""
    if isinstance(schema, list):
        return [_strip_numeric_bounds(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "minItems",
            "maxItems",
        }:
            continue
        cleaned[key] = _strip_numeric_bounds(value)
    return cleaned


# ---------------------------------------------------------------------------
# OpenAI Responses API
# ---------------------------------------------------------------------------


def _openai_extract_text(response_json: dict[str, Any]) -> str:
    output_text = response_json.get("output_text")
    if isinstance(output_text, str) and output_text:
        return output_text
    for item in response_json.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
            if content.get("type") == "refusal":
                raise RuntimeError(f"Model refusal: {content.get('refusal', '')}")
    raise RuntimeError("No text output found in OpenAI Responses reply")


def call_openai(
    *,
    model: str,
    api_key: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "reasoning": {"effort": "medium"},
        "input": [
            {"role": "developer", "content": system},
            {"role": "user", "content": user},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    }
    response_json = _http_post_json(
        OPENAI_RESPONSES_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        payload=payload,
    )
    return json.loads(_openai_extract_text(response_json))


# ---------------------------------------------------------------------------
# Anthropic Messages API (tool-use structured output)
# ---------------------------------------------------------------------------


def call_anthropic(
    *,
    model: str,
    api_key: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
) -> dict[str, Any]:
    tool_name = schema_name
    # Prompt caching disabled: the system prompt for this codebase is ~1160
    # tokens, well below claude-opus-4-6's 4096-token cache minimum.
    # Anthropic silently skips caching for prompts under the threshold,
    # producing zero benefit but a more complex payload. Empirically
    # confirmed via direct curl test on 2026-05-06: cache_creation +
    # cache_read both stayed at 0 with the system marked cache_control.
    # Reintroduce caching only if the system prompt grows past ~4500 tokens.
    payload = {
        "model": model,
        # 4096 was too tight for the v2 evidence schema (object bullets with
        # structured refs). Opus would emit valid JSON, run out of tokens
        # mid-array, and Anthropic's tool-use server returned the truncated
        # array as a single JSON-encoded string in the input field. 16384
        # gives comfortable headroom for the largest plausible judgment
        # (classification with 5 mechanisms × 6 evidence bullets each).
        "max_tokens": _positive_int_env("ANTHROPIC_MAX_TOKENS", 16384),
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [
            {
                "name": tool_name,
                "description": "Return the structured judgment.",
                "input_schema": schema,
            }
        ],
        "tool_choice": {"type": "tool", "name": tool_name},
    }
    response_json = _http_post_json(
        ANTHROPIC_MESSAGES_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        payload=payload,
    )
    for block in response_json.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == tool_name:
            tool_input = block.get("input")
            if isinstance(tool_input, dict):
                return tool_input
            if isinstance(tool_input, str):
                return json.loads(tool_input)
    raise RuntimeError(f"No tool_use block found in Anthropic reply: {response_json}")


# ---------------------------------------------------------------------------
# OpenRouter chat completions (Kimi)
# ---------------------------------------------------------------------------


def _parse_openrouter_json_object(content: str) -> dict[str, Any]:
    """Parse a schema object, tolerating a provider-added prose prefix/fence.

    OpenRouter providers occasionally return a valid JSON object preceded by
    a short rationale or Markdown code fence despite ``response_format``.
    Only accept a decoded object; malformed or truncated objects still raise
    so the existing retry path remains in force.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        for index, char in enumerate(content):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(content[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise original_error
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")
    return parsed


def call_openrouter(
    *,
    model: str,
    api_key: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
) -> dict[str, Any]:
    payload = {
        "model": model,
        # Cap worst-case output. Empirically the median GLM-5.1 binary
        # judgment is ~700 output tokens and the 99th percentile is ~1300,
        # but a small fraction of cases triggered runaway emission that the
        # provider later truncated mid-JSON, causing client-side parse
        # errors at char 30K-65K. 16384 matches the anthropic budget and
        # gives plenty of headroom for the largest plausible classification
        # judgment (5 mechanisms x 6 evidence bullets).
        "max_tokens": _positive_int_env("OPENROUTER_MAX_TOKENS", 16384),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": _strip_numeric_bounds(schema),
            },
        },
        "temperature": 0.0,
    }
    reasoning_effort = os.getenv("OPENROUTER_REASONING_EFFORT", "").strip()
    if reasoning_effort:
        allowed_efforts = {"max", "xhigh", "high", "medium", "low", "minimal", "none"}
        if reasoning_effort not in allowed_efforts:
            raise RuntimeError(
                "OPENROUTER_REASONING_EFFORT must be one of "
                f"{sorted(allowed_efforts)}, got {reasoning_effort!r}"
            )
        # Keep the model's private reasoning out of the response body. The
        # judge only needs the schema-constrained final JSON, while the model
        # still uses the requested reasoning effort internally.
        payload["reasoning"] = {"effort": reasoning_effort, "exclude": True}
    provider_preferences: dict[str, Any] = {}
    provider_ignore = os.getenv("OPENROUTER_PROVIDER_IGNORE", "")
    ignored_providers = [
        provider.strip()
        for provider in provider_ignore.split(",")
        if provider.strip()
    ]
    if ignored_providers:
        provider_preferences["ignore"] = ignored_providers
    if provider_preferences:
        payload["provider"] = provider_preferences
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/spar-maded-2026/reward-hacking-evals",
        "X-Title": "reward-hacking-evals",
    }
    # GLM (and some other OpenRouter providers) occasionally emit malformed
    # JSON, an unterminated string, or a truncated response that fails to
    # parse client-side even with response_format strict=true. The HTTP-level
    # retry in _http_post_json handles network/5xx, but parse failures slip
    # through. Retry the whole call up to 2 more times when parse fails;
    # only escalate to RuntimeError if all retries also fail to parse.
    parse_errors: list[str] = []
    for attempt in range(3):
        response_json = _http_post_json(
            OPENROUTER_CHAT_URL,
            headers=headers,
            payload=payload,
        )
        choices = response_json.get("choices") or []
        if not choices:
            parse_errors.append(f"attempt {attempt+1}: no choices in reply")
            continue
        message = choices[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            parse_errors.append(f"attempt {attempt+1}: empty content")
            continue
        try:
            return _parse_openrouter_json_object(content)
        except json.JSONDecodeError as exc:
            # Common failure modes: trailing junk, unterminated string,
            # truncated mid-field. Retry the whole call.
            parse_errors.append(
                f"attempt {attempt+1}: JSONDecodeError {exc.msg} at char {exc.pos}"
                f" (content len={len(content)})"
            )
            continue
    raise RuntimeError(
        "OpenRouter response failed to parse after 3 attempts: "
        + "; ".join(parse_errors)
    )


BACKEND_CALLERS = {
    "openai": call_openai,
    "anthropic": call_anthropic,
    "openrouter": call_openrouter,
}


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def call_judge(
    *,
    backend: str,
    model: str,
    api_key: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    schema_name: str,
) -> dict[str, Any]:
    caller = BACKEND_CALLERS.get(backend)
    if caller is None:
        raise ValueError(f"Unsupported backend: {backend!r} (supported: {SUPPORTED_BACKENDS})")
    raw = caller(
        model=model,
        api_key=api_key,
        system=system,
        user=user,
        schema=schema,
        schema_name=schema_name,
    )
    # Defend against backends that occasionally return arrays as JSON-encoded
    # strings (Anthropic tool-use truncation, etc.). _walk_repair tries to
    # recover; if the response is irrecoverable it raises ValueError, which
    # the pipeline records as a per-record failure rather than silently
    # writing garbage.
    repaired = _walk_repair(raw, schema)
    if not isinstance(repaired, dict):
        raise ValueError(f"Expected dict at top level, got {type(repaired).__name__}")
    return repaired
