from dataclasses import dataclass, field
import logging
import os
from pathlib import Path

from dotenv import dotenv_values
from openai import (
    APIConnectionError, APIError, APITimeoutError, AuthenticationError,
    BadRequestError, OpenAI, PermissionDeniedError, RateLimitError,
)
from pydantic import BaseModel

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


@dataclass(frozen=True)
class ApiSettings:
    api_key: str = field(repr=False)
    model: str


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ModelReply:
    payload: BaseModel | dict | str | None
    usage: TokenUsage | None = None


class ExtractionRequestError(Exception):
    """A safe message for display without SDK request or response details."""


def read_settings(env_file: Path = ENV_FILE) -> ApiSettings:
    values = dotenv_values(env_file, encoding="utf-8-sig", interpolate=False)
    key = os.environ.get("OPENAI_API_KEY") or values.get("OPENAI_API_KEY") or ""
    model = os.environ.get("OPENAI_MODEL") or values.get("OPENAI_MODEL") or ""
    return ApiSettings(key.strip(), model.strip())


def configuration_message(settings: ApiSettings) -> str | None:
    if not settings.api_key:
        return "API 키가 설정되지 않았어요"
    if not settings.model:
        return "사용할 AI 모델이 설정되지 않았어요"
    return None


class ExtractionClient:
    def __init__(self, settings: ApiSettings):
        message = configuration_message(settings)
        if message:
            raise ExtractionRequestError(message)
        self.model = settings.model
        self.settings = settings
        # SDK debug logging can include the text sent to the service.
        logging.getLogger("openai").setLevel(logging.WARNING)
        logging.getLogger("openai._base_client").setLevel(logging.WARNING)

    def read(self, prompt: str, documents: str, schema: type[BaseModel]) -> ModelReply:
        try:
            with OpenAI(
                api_key=self.settings.api_key,
                base_url="https://api.openai.com/v1",
                timeout=45.0,
                max_retries=0,
            ) as api:
                response = api.responses.parse(
                    model=self.model,
                    input=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": documents},
                    ],
                    text_format=schema,
                    store=False,
                    max_output_tokens=8192,
                )
        except APITimeoutError:
            raise ExtractionRequestError("AI 응답이 늦어졌어요. 잠시 후 다시 시도해 주세요.") from None
        except (AuthenticationError, PermissionDeniedError):
            raise ExtractionRequestError("API 키 또는 모델의 사용 권한을 확인해 주세요.") from None
        except RateLimitError:
            raise ExtractionRequestError("요청 한도에 도달했어요. 잠시 후 다시 시도해 주세요.") from None
        except BadRequestError:
            raise ExtractionRequestError("모델 설정이나 서류 길이를 확인한 뒤 다시 시도해 주세요.") from None
        except (APIConnectionError, APIError):
            raise ExtractionRequestError("AI 서비스 연결에 문제가 있어요. 잠시 후 다시 시도해 주세요.") from None
        usage = response.usage
        return ModelReply(
            response.output_parsed if response.status == "completed" else None,
            TokenUsage(usage.input_tokens, usage.output_tokens) if usage else None,
        )
