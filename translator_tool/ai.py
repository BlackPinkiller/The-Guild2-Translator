from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterator, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .settings import AppSettings, reveal_secret
from .validation import TOKEN_RE, validate_translation


class TranslationProviderError(RuntimeError):
    pass


class JsonTransport(Protocol):
    def get_json(self, url: str) -> Any: ...

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> Any: ...


class UrlLibTransport:
    timeout_seconds = 45

    def get_json(self, url: str) -> Any:
        return self._read_json(Request(url, headers={"User-Agent": "TheGuild2Translator/1.0"}))

    def post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        request_headers = {"Content-Type": "application/json", **headers}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self._read_json(Request(url, data=data, headers=request_headers, method="POST"))

    def post_sse(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> Iterator[Any]:
        request_headers = {"Content-Type": "application/json", "Accept": "text/event-stream", **headers}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=data, headers=request_headers, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        return
                    try:
                        yield json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise TranslationProviderError("LLM 流式响应格式无效。") from exc
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise TranslationProviderError(f"HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise TranslationProviderError(str(exc)) from exc

    def _read_json(self, request: Request) -> Any:
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise TranslationProviderError(f"HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise TranslationProviderError(str(exc)) from exc


@dataclass(frozen=True)
class ProtectedText:
    text: str
    tokens: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class LlmNeighborContext:
    relation: str
    label: str
    source_text: str
    record_id: str = ""


@dataclass(frozen=True)
class LlmSuggestionContext:
    file_rel: str
    record_id: str
    label: str
    neighbors: tuple[LlmNeighborContext, ...] = ()


def _build_llm_suggestion_prompt(
    source: str, current_translation: str, context: LlmSuggestionContext | None = None
) -> str:
    lines = [
        f"原文：\n{source}",
        f"当前译文：\n{current_translation or '（空）'}",
    ]
    if context is None:
        return "\n\n".join(lines)
    lines.extend(
        (
            "条目上下文：",
            f"- 文件：{context.file_rel or '（未知）'}",
            f"- ID：{context.record_id or '（空）'}",
            f"- Label：{context.label or '（空）'}",
        )
    )
    if context.neighbors:
        lines.append("前后邻近条目（用于帮助理解当前条目语义，不要把它们拼进当前译文）：")
        for neighbor in context.neighbors:
            identity = neighbor.relation
            if neighbor.record_id:
                identity += f" · ID={neighbor.record_id}"
            lines.append(f"- {identity}")
            lines.append(f"  Label: {neighbor.label or '（空）'}")
            lines.append(f"  原文: {neighbor.source_text or '（空）'}")
    return "\n\n".join(lines)


def protect_tokens(text: str) -> ProtectedText:
    tokens: list[tuple[str, str]] = []
    pieces: list[str] = []
    cursor = 0
    for index, match in enumerate(TOKEN_RE.finditer(text)):
        placeholder = f"__TG_FMT_{index:04d}__"
        pieces.extend((text[cursor : match.start()], placeholder))
        tokens.append((placeholder, match.group(0)))
        cursor = match.end()
    pieces.append(text[cursor:])
    return ProtectedText("".join(pieces), tuple(tokens))


def restore_tokens(text: str, protected: ProtectedText) -> str:
    restored = text
    for placeholder, token in protected.tokens:
        if restored.count(placeholder) != 1:
            raise TranslationProviderError("AI 修改或删除了游戏格式标记，结果未应用。")
        restored = restored.replace(placeholder, token)
    return restored


class TranslationProvider(Protocol):
    name: str
    request_delay_seconds: float

    def translate(self, source: str, *, dbt_field: bool) -> str: ...


@dataclass
class GoogleTranslateProvider:
    endpoint: str
    source_language: str
    target_language: str
    transport: JsonTransport
    name: str = "Google Translate（公共免费端点）"
    request_delay_seconds: float = 1.05

    def translate(self, source: str, *, dbt_field: bool) -> str:
        protected = protect_tokens(source)
        params = urlencode(
            {
                "client": "gtx",
                "sl": self.source_language,
                "tl": self.target_language,
                "dt": "t",
                "q": protected.text,
            }
        )
        separator = "&" if "?" in self.endpoint else "?"
        response = self.transport.get_json(self.endpoint + separator + params)
        try:
            translated = "".join(part[0] for part in response[0])
        except (IndexError, KeyError, TypeError) as exc:
            raise TranslationProviderError("Google Translate 返回了无法识别的数据。") from exc
        return _validate_result(source, restore_tokens(translated, protected), dbt_field)


@dataclass
class OpenAICompatibleProvider:
    base_url: str
    model: str
    api_key: str
    transport: JsonTransport
    name: str = "OpenAI 兼容接口"
    request_delay_seconds: float = 0.0

    def translate(self, source: str, *, dbt_field: bool) -> str:
        if not self.api_key:
            raise TranslationProviderError("请先在设置中填写 OpenAI 兼容接口的 API Key。")
        protected = protect_tokens(source)
        instruction = (
            "Translate this The Guild 2 game text from English into Simplified Chinese. "
            "Return only the translation. Keep every __TG_FMT_####__ token exactly unchanged. "
            "Do not add quotes or explanations."
        )
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": protected.text},
            ],
        }
        response = self.transport.post_json(
            _chat_completions_url(self.base_url), payload, {"Authorization": f"Bearer {self.api_key}"}
        )
        try:
            translated = response["choices"][0]["message"]["content"]
        except (IndexError, KeyError, TypeError) as exc:
            raise TranslationProviderError("OpenAI 兼容接口返回了无法识别的数据。") from exc
        if not isinstance(translated, str):
            raise TranslationProviderError("OpenAI 兼容接口没有返回文本译文。")
        return _validate_result(source, restore_tokens(translated.strip(), protected), dbt_field)

    def stream_suggestion(self, source: str, current_translation: str) -> Iterator[str]:
        """Yield concise reviewer advice as an OpenAI-compatible SSE response arrives."""
        if not self.api_key:
            raise TranslationProviderError("请先在设置中填写 OpenAI 兼容接口的 API Key。")
        instruction = (
            "You are a Chinese game-localization reviewer for The Guild 2. Reply in Simplified Chinese using Markdown. "
            "Explain the source meaning, tone, ambiguity, placeholders, and any key localization choices in useful detail. "
            "Then provide exactly one recommended translation in this final section and no other code blocks: "
            "## 推荐译文\n```text\n<translation only>\n```. "
            "Preserve placeholders and game formatting tokens exactly in the recommended translation. "
            "Do not modify files; this is advice only."
        )
        prompt = f"原文：\n{source}\n\n当前译文：\n{current_translation or '（空）'}"
        payload = {
            "model": self.model,
            "temperature": 0.25,
            "stream": True,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": prompt},
            ],
        }
        url = _chat_completions_url(self.base_url)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        stream = getattr(self.transport, "post_sse", None)
        if not callable(stream):
            # Custom transports used by tests or simple relays may not support SSE.
            payload["stream"] = False
            response = self.transport.post_json(url, payload, headers)
            try:
                content = response["choices"][0]["message"]["content"]
            except (IndexError, KeyError, TypeError) as exc:
                raise TranslationProviderError("LLM 没有返回可显示的建议。") from exc
            if not isinstance(content, str):
                raise TranslationProviderError("LLM 没有返回文本建议。")
            yield content
            return
        for event in stream(url, payload, headers):
            try:
                content = event["choices"][0]["delta"].get("content", "")
            except (IndexError, KeyError, TypeError) as exc:
                raise TranslationProviderError("LLM 流式响应缺少内容。") from exc
            if isinstance(content, str) and content:
                yield content

    def stream_suggestion_with_context(
        self,
        source: str,
        current_translation: str,
        context: LlmSuggestionContext | None = None,
    ) -> Iterator[str]:
        """Yield reviewer advice with additional entry context when available."""
        if context is None:
            yield from self.stream_suggestion(source, current_translation)
            return
        if not self.api_key:
            raise TranslationProviderError("请先在设置中填写 OpenAI 兼容接口的 API Key。")
        instruction = (
            "You are a Chinese game-localization reviewer for The Guild 2. Reply in Simplified Chinese using Markdown. "
            "Explain the source meaning, tone, ambiguity, placeholders, and any key localization choices in useful detail. "
            "Use the entry label and nearby source entries as disambiguation context when they are provided, but only translate the current source text. "
            "Then provide exactly one recommended translation in this final section and no other code blocks: "
            "## 推荐译文\n```text\n<translation only>\n```. "
            "Preserve placeholders and game formatting tokens exactly in the recommended translation. "
            "Do not modify files; this is advice only."
        )
        prompt = _build_llm_suggestion_prompt(source, current_translation, context)
        payload = {
            "model": self.model,
            "temperature": 0.25,
            "stream": True,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": prompt},
            ],
        }
        url = _chat_completions_url(self.base_url)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        stream = getattr(self.transport, "post_sse", None)
        if not callable(stream):
            payload["stream"] = False
            response = self.transport.post_json(url, payload, headers)
            try:
                content = response["choices"][0]["message"]["content"]
            except (IndexError, KeyError, TypeError) as exc:
                raise TranslationProviderError("LLM 没有返回可显示的建议。") from exc
            if not isinstance(content, str):
                raise TranslationProviderError("LLM 没有返回文本建议。")
            yield content
            return
        for event in stream(url, payload, headers):
            try:
                content = event["choices"][0]["delta"].get("content", "")
            except (IndexError, KeyError, TypeError) as exc:
                raise TranslationProviderError("LLM 流式响应缺少内容。") from exc
            if isinstance(content, str) and content:
                yield content


def provider_from_settings(settings: AppSettings, transport: JsonTransport | None = None) -> TranslationProvider:
    client = transport or UrlLibTransport()
    if settings.provider == "openai":
        return OpenAICompatibleProvider(
            settings.openai_base_url.strip(),
            settings.openai_model.strip(),
            reveal_secret(settings.openai_api_key_protected),
            client,
        )
    return GoogleTranslateProvider(
        settings.google_endpoint.strip(), settings.source_language.strip(), settings.target_language.strip(), client
    )


def llm_provider_from_settings(settings: AppSettings, transport: JsonTransport | None = None) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        settings.openai_base_url.strip(),
        settings.openai_model.strip(),
        reveal_secret(settings.openai_api_key_protected),
        transport or UrlLibTransport(),
    )


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _validate_result(source: str, translated: str, dbt_field: bool) -> str:
    errors = [issue.message for issue in validate_translation(source, translated, dbt_field=dbt_field) if issue.blocks_save]
    if errors:
        raise TranslationProviderError("AI 结果未通过格式校验：" + "; ".join(errors))
    return translated
