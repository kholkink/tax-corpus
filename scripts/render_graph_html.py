"""Собирает автономную HTML-визуализацию графа НК РФ (оффлайн, без CDN).

Запуск: python scripts/render_graph_html.py
Вход:  data/processed/nk1_graph.json, reports/assets/vis-network.min.js
Выход: reports/nk1_graph.html
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data/processed/nk1_graph.json"
VIS_JS = ROOT / "reports/assets/vis-network.min.js"
OUT = ROOT / "reports/nk1_graph.html"

TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Граф НК РФ ч.1 — структурные единицы и перекрёстные ссылки</title>
<style>
  html, body { margin:0; height:100%; font-family:'Segoe UI', Arial, sans-serif; }
  #layout { display:flex; height:100%; }
  #panel { width:320px; padding:14px; box-sizing:border-box; background:#f6f8fa;
           border-right:1px solid #d0d7de; overflow-y:auto; flex-shrink:0; }
  #panel h1 { font-size:16px; margin:0 0 4px; }
  #panel .sub { color:#57606a; font-size:12px; margin-bottom:12px; }
  .stats { font-size:12px; line-height:1.6; background:#fff; border:1px solid #d0d7de;
           border-radius:8px; padding:8px 10px; margin-bottom:12px; }
  .ctl { margin-bottom:10px; font-size:13px; }
  .ctl label { display:block; font-weight:600; margin-bottom:3px; }
  .ctl input[type=text] { width:100%; box-sizing:border-box; padding:6px 8px;
           border:1px solid #d0d7de; border-radius:6px; font-size:13px; }
  .ctl input[type=range] { width:100%; }
  .ctl label.inline { font-weight:400; display:flex; gap:6px; align-items:center; }
  #info { font-size:12px; background:#fff; border:1px solid #d0d7de; border-radius:8px;
          padding:8px 10px; margin-top:12px; min-height:60px; }
  #info b { font-size:13px; }
  .legend { font-size:11px; line-height:1.7; }
  .legend .sw { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; }
  #net { flex:1; background:#fafbfc; }
  .hint { color:#57606a; font-size:11px; margin-top:10px; }
</style>
</head>
<body>
<div id="layout">
  <div id="panel">
    <h1>Налоговый кодекс РФ (обе части)</h1>
    <div class="sub">граф перекрёстных ссылок между статьями · ч.1 ред. №199 от 04.08.2026, ч.2 ред. №376 от 04.08.2026 · банк ГАС Минюста</div>
    <div class="stats" id="stats"></div>
    <div class="ctl">
      <label for="q">Поиск статьи (номер или заголовок)</label>
      <input type="text" id="q" placeholder="например: 54.1, ЕНС, камеральн">
    </div>
    <div class="ctl">
      <label for="minw">Минимум ссылок в связи: <span id="minw_val">1</span></label>
      <input type="range" id="minw" min="1" max="10" value="1">
    </div>
    <div class="ctl">
      <label class="inline"><input type="checkbox" id="tree"> Показать главы (иерархия)</label>
      <label class="inline"><input type="checkbox" id="inter"> Только межразделовые связи</label>
      <label class="inline"><input type="checkbox" id="linked" checked> Скрыть статьи без связей</label>
    </div>
    <div id="info">Кликните по статье, чтобы увидеть её связи.</div>
    <div class="legend" id="legend"></div>
    <div class="hint">Колесо — масштаб, тянуть фон — панорама. Цвет статьи = раздел кодекса,
    размер = суммарное число ссылок. Ищите «статьи 2211»? — это сплющенный номер банка
    (22.1-1), в графе уже восстановлен.</div>
  </div>
  <div id="net"></div>
</div>
<script>__VIS_JS__</script>
<script>
const GRAPH = __GRAPH_JSON__;
const SECTION_COLORS = {
  "I":"#1f77b4","II":"#ff7f0e","III":"#2ca02c","IV":"#d62728","V":"#9467bd",
  "V.1":"#8c564b","V.2":"#e377c2","VI":"#bcbd22","VII":"#17becf","VII.1":"#7f7f7f",
  "VIII":"#393b79","VIII.1":"#ce6dbd","IX":"#9c9ede","X":"#637939","XI":"#e7969c"
};
const stats = GRAPH.stats;
document.getElementById('stats').innerHTML =
  'Статей: <b>' + stats.articles + '</b> · Глав: <b>' + GRAPH.chapters.length + '</b><br>' +
  'Ссылок всего: <b>' + stats.references_total + '</b><br>' +
  '· статья→статья: <b>' + (stats.resolved + stats.self_loops) + '</b> ' +
  '(рёбер: ' + stats.edges + ')<br>' +
  '· на главу/раздел: ' + stats.chapter_level + '<br>' +
  '· из шапки акта: ' + stats.structure_level + '<br>' +
  '· неразрешённых: ' + stats.unresolved + ' (другая часть / дефект подачи)';

const secTitle = {};
GRAPH.sections.forEach(s => secTitle[s.num] = s.title || '');
let legendHtml = '<div style="font-weight:600;margin:10px 0 4px;">Разделы</div>';
Object.keys(SECTION_COLORS).forEach(s => {
  const t = (secTitle[s] || '').toLowerCase();
  const short = t.length > 34 ? t.slice(0, 34) + '…' : t;
  legendHtml += '<div><span class="sw" style="background:' + SECTION_COLORS[s] + '"></span>Раздел ' + s +
    (short ? ' — ' + short : '') + '</div>';
});
document.getElementById('legend').innerHTML = legendHtml;

const nodes = [];
const artByNum = {};
GRAPH.nodes.forEach(n => {
  artByNum[n.num] = n;
  nodes.push({
    id: n.id, label: 'ст. ' + n.num, group: n.section,
    value: 3 + n.in + n.out,
    title: 'ст. ' + n.num + ' НК РФ — ' + (n.title || '') +
           '\\nвходящих: ' + n.in + ' · исходящих: ' + n.out +
           '\\nглава ' + n.chapter + ', раздел ' + n.section,
    chapter: n.chapter, section: n.section, num: n.num, fullTitle: n.title || ''
  });
});
const chapterNodes = {};
GRAPH.chapters.forEach(c => {
  const act = String(c.id).split('.')[0] === 'nk1' ? 'ч.1' : 'ч.2';
  chapterNodes[c.id] = {
    id: c.id, label: act + ' гл.' + c.num, group: 'chapter',
    value: 10, shape: 'box', title: act + ' Глава ' + c.num + ' — ' + (c.title || ''),
    chapter: c.num, num: c.num, fullTitle: c.title || '', section: null
  };
});
const treeEdges = [];
const chByNum = {};
GRAPH.chapters.forEach(c => {
  chByNum[String(c.id).split('.')[0] + '|' + c.num] = c.id;
});
GRAPH.nodes.forEach(n => {
  const ch = chByNum[String(n.id).split('.')[0] + '|' + n.chapter];
  if (ch) treeEdges.push({ from: ch, to: n.id, kind: 'tree' });
});
const citeEdges = GRAPH.edges.map(e => ({
  from: e.from, to: e.to, kind: 'cite', w: e.w,
  arrows: { to: { enabled: true, scaleFactor: 0.4 } },
  title: 'ссылок: ' + e.w
}));

const network = new vis.Network(
  document.getElementById('net'),
  { nodes: new vis.DataSet(nodes), edges: new vis.DataSet(citeEdges) },
  {
    autoResize: true, height: '100%', width: '100%',
    nodes: {
      shape: 'dot', size: 8,
      color: { highlight: '#ff5722', hover: '#ff8a50' },
      font: { size: 9, color: '#333' }, borderWidth: 0
    },
    groups: {
      chapter: { color: { background: '#34495e' }, font: { color: 'white', size: 12 }, shape: 'box' },
    },
    edges: {
      color: { color: 'rgba(120,130,150,0.35)', highlight: '#e67e22', hover: '#e67e22' },
      smooth: { type: 'continuous', roundness: 0.25 },
      selectionWidth: 1.6
    },
    physics: {
      solver: 'barnesHut',
      barnesHut: { gravitationalConstant: -3600, centralGravitation: 0.25,
                   springLength: 95, springConstant: 0.045, avoidOverlap: 0.15 },
      stabilization: { iterations: 320, fit: true }
    },
    interaction: { hover: true, tooltipDelay: 120, navigationButtons: false, keyboard: true }
  }
);

const groupColors = {};
Object.keys(SECTION_COLORS).forEach(s => groupColors[s] = SECTION_COLORS[s]);
nodes.forEach(n => { if (groupColors[n.group]) n.color = groupColors[n.group]; });

let allNodes = nodes.concat(Object.values(chapterNodes));
let showTree = false, minW = 1, interOnly = false, onlyLinked = true, query = '';

function rebuild() {
  const allowedIds = new Set();
  const nodeSet = new vis.DataSet();
  const edgeSet = new vis.DataSet();
  const visibleArticles = {};

  GRAPH.nodes.forEach(n => {
    const ok = !query || n.num.includes(query) || (n.title || '').toLowerCase().includes(query.toLowerCase());
    visibleArticles[n.id] = ok;
  });

  allNodes.forEach(n => {
    if (n.group === 'chapter') {
      if (showTree) nodeSet.add(n);
      return;
    }
    if (query && !visibleArticles[n.id]) return;
    if (onlyLinked && !query && !(n.in + n.out)) return;
    nodeSet.add(n);
  });

  citeEdges.forEach(e => {
    if (e.w < minW) return;
    if (interOnly) {
      const s = allNodes.find(x => x.id === e.from), d = allNodes.find(x => x.id === e.to);
      if (!s || !d || s.section === d.section) return;
    }
    if (query && (!visibleArticles[e.from] || !visibleArticles[e.to])) return;
    edgeSet.add({ ...e, width: 1 + Math.min(e.w, 8) * 0.7,
                  value: e.w, color: { color: e.w >= 5 ? 'rgba(200,80,40,0.55)' : undefined } });
  });

  if (showTree) {
    treeEdges.forEach(e => {
      if (nodeSet.get(e.to) && nodeSet.get(e.from)) {
        edgeSet.add({ from: e.from, to: e.to, kind: 'tree', dashes: true,
                      width: 0.5, color: { color: 'rgba(100,110,130,0.25)' },
                      physics: false });
      }
    });
  }

  network.setData({ nodes: nodeSet, edges: edgeSet });
}

document.getElementById('tree').addEventListener('change', e => { showTree = e.target.checked; rebuild(); });
document.getElementById('inter').addEventListener('change', e => { interOnly = e.target.checked; rebuild(); });
document.getElementById('linked').addEventListener('change', e => { onlyLinked = e.target.checked; rebuild(); });
document.getElementById('minw').addEventListener('input', e => {
  minW = +e.target.value; document.getElementById('minw_val').textContent = minW; rebuild();
});
let qTimer = null;
document.getElementById('q').addEventListener('input', e => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => { query = e.target.value.trim(); rebuild();
    if (query) network.fit({ animation: true }); }, 250);
});

network.on('click', params => {
  const info = document.getElementById('info');
  if (!params.nodes.length) { info.innerHTML = 'Кликните по статье, чтобы увидеть её связи.'; return; }
  const node = allNodes.find(n => n.id === params.nodes[0]);
  if (!node) return;
  const conn = GRAPH.edges.filter(e => e.from === node.id || e.to === node.id);
  let html = '<b>ст. ' + node.num + ' НК РФ</b><br>' + (node.fullTitle || '') +
    '<br><span style="color:#57606a">глава ' + node.chapter + ' · раздел ' + node.section +
    ' · входящих ' + node.in + ' · исходящих ' + node.out + '</span>';
  if (conn.length) {
    html += '<div style="margin-top:6px">' + conn.length + ' связей; крупнейшие:</div><ul style="margin:4px 0 0;padding-left:16px">';
    conn.sort((a, b) => b.w - a.w).slice(0, 5).forEach(e => {
      const otherId = e.from === node.id ? e.to : e.from;
      const other = artByNum[otherId.replace(/@\\d+$/, '')] || GRAPH.nodes.find(n => n.id === otherId);
      html += '<li>' + (e.from === node.id ? '→ ' : '← ') + 'ст. ' +
        (other ? other.num : otherId) + ' (' + e.w + ')</li>';
    });
    html += '</ul>';
  }
  info.innerHTML = html;
  network.selectNodes([node.id]);
});

rebuild();
</script>
</body>
</html>
"""


def main() -> int:
    graph = json.loads(DATA.read_text(encoding="utf-8"))
    vis_js = VIS_JS.read_text(encoding="utf-8")
    # не допускаем преждевременного закрытия тега script содержимым бандла
    vis_js = vis_js.replace("</script>", "<\\/script>")
    html = TEMPLATE.replace("__VIS_JS__", vis_js).replace(
        "__GRAPH_JSON__", json.dumps(graph, ensure_ascii=False))
    OUT.write_text(html, encoding="utf-8")
    print(f"написано: {OUT} ({OUT.stat().st_size / 1024:.0f} КБ), "
          f"узлов: {len(graph['nodes'])}, рёбер: {len(graph['edges'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
