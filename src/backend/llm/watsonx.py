"""watsonx.ai / IBM Granite LLM client implementation.

Requires environment variables:
    WATSONX_API_KEY
    WATSONX_PROJECT_ID
    WATSONX_URL            (default: https://us-south.ml.cloud.ibm.com)
    WATSONX_MODEL_ID       (default: ibm/granite-13b-chat-v2)
"""

from __future__ import annotations

import os

from backend.llm.client import LLMClient

_DEFAULT_URL = "https://us-south.ml.cloud.ibm.com"
_DEFAULT_MODEL = "ibm/granite-13b-chat-v2"


class WatsonxClient(LLMClient):
    """Calls IBM watsonx.ai text generation API."""

    def __init__(self) -> None:
        self._api_key = os.environ["WATSONX_API_KEY"]
        self._project_id = os.environ["WATSONX_PROJECT_ID"]
        self._url = os.environ.get("WATSONX_URL", _DEFAULT_URL)
        self._model_id = os.environ.get("WATSONX_MODEL_ID", _DEFAULT_MODEL)
        self._client = self._build_client()

    def _build_client(self):  # type: ignore[return]
        try:
            from ibm_watsonx_ai.foundation_models import ModelInference
            from ibm_watsonx_ai import Credentials

            return ModelInference(
                model_id=self._model_id,
                credentials=Credentials(api_key=self._api_key, url=self._url),
                project_id=self._project_id,
            )
        except ImportError as exc:
            raise ImportError(
                "ibm-watsonx-ai package not installed. "
                "Run: pip install ibm-watsonx-ai"
            ) from exc

    def complete(self, system_prompt: str, user_message: str) -> str:
        prompt = f"{system_prompt}\n\nUser: {user_message}\nAssistant:"
        response = self._client.generate_text(
            prompt=prompt,
            params={"max_new_tokens": 512, "temperature": 0.1},
        )
        return response
