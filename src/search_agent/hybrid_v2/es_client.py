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

    @property
    def client(self) -> Any:
        return self._ensure_client()

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
        request_body.setdefault("track_total_hits", True)
        try:
            response = client.search(
                index=self._config.es_index,
                body=request_body,
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

    def index_exists(self, index: str | None = None) -> bool:
        target = index or self._config.es_index
        response = self.client.indices.exists(index=target)
        return bool(response)

    def create_index(
        self,
        index: str,
        body: dict[str, Any],
        *,
        recreate: bool = False,
    ) -> bool:
        """Create an index and return whether this call created it."""

        exists = self.index_exists(index)
        if exists and recreate:
            self.client.indices.delete(index=index)
            exists = False
        if exists:
            return False
        self.client.indices.create(
            index=index,
            settings=body.get("settings"),
            mappings=body.get("mappings"),
            aliases=body.get("aliases"),
        )
        return True

    def delete_index(self, index: str | None = None) -> None:
        target = index or self._config.es_index
        if self.index_exists(target):
            self.client.indices.delete(index=target)

    def refresh_index(self, index: str | None = None) -> None:
        target = index or self._config.es_index
        self.client.indices.refresh(index=target)

    def count_documents(self, index: str | None = None) -> int:
        target = index or self._config.es_index
        response = self._response_dict(self.client.count(index=target))
        try:
            return int(response.get("count") or 0)
        except (TypeError, ValueError):
            return 0

    def get_mapping(self, index: str | None = None) -> dict[str, Any]:
        target = index or self._config.es_index
        return self._response_dict(self.client.indices.get_mapping(index=target))

    def index_documents(
        self,
        index: str,
        documents: list[dict[str, Any]],
        *,
        batch_size: int = 500,
        refresh: str | bool = False,
    ) -> int:
        """Bulk-index documents and raise on any item-level failure."""

        written = 0
        effective_batch = max(1, int(batch_size))
        for offset in range(0, len(documents), effective_batch):
            batch = documents[offset : offset + effective_batch]
            operations: list[dict[str, Any]] = []
            for document in batch:
                source = dict(document)
                doc_id = source.pop("_id", None) or source.get("doc_id")
                if doc_id is None:
                    raise RuntimeError("bulk indexing requires _id or doc_id on every document")
                operations.append({"index": {"_id": str(doc_id)}})
                operations.append(source)
            response = self._response_dict(
                self.client.bulk(
                    index=index,
                    operations=operations,
                    refresh=False,
                )
            )
            if response.get("errors"):
                failures: list[str] = []
                for item in response.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    for action in item.values():
                        if not isinstance(action, dict) or not action.get("error"):
                            continue
                        error = action.get("error")
                        reason = error.get("reason") if isinstance(error, dict) else error
                        failures.append(str(reason))
                        if len(failures) >= 3:
                            break
                    if len(failures) >= 3:
                        break
                detail = "; ".join(failures) or "unknown bulk item failure"
                raise RuntimeError(f"Hybrid V2 bulk indexing failed: {detail}")
            written += len(batch)
        if refresh and documents:
            self.refresh_index(index)
        return written

    @staticmethod
    def _response_dict(response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            return response
        body = getattr(response, "body", None)
        if isinstance(body, dict):
            return dict(body)
        return dict(response)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


__all__ = ["ElasticsearchClient"]
