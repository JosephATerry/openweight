"""Local llama.cpp backend for the Muse Glimmer text model."""

from __future__ import annotations

import ipaddress
import json
import time
from collections.abc import Callable, Mapping
from typing import Literal, TypeAlias, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from openweight_platform.backends.base import GenerationResult, ModelBackend


DEFAULT_MUSE_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MUSE_MODEL_ALIAS = "meta-models/Muse-Glimmer-30B"
DEFAULT_MUSE_TIMEOUT_SECONDS = 300.0
ReasoningStrength: TypeAlias = Literal["low", "medium", "high", "xhigh"]
SUPPORTED_REASONING_STRENGTHS = ("low", "medium", "high", "xhigh")

JsonObject: TypeAlias = dict[str, object]
JsonTransport: TypeAlias = Callable[[str, Mapping[str, object], float], JsonObject]


class MuseGlimmerError(RuntimeError):
    """Base error for the local Muse Glimmer backend."""


class MuseGlimmerConnectionError(MuseGlimmerError):
    """Raised when the configured local llama.cpp server is unavailable."""


class MuseGlimmerResponseError(MuseGlimmerError):
    """Raised when the local llama.cpp server returns an invalid response."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Prevent a loopback endpoint from redirecting to another destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post_json(
    url: str,
    payload: Mapping[str, object],
    timeout_seconds: float,
) -> JsonObject:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = build_opener(_NoRedirectHandler())

    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            raw_response = response.read()
    except HTTPError as error:
        raise MuseGlimmerConnectionError(
            "Local Muse llama.cpp server returned HTTP status "
            f"{error.code}"
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise MuseGlimmerConnectionError(
            "Unable to reach the local Muse llama.cpp server"
        ) from error

    try:
        parsed = json.loads(raw_response)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise MuseGlimmerResponseError(
            "Local Muse llama.cpp server returned invalid JSON"
        ) from error

    if not isinstance(parsed, dict):
        raise MuseGlimmerResponseError(
            "Local Muse llama.cpp response must be a JSON object"
        )
    return cast(JsonObject, parsed)


class MuseGlimmerBackend(ModelBackend):
    """Generate text through a separately managed local llama.cpp server.

    The server must enable Jinja chat templates so the official template
    embedded in the Muse GGUF is applied to the ``messages`` request.
    The server owns model loading and GPU memory, so ``load`` and ``unload``
    manage only this client's lifecycle. The existing ``peak_vram_gib`` field
    is returned as ``0.0`` because the OpenAI-compatible response does not
    expose reliable process-level VRAM usage to this client.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_MUSE_BASE_URL,
        model_alias: str = DEFAULT_MUSE_MODEL_ALIAS,
        max_new_tokens: int = 256,
        timeout_seconds: float = DEFAULT_MUSE_TIMEOUT_SECONDS,
        reasoning_strength: ReasoningStrength = "low",
        response_grammar: str | None = None,
        transport: JsonTransport | None = None,
    ) -> None:
        self.base_url = _validated_local_base_url(base_url)
        self.model_alias = _nonempty_string(model_alias, "model_alias").strip()
        if not isinstance(max_new_tokens, int) or isinstance(
            max_new_tokens,
            bool,
        ):
            raise TypeError("max_new_tokens must be an integer")
        if max_new_tokens <= 0:
            raise ValueError("max_new_tokens must be greater than zero")
        if not isinstance(timeout_seconds, (int, float)) or isinstance(
            timeout_seconds,
            bool,
        ):
            raise TypeError("timeout_seconds must be a number")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if reasoning_strength not in SUPPORTED_REASONING_STRENGTHS:
            expected = ", ".join(SUPPORTED_REASONING_STRENGTHS)
            raise ValueError(
                f"reasoning_strength must be one of: {expected}"
            )

        self.max_new_tokens = max_new_tokens
        self.timeout_seconds = float(timeout_seconds)
        self.reasoning_strength = reasoning_strength
        self.response_grammar = (
            _nonempty_string(response_grammar, "response_grammar")
            if response_grammar is not None
            else None
        )
        self._transport = transport or _post_json
        self._loaded = False

    @property
    def model_name(self) -> str:
        return self.model_alias

    def load(self) -> None:
        """Open the client lifecycle without launching or loading llama.cpp."""
        self._loaded = True

    def generate(self, prompt: str) -> GenerationResult:
        if not self._loaded:
            raise RuntimeError(
                "The backend must be loaded before generate() is called."
            )
        prompt = _nonempty_string(prompt, "prompt")
        payload: JsonObject = {
            "model": self.model_alias,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.max_new_tokens,
            "reasoning_effort": self.reasoning_strength,
            "temperature": 0.0,
            "stream": False,
        }
        if self.response_grammar is not None:
            payload["grammar"] = self.response_grammar

        start = time.perf_counter()
        try:
            response = self._transport(
                f"{self.base_url}/chat/completions",
                payload,
                self.timeout_seconds,
            )
        except MuseGlimmerError:
            raise
        except (URLError, TimeoutError, OSError) as error:
            raise MuseGlimmerConnectionError(
                "Unable to reach the local Muse llama.cpp server"
            ) from error
        generation_seconds = time.perf_counter() - start

        content = _response_content(response)
        input_tokens, output_tokens = _response_usage(response)
        return GenerationResult(
            text=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            generation_seconds=generation_seconds,
            peak_vram_gib=0.0,
        )

    def unload(self) -> None:
        """Close the client lifecycle without stopping the external server."""
        self._loaded = False


