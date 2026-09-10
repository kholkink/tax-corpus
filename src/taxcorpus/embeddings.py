"""Семантическое плечо поиска (план, слой 4): dense-эмбеддинги чанков + RRF-слияние.

Без pgvector: векторы чанков лежат в data/index/<model>.npz (матрица float32 с
нормировкой + список unit_id), поиск — скалярное произведение по всей матрице
(10–15 тыс. чанков, миллисекунды). Модель — многоязычная, работает на CPU
(по умолчанию intfloat/multilingual-e5-small; e5 требует префиксов «query:» /
«passage:»). Слияние с лексическим поиском — Reciprocal Rank Fusion.

Построение: python -m taxcorpus embed [--model …]   (нужен extra [semantic])
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_MODEL = "intfloat/multilingual-e5-small"
INDEX_DIR = Path("data/index")


def _safe_name(model: str) -> str:
    return model.replace("/", "__")


def chunk_text(record: dict) -> str:
    """Текст чанка для эмбеддинга: контекст заголовков + полный текст единицы."""
    context = record.get("context") or ""
    body = record.get("full_text") or record.get("text") or ""
    return f"{context}\n{body}".strip()


class DenseIndex:
    """Матрица эмбеддингов чанков корпуса и поиск по косинусу."""

    def __init__(self, model_name: str = DEFAULT_MODEL, index_dir: str | Path = INDEX_DIR):
        self.model_name = model_name
        self.index_dir = Path(index_dir)
        self.path = self.index_dir / f"{_safe_name(model_name)}.npz"
        self._model = None
        self.ids: list[str] = []
        self.matrix = None

    # --- модель ---------------------------------------------------------------------
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device="cpu")
        return self._model

    def _prefix(self, kind: str) -> str:
        return f"{kind}: " if "e5" in self.model_name else ""

    def encode_queries(self, queries: list[str]):
        return self.model().encode([self._prefix("query") + q for q in queries],
                                   normalize_embeddings=True, convert_to_numpy=True)

    def encode_passages(self, texts: list[str], batch_size: int = 32, show_progress: bool = False):
        return self.model().encode([self._prefix("passage") + t for t in texts],
                                   batch_size=batch_size, normalize_embeddings=True,
                                   convert_to_numpy=True, show_progress_bar=show_progress)

    # --- построение / загрузка -------------------------------------------------------
    def build(self, records: list[dict], max_chars: int = 2000, show_progress: bool = True) -> int:
        import numpy as np
        chunks = [r for r in records if r.get("is_chunk")]
        texts = [chunk_text(r)[:max_chars] for r in chunks]
        matrix = self.encode_passages(texts, show_progress=show_progress).astype("float32")
        self.ids = [r["unit_id"] for r in chunks]
        self.matrix = matrix
        self.index_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.path, matrix=matrix, ids=np.array(self.ids, dtype=object),
                            model=np.array([self.model_name]))
        (self.index_dir / f"{_safe_name(self.model_name)}.json").write_text(
            json.dumps({"model": self.model_name, "chunks": len(self.ids), "dim": int(matrix.shape[1]),
                        "max_chars": max_chars}, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(self.ids)

    def load(self) -> bool:
        import numpy as np
        if not self.path.exists():
            return False
        data = np.load(self.path, allow_pickle=True)
        self.matrix = data["matrix"]
        self.ids = list(data["ids"])
        return True

    @property
    def ready(self) -> bool:
        return self.matrix is not None or self.path.exists()

    # --- поиск ----------------------------------------------------------------------
    def search(self, query: str, limit: int = 20, allowed: set[str] | None = None) -> list[tuple[str, float]]:
        """[(unit_id, cos)] по убыванию; allowed — ограничение множеством действующих чанков."""
        import numpy as np
        if self.matrix is None and not self.load():
            return []
        q = self.encode_queries([query])[0]
        scores = self.matrix @ q
        order = np.argsort(-scores)
        out = []
        for i in order:
            uid = self.ids[i]
            if allowed is not None and uid not in allowed:
                continue
            out.append((uid, float(scores[i])))
            if len(out) >= limit:
                break
        return out


def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score = Σ 1/(k + rank_i) по каждому списку."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, uid in enumerate(ranking, start=1):
            scores[uid] = scores.get(uid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1])


# --- pgvector (прод): та же матрица в таблице unit_embedding ------------------------------

def has_pgvector(conn) -> bool:
    row = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
    return bool(row) and bool(conn.execute("SELECT to_regclass('unit_embedding') IS NOT NULL AS ok").fetchone()["ok"])


def load_embeddings_db(conn, index: DenseIndex, batch: int = 500) -> int:
    """npz-индекс -> unit_embedding (полная замена строк этой модели). Нужен pgvector."""
    if index.matrix is None and not index.load():
        raise RuntimeError("индекс не построен: python -m taxcorpus embed")
    if not has_pgvector(conn):
        raise RuntimeError("pgvector не установлен (миграция 006 пропустила таблицу)")
    known = {r["unit_id"] for r in conn.execute("SELECT unit_id FROM unit").fetchall()}
    dim = int(index.matrix.shape[1])
    rows = [(uid, index.model_name, dim, "[" + ",".join(f"{x:.6f}" for x in vec) + "]")
            for uid, vec in zip(index.ids, index.matrix) if uid in known]
    with conn.transaction():
        conn.execute("DELETE FROM unit_embedding WHERE model = %s", (index.model_name,))
        with conn.cursor() as cur:
            for i in range(0, len(rows), batch):
                cur.executemany("INSERT INTO unit_embedding (unit_id, model, dim, vec) VALUES (%s, %s, %s, %s::vector)",
                                rows[i:i + batch])
    return len(rows)


class PgDenseIndex(DenseIndex):
    """Семантический поиск через pgvector: косинус по unit_embedding вместо матрицы в памяти."""

    def __init__(self, conn, model_name: str = DEFAULT_MODEL):
        super().__init__(model_name)
        self.conn = conn

    @property
    def ready(self) -> bool:
        try:
            return has_pgvector(self.conn) and bool(self.conn.execute(
                "SELECT 1 FROM unit_embedding WHERE model = %s LIMIT 1", (self.model_name,)).fetchone())
        except Exception:  # noqa: BLE001
            return False

    def search(self, query: str, limit: int = 20, allowed: set[str] | None = None) -> list[tuple[str, float]]:
        q = self.encode_queries([query])[0]
        vec = "[" + ",".join(f"{x:.6f}" for x in q) + "]"
        rows = self.conn.execute(
            "SELECT unit_id, 1 - (vec <=> %s::vector) AS cos FROM unit_embedding WHERE model = %s "
            "ORDER BY vec <=> %s::vector LIMIT %s",
            (vec, self.model_name, vec, limit * 4 if allowed is not None else limit)).fetchall()
        out = [(r["unit_id"], float(r["cos"])) for r in rows if allowed is None or r["unit_id"] in allowed]
        return out[:limit]
