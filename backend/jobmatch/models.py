import asyncio
from contextlib import nullcontext
from typing import TypeVar

from langfuse import Langfuse
from openai import AsyncOpenAI, OpenAIError
from openai.types.chat import ChatCompletionMessage
from pydantic import BaseModel, ValidationError

from .config import Settings

T = TypeVar("T", bound=BaseModel)
EMBEDDING_DIMENSIONS = 1536


class ModelUnavailable(Exception):
    pass


class ModelResponseError(Exception):
    pass


class Models:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url, timeout=50)
            if settings.openai_api_key else None
        )
        self.tracer = (
            Langfuse(public_key=settings.langfuse_public_key, secret_key=settings.langfuse_secret_key,
                     base_url=settings.langfuse_base_url)
            if settings.langfuse_public_key and settings.langfuse_secret_key else None
        )

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
        if self.tracer is not None:
            await asyncio.to_thread(self.tracer.flush)

    def _client(self) -> AsyncOpenAI:
        if self.client is None:
            raise ModelUnavailable("The model provider is not configured.")
        return self.client

    def operation(self, name: str):
        if self.tracer is None:
            return nullcontext()
        return self.tracer.start_as_current_observation(name=name, as_type="span")

    def _generation(self, name: str, model: str):
        if self.tracer is None:
            return nullcontext()
        return self.tracer.start_as_current_observation(name=name, as_type="generation", model=model)

    @staticmethod
    def _usage(span, usage) -> None:
        if span is not None and usage is not None:
            data = {"input": usage.prompt_tokens, "total": usage.total_tokens}
            if hasattr(usage, "completion_tokens"):
                data["output"] = usage.completion_tokens
            span.update(usage_details=data)

    @staticmethod
    def _error(span) -> None:
        if span is not None:
            span.update(level="ERROR", status_message="Provider request or response failed.")

    async def structured(self, name: str, messages: list[dict], schema: type[T]) -> T:
        client = self._client()
        model = self.settings.extraction_model
        with self._generation(name, model) as span:
            try:
                result = await client.chat.completions.parse(
                    model=model, messages=messages, response_format=schema, store=False,
                    temperature=0, max_completion_tokens=4000,
                )
                self._usage(span, result.usage)
                parsed = result.choices[0].message.parsed if result.choices else None
                if parsed is not None:
                    return parsed
            except (OpenAIError, ValidationError):
                pass
            self._error(span)
        raise ModelResponseError("The model did not return a usable response.")

    async def chat(self, name: str, messages: list[dict], tools: list[dict]) -> ChatCompletionMessage:
        client = self._client()
        model = self.settings.chat_model
        with self._generation(name, model) as span:
            try:
                result = await client.chat.completions.create(
                    model=model, messages=messages, tools=tools, parallel_tool_calls=False,
                    store=False, max_completion_tokens=1500,
                )
                self._usage(span, result.usage)
                if result.choices:
                    message = result.choices[0].message
                    if not message.refusal and (message.content or message.tool_calls):
                        return message
            except OpenAIError:
                pass
            self._error(span)
        raise ModelResponseError("The model did not return a usable response.")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._client()
        model = self.settings.embedding_model
        with self._generation("embed", model) as span:
            try:
                result = await client.embeddings.create(
                    model=model, input=texts, dimensions=EMBEDDING_DIMENSIONS, encoding_format="float",
                )
                self._usage(span, result.usage)
                vectors = sorted(result.data, key=lambda item: item.index)
                if len(vectors) == len(texts) and all(
                    item.index == index and len(item.embedding) == EMBEDDING_DIMENSIONS
                    for index, item in enumerate(vectors)
                ):
                    return [item.embedding for item in vectors]
            except OpenAIError:
                pass
            self._error(span)
        raise ModelResponseError("The model did not return usable embeddings.")
