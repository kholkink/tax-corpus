"""Реранкер (F13 плана ПО): кросс-энкодер над top-N гибридного поиска.

По умолчанию BAAI/bge-reranker-v2-m3 (многоязычный, знает русский); на CPU ~0,1–0,3 с на пару,
поэтому включается явно: TAXCORPUS_RERANK=1 (модель по умолчанию) или TAXCORPUS_RERANK=<имя
модели>; TAXCORPUS_RERANK_DEPTH — сколько кандидатов гибрида переранжировать (по умолчанию 30).
Без sentence-transformers или без модели реранкер молча выключен — поиск работает как раньше.
"""

from __future__ import annotations

import os

DEFAULT_RERANKER = "BAAI/bge-reranker-v2-m3"


class Reranker:
    def __init__(self, model_name: str = DEFAULT_RERANKER, depth: int = 30, max_length: int = 512):
        self.model_name = model_name
        self.depth = depth
        self.max_length = max_length
        self._model = None
        self._failed: str | None = None

    @classmethod
    def from_env(cls) -> "Reranker | None":
        flag = (os.environ.get("TAXCORPUS_RERANK") or "").strip()
        if not flag or flag in ("0", "false", "no", "off"):
            return None
        model = DEFAULT_RERANKER if flag in ("1", "true", "yes", "on") else flag
        depth = int(os.environ.get("TAXCORPUS_RERANK_DEPTH") or 30)
        return cls(model, depth=depth)

    @property
    def ready(self) -> bool:
        if self._model is not None:
            return True
        if self._failed:
            return False
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            self._failed = "нет sentence-transformers"
            return False
        return True

    def model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder
            self._model = CrossEncoder(self.model_name, device="cpu", max_length=self.max_length)
        return self._model

    def scores(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        return [float(s) for s in self.model().predict([(query, t) for t in texts], batch_size=16,
                                                       show_progress_bar=False)]

    def rerank(self, query: str, candidates: list[tuple[str, str]], top_k: int | None = None) -> list[tuple[str, float]]:
        """candidates: [(unit_id, text)] -> [(unit_id, score)] по убыванию релевантности."""
        try:
            scored = list(zip([c[0] for c in candidates], self.scores(query, [c[1] for c in candidates])))
        except Exception as exc:  # noqa: BLE001 — модель не скачалась/не влезла: поиск без реранкера
            self._failed = f"{type(exc).__name__}: {exc}"
            return []
        scored.sort(key=lambda x: -x[1])
        return scored[:top_k] if top_k else scored
