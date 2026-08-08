"""Hosted embedding adapter (Voyage AI).

Kept behind a lazy import so the package installs and tests run with no extra
dependency and no API key. Any other hosted embedding provider is a ~20-line
sibling of this file; the rest of the system only sees `Embedder`.

Operational notes that matter more than the code:
  * Batch. Per-text calls dominate ingestion wall time.
  * Retry 429/5xx with jittered backoff; embedding a 40k-doc corpus will hit
    rate limits.
  * Pin the model id into the index metadata. Mixing vectors from two model
    versions in one index silently destroys recall, and it is invisible in
    unit tests — you only see it as a slow quality bleed in production.
"""

from __future__ import annotations

import os
import random
import time
from collections.abc import Sequence

from .base import Vector, l2_normalize


class VoyageEmbedder:
    def __init__(
        self,
        model: str = "voyage-3",
        dim: int = 1024,
        api_key: str | None = None,
        batch_size: int = 96,
        max_retries: int = 5,
        timeout_s: float = 30.0,
    ) -> None:
        self.model_id = model
        self.dim = dim
        self._api_key = api_key or os.getenv("VOYAGE_API_KEY")
        self._batch_size = batch_size
        self._max_retries = max_retries
        self._timeout_s = timeout_s
        self._client = None

    def _http(self):
        if self._client is None:
            import httpx  # lazy: optional dependency

            if not self._api_key:
                raise RuntimeError("VOYAGE_API_KEY is not set")
            self._client = httpx.Client(
                base_url="https://api.voyageai.com/v1",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout_s,
            )
        return self._client

    def _post(self, texts: Sequence[str], input_type: str) -> list[Vector]:
        import httpx

        payload = {"model": self.model_id, "input": list(texts), "input_type": input_type}
        last_error: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._http().post("/embeddings", json=payload)
                if response.status_code in (429, 500, 502, 503, 529):
                    raise httpx.HTTPStatusError(
                        "retryable", request=response.request, response=response
                    )
                response.raise_for_status()
                data = response.json()["data"]
                return [l2_normalize(item["embedding"]) for item in data]
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_error = exc
                time.sleep(min(2**attempt, 16) * (0.5 + random.random()))
        raise RuntimeError(f"embedding request failed after retries: {last_error}")

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        out: list[Vector] = []
        for start in range(0, len(texts), self._batch_size):
            out.extend(self._post(texts[start : start + self._batch_size], "document"))
        return out

    def embed_query(self, text: str) -> Vector:
        return self._post([text], "query")[0]
