"""LLM client — thin wrapper around the OpenAI SDK."""

from openai import AsyncOpenAI

from asapbackend.config import settings

_client: AsyncOpenAI | None = None


def get_llm_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url=settings.inference_base_url,
            api_key=settings.inference_api_key,
        )
    return _client


def sampling_kwargs(requested: float | None = None) -> dict:
    """Temperature kwargs for a completion call, honoring the master switch.

    INFERENCE_TEMPERATURE=-1 means the deployed model rejects the
    temperature parameter outright (OpenAI reasoning-class models accept
    only their default): omit it from EVERY request, including per-message
    values from the UI temperature stops and the RAG-internal 0.0 pins.
    Otherwise send the requested value, falling back to the configured
    default.
    """
    if settings.inference_temperature < 0:
        return {}
    return {
        "temperature": (
            requested if requested is not None
            else settings.inference_temperature
        )
    }
