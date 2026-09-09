"""Сборка графа корпуса для визуализации: единицы + ссылки -> JSON + агрегаты.

Запуск: python scripts/build_graph.py
Выход: data/processed/nk1_graph.json (для интерактивной HTML-визуализации),
reports/nk1_chapter_matrix.png, reports/nk1_top_cited.png, краткая статистика.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

DB = "postgresql://postgres@localhost:5432/taxcorpus"
OUT_JSON = Path("data/processed/nk1_graph.json")

# --- варианты написания номера в подаче банка: «22.1-1» могло стать «2211» ---
def number_variants(number: str) -> set[str]:
    variants = {number, number.replace(".", ""), number.replace("-", ""),
                number.replace(".", "").replace("-", "")}
    return {v for v in variants if v}


def ancestors_of(unit_id: str, parent_of: dict[str, str]) -> list[str]:
    chain = []
    cur = unit_id
    while cur in parent_of:
        cur = parent_of[cur]
        chain.append(cur)
    return chain


def article_of(unit_id: str, parent_of: dict[str, str], units: dict[str, dict]) -> str | None:
    """unit_id -> unit_id ближайшей статьи-предка (или самой статьи)."""
    if unit_id in units and units[unit_id]["kind"] == "article":
        return unit_id
    for anc in ancestors_of(unit_id, parent_of):
        if anc in units and units[anc]["kind"] == "article":
            return anc
    return None


def main() -> int:
    conn = psycopg.connect(DB, row_factory=dict_row)
    rows = conn.execute(
        "SELECT unit_id, kind, number, label, title, parent_unit_id FROM unit"
    ).fetchall()
    units = {r["unit_id"]: r for r in rows}
    conn.close()

    articles = {uid: u for uid, u in units.items() if u["kind"] == "article"}
    chapters = {uid: u for uid, u in units.items() if u["kind"] == "chapter"}
    sections = {uid: u for uid, u in units.items() if u["kind"] == "section"}

    parent_of = {uid: u["parent_unit_id"] for uid, u in units.items() if u["parent_unit_id"]}

    # индекс статей: точные номера + варианты банка. Коллизии вариантов
    # («61» = и настоящая ст. 61, и сплющенная 6.1) не допускаются:
    # сначала ищем точное совпадение, неоднозначные варианты отбрасываем
    by_number: dict[str, str] = {}
    for uid, u in articles.items():
        if u["number"]:
            by_number.setdefault(u["number"], uid)
    variant_map: dict[str, list[str]] = defaultdict(list)
    for uid, u in articles.items():
        for variant in number_variants(u["number"] or ""):
            if variant != u["number"]:
                variant_map[variant].append(uid)
    variant_flat = {k: v[0] for k, v in variant_map.items() if len(v) == 1}

    def resolve(cited: str) -> str | None:
        if cited in by_number:
            return by_number[cited]
        return variant_flat.get(cited)

    conn = psycopg.connect(DB, row_factory=dict_row)
    references = conn.execute(
        "SELECT from_unit_id, to_unit_id FROM reference WHERE to_unit_id IS NOT NULL"
    ).fetchall()
    conn.close()

    edges: dict[tuple[str, str], int] = defaultdict(int)
    resolved = self_loops = 0
    chapter_level = 0    # цель — глава/раздел (в графе статья-к-статье не участвует)
    structure_level = 0  # источник — сам акт (шапка-перечень изменений)
    unresolved = 0
    unresolved_samples: list[dict] = []

    article_level = {uid for uid, u in articles.items()}

    def to_article(unit_id: str | None) -> str | None:
        if unit_id is None:
            return None
        if unit_id in article_level:
            return unit_id
        return article_of(unit_id, parent_of, units)

    for ref in references:
        dst_unit = units.get(ref["to_unit_id"])
        if dst_unit is None:
            unresolved += 1
            continue
        if dst_unit["kind"] in ("chapter", "section"):
            chapter_level += 1
            continue
        src = to_article(ref["from_unit_id"])
        if src is None:
            structure_level += 1
            continue
        dst = to_article(ref["to_unit_id"])
        if dst is None:
            unresolved += 1
            if len(unresolved_samples) < 12:
                unresolved_samples.append({"from": ref["from_unit_id"], "to": ref["to_unit_id"]})
            continue
        resolved += 1
        if dst == src:
            self_loops += 1
            continue
        edges[(src, dst)] += 1

    # --- агрегаты по главам ---
    chapter_of_article = {}
    section_of_article = {}
    for uid in articles:
        for anc in [uid] + ancestors_of(uid, parent_of):
            if anc in chapters and uid not in chapter_of_article:
                chapter_of_article[uid] = anc
            if anc in sections and uid not in section_of_article:
                section_of_article[uid] = anc
            if uid in chapter_of_article and uid in section_of_article:
                break

    chapter_edges: dict[tuple[str, str], int] = defaultdict(int)
    for (src, dst), w in edges.items():
        cs, cd = chapter_of_article.get(src), chapter_of_article.get(dst)
        if cs and cd:
            chapter_edges[(cs, cd)] += w

    in_degree: dict[str, int] = defaultdict(int)
    out_degree: dict[str, int] = defaultdict(int)
    for (src, dst), w in edges.items():
        out_degree[src] += w
        in_degree[dst] += w

    top_cited = sorted(in_degree.items(), key=lambda kv: -kv[1])[:15]
    top_citing = sorted(out_degree.items(), key=lambda kv: -kv[1])[:15]

    # --- JSON для интерактивной визуализации ---
    node_list = []
    for uid, u in articles.items():
        ch = chapter_of_article.get(uid)
        sec = section_of_article.get(uid)
        node_list.append({
            "id": uid,
            "num": u["number"],
            "title": u["title"] or (u["label"].replace(" НК РФ", "")),
            "chapter": chapters[ch]["number"] if ch else None,
            "section": sections[sec]["number"] if sec else None,
            "in": in_degree.get(uid, 0),
            "out": out_degree.get(uid, 0),
        })
    graph = {
        "act": "nk1",
        "nodes": node_list,
        "edges": [{"from": s, "to": d, "w": w} for (s, d), w in edges.items()],
        "chapters": [{"id": uid, "num": u["number"], "title": u["title"]} for uid, u in chapters.items()],
        "sections": [{"id": uid, "num": u["number"], "title": u["title"]} for uid, u in sections.items()],
        "chapter_edges": [{"from": s, "to": d, "w": w} for (s, d), w in chapter_edges.items()],
        "stats": {
            "articles": len(articles),
            "references_total": resolved + unresolved + chapter_level + structure_level,
            "resolved": resolved,
            "unresolved": unresolved,
            "chapter_level": chapter_level,
            "structure_level": structure_level,
            "self_loops": self_loops,
            "edges": len(edges),
            "unresolved_samples": unresolved_samples,
        },
    }
    OUT_JSON.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    # --- статистика в stdout ---
    print(f"статей: {len(articles)}, глав: {len(chapters)}")
    print(f"ссылок всего: {resolved + unresolved + chapter_level + structure_level}")
    print(f"  статья->статья: {resolved + self_loops}; на главу/раздел: {chapter_level}; "
          f"из шапки акта: {structure_level}; неразрешённых: {unresolved}")
    print(f"уникальных рёбер (статья->статья): {len(edges)}; петель: {self_loops}")
    print("\nТоп-10 статей по числу входящих ссылок:")
    for uid, w in top_cited[:10]:
        u = articles[uid]
        print(f"  {w:4d}  ст. {u['number']} — {(u['title'] or '')[:55]}")
    print("\nПримеры неразрешённых ссылок:",
          [f"{s.get('from')} -> {s.get('to')}" for s in unresolved_samples[:5]])

    _plots(units, articles, chapters, chapter_of_article, chapter_edges,
           top_cited, parent_of)
    return 0


def _plots(units, articles, chapters, chapter_of_article, chapter_edges,
           top_cited, parent_of):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)

    # главы в порядке документа (числовой ключ с дробями)
    def ch_key(uid: str) -> tuple:
        parts = []
        for p in str(chapters[uid]["number"]).split("."):
            try:
                parts.append(int(p))
            except ValueError:
                continue
        return tuple(parts)

    ordered = sorted(chapters, key=ch_key)
    idx = {uid: i for i, uid in enumerate(ordered)}
    n = len(ordered)
    matrix = [[0] * n for _ in range(n)]
    for (src, dst), w in chapter_edges.items():
        if src in idx and dst in idx:
            matrix[idx[src]][idx[dst]] = w

    # акт виден в подписи: главы двух частей НК имеют одинаковые номера
    def ch_label(uid: str) -> str:
        act = "ч.1" if uid.startswith("nk1") else "ч.2"
        return f"{act} гл.{chapters[uid]['number']}"

    labels = [ch_label(uid) for uid in ordered]

    fig, ax = plt.subplots(figsize=(15, 13))
    cmap = LinearSegmentedColormap.from_list("cite", ["#f7f7f7", "#c6dbef", "#6baed6", "#2171b5", "#08306b"])
    im = ax.imshow(matrix, cmap=cmap)
    ax.set_xticks(range(n), labels, rotation=90, fontsize=6)
    ax.set_yticks(range(n), labels, fontsize=6)
    ax.set_xlabel("цитируемая глава")
    ax.set_ylabel("цитирующая глава")
    ax.set_title("НК РФ (обе части): перекрёстные ссылки между главами", fontsize=12)
    for i in range(n):
        for j in range(n):
            v = matrix[i][j]
            if v > 0:
                ax.text(j, i, str(v), ha="center", va="center",
                        fontsize=5.6, color="white" if v > 25 else "#333333")
    fig.colorbar(im, ax=ax, shrink=0.75, label="число ссылок")
    fig.tight_layout()
    fig.savefig(out_dir / "nk1_chapter_matrix.png", dpi=150)
    plt.close(fig)

    # --- топ цитируемых статей ---
    fig, ax = plt.subplots(figsize=(10, 6.5))
    names, values = [], []
    for uid, w in top_cited:
        u = articles[uid]
        names.append(f"ст. {u['number']}")
        values.append(w)
    y = range(len(names))[::-1]
    ax.barh(y, values, color="#2171b5")
    ax.set_yticks(y, names)
    for yi, v in zip(y, values):
        ax.text(v + 1, yi, str(v), va="center", fontsize=8)
    ax.set_xlabel("число входящих ссылок из других статей ч.1")
    ax.set_title("«Хабы» НК РФ ч.1: самые цитируемые статьи", fontsize=12)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / "nk1_top_cited.png", dpi=150)
    plt.close(fig)
    print(f"\nфайлы: {OUT_JSON}, {out_dir / 'nk1_chapter_matrix.png'}, {out_dir / 'nk1_top_cited.png'}")


if __name__ == "__main__":
    sys.exit(main())