def _validated_local_base_url(base_url: str) -> str:
    value = _nonempty_string(base_url, "base_url").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Muse base_url must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Muse base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Muse base_url must not contain a query or fragment")
    if parsed.hostname is None or not _is_loopback_host(parsed.hostname):
        raise ValueError("Muse base_url must use a localhost loopback address")
    return value


def _is_loopback_host(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _response_content(response: Mapping[str, object]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise MuseGlimmerResponseError(
            "Local Muse llama.cpp response requires a non-empty choices list"
        )
    first_choice = choices[0]
    if not isinstance(first_choice, Mapping):
        raise MuseGlimmerResponseError(
            "Local Muse llama.cpp response choice must be an object"
        )
    message = first_choice.get("message")
    if not isinstance(message, Mapping):
        raise MuseGlimmerResponseError(
            "Local Muse llama.cpp response choice requires a message object"
        )
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise MuseGlimmerResponseError(
            "Local Muse llama.cpp response requires non-empty message content"
        )

    # reasoning_content is intentionally ignored. ModelRouter and policy
    # consumers receive only the final user-facing content.
    return content


def _response_usage(response: Mapping[str, object]) -> tuple[int, int]:
    usage = response.get("usage")
    if usage is None:
        return 0, 0
    if not isinstance(usage, Mapping):
        raise MuseGlimmerResponseError(
            "Local Muse llama.cpp response usage must be an object"
        )
    return (
        _usage_count(usage, "prompt_tokens"),
        _usage_count(usage, "completion_tokens"),
    )


def _usage_count(usage: Mapping[str, object], field: str) -> int:
    value = usage.get(field, 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MuseGlimmerResponseError(
            "Local Muse llama.cpp response "
            f"{field} must be a non-negative integer"
        )
    return value


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


__all__ = [
    "DEFAULT_MUSE_BASE_URL",
    "DEFAULT_MUSE_MODEL_ALIAS",
    "DEFAULT_MUSE_TIMEOUT_SECONDS",
    "JsonTransport",
    "MuseGlimmerBackend",
    "MuseGlimmerConnectionError",
    "MuseGlimmerError",
    "MuseGlimmerResponseError",
    "ReasoningStrength",
    "SUPPORTED_REASONING_STRENGTHS",
]
