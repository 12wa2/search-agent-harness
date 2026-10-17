"""Small Elasticsearch 8 client with lazy imports and environment-only credentials."""

from __future__ import annotations

from typing import Any

from .config import HybridV2Config


class ElasticsearchClient:
    """Execute validated search bodies against a user-provided ES 8 cluster."""

    def __init__(
        self,
        config: HybridV2Config,
        *,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._client = client

    @property
    def index(self) -> str:
        return self._config.es_index

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from elasticsearch import Elasticsearch
        except ImportError as exc:
            raise RuntimeError(
                'Hybrid V2 needs Elasticsearch support: pip install -e ".[es]"'
            ) from exc

        kwargs: dict[str, Any] = {
            "verify_certs": self._config.es_verify_certs,
            "request_timeout": self._config.es_request_timeout,
        }
        if self._config.es_api_key:
            kwargs["api_key"] = self._config.es_api_key
        elif self._config.es_username:
            kwargs["basic_auth"] = (
                self._config.es_username,
                self._config.es_password,
            )
        self._client = Elasticsearch(self._config.es_url, **kwargs)
        return self._client

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        """Return a plain response mapping; never include credentials in errors."""

        client = self._ensure_client()
        request_body = dict(body)
        request_body.pop("_name_meta", None)
        try:
            response = client.search(
                index=self._config.es_index,
                body=request_body,
                track_total_hits=True,
            )
        except Exception as exc:
            raise RuntimeError(
                "Hybrid V2 Elasticsearch search failed; verify "
                "SEARCH_AGENT_HYBRID_V2_ES_URL, index mapping, credentials, and TLS settings"
            ) from exc
        if isinstance(response, dict):
            return response
        if hasattr(response, "body"):
            return dict(response.body)
        return dict(response)

    def health(self) -> dict[str, Any]:
        """Return non-secret cluster information for an operator health check."""

        client = self._ensure_client()
        try:
            info = client.info()
        except Exception as exc:
            raise RuntimeError(f"Elasticsearch is not reachable at {self._config.es_url}") from exc
        payload = dict(info) if not isinstance(info, dict) else info
        version = payload.get("version") if isinstance(payload, dict) else None
        return {
            "reachable": True,
            "url": self._config.es_url,
            "index": self._config.es_index,
            "clusterName": payload.get("cluster_name") if isinstance(payload, dict) else None,
            "version": version.get("number") if isinstance(version, dict) else None,
        }

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


__all__ = ["ElasticsearchClient"]
