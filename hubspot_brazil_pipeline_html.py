#!/usr/bin/env python3
"""
Generate a self-contained HTML dashboard of Brazil Pipeline deals.

The script queries HubSpot, then writes an HTML file you can open locally in
your browser (no server, no CORS). Data is embedded as JSON inside the page,
so it works offline after generation.

Usage:
    export HUBSPOT_TOKEN='pat-eu1-...'
    python hubspot_brazil_pipeline_html.py
    python hubspot_brazil_pipeline_html.py --pipeline "Brazil Pipeline" --output dashboard.html --open
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from hubspot_brazil_pipeline_report import (
    build_stage_map,
    fetch_deals_in_pipeline,
    find_pipeline,
    last_stage_before_lost,
    stage_is_closed_lost,
)


def stage_is_closed_won(stage_id, stage_map):
    if not stage_id:
        return False
    meta = stage_map.get(stage_id, {}).get("metadata", {})
    probability = str(meta.get("probability", "")).strip()
    is_closed = str(meta.get("isClosed", "")).lower() == "true"
    return is_closed and probability in ("1.0", "1")


def build_context(token: str, pipeline_label: str) -> dict:
    pipeline = find_pipeline(token, pipeline_label)
    stage_map = build_stage_map(pipeline)
    deals = fetch_deals_in_pipeline(token, pipeline["id"])

    rows = []
    lost_count = won_count = open_count = 0
    total_amount = 0.0
    lost_amount = 0.0
    agg_last_before_lost: dict[str, int] = {}
    agg_current_stage: dict[str, int] = {}

    for deal in deals:
        props = deal.get("properties", {}) or {}
        history = (deal.get("propertiesWithHistory", {}) or {}).get("dealstage", []) or []
        stage_id = props.get("dealstage")
        stage_label = stage_map.get(stage_id, {}).get("label") or stage_id or "—"

        is_lost = stage_is_closed_lost(stage_id, stage_map)
        is_won = stage_is_closed_won(stage_id, stage_map)
        last_before_id = last_stage_before_lost(history, stage_id, stage_map) if is_lost else None
        last_before_label = stage_map.get(last_before_id, {}).get("label") if last_before_id else None

        try:
            amount = float(props.get("amount") or 0)
        except ValueError:
            amount = 0.0
        total_amount += amount
        if is_lost:
            lost_count += 1
            lost_amount += amount
            key = last_before_label or "(sem histórico anterior)"
            agg_last_before_lost[key] = agg_last_before_lost.get(key, 0) + 1
        elif is_won:
            won_count += 1
        else:
            open_count += 1

        agg_current_stage[stage_label] = agg_current_stage.get(stage_label, 0) + 1

        rows.append({
            "id": deal.get("id"),
            "name": props.get("dealname") or "(sem nome)",
            "amount": amount,
            "createdate": props.get("createdate"),
            "closedate": props.get("closedate"),
            "stage_id": stage_id,
            "stage_label": stage_label,
            "status": "Lost" if is_lost else ("Won" if is_won else "Open"),
            "last_before_lost_id": last_before_id,
            "last_before_lost_label": last_before_label,
            "closed_lost_reason": props.get("closed_lost_reason"),
        })

    rows.sort(key=lambda r: (r["status"] != "Lost", -(r["amount"] or 0)))

    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "pipeline": {"id": pipeline["id"], "label": pipeline["label"]},
        "stages": [
            {
                "id": sid,
                "label": s["label"],
                "displayOrder": s.get("displayOrder"),
                "isClosed": str(s["metadata"].get("isClosed", "")).lower() == "true",
                "probability": s["metadata"].get("probability"),
            }
            for sid, s in sorted(stage_map.items(), key=lambda kv: kv[1].get("displayOrder") or 0)
        ],
        "totals": {
            "deals": len(deals),
            "lost": lost_count,
            "won": won_count,
            "open": open_count,
            "total_amount": total_amount,
            "lost_amount": lost_amount,
        },
        "agg_last_before_lost": sorted(
            [{"label": k, "count": v} for k, v in agg_last_before_lost.items()],
            key=lambda x: -x["count"],
        ),
        "agg_current_stage": sorted(
            [{"label": k, "count": v} for k, v in agg_current_stage.items()],
            key=lambda x: -x["count"],
        ),
        "rows": rows,
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Brazil Pipeline — HubSpot Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0f172a; --panel: #111827; --muted: #94a3b8; --text: #e5e7eb;
    --accent: #38bdf8; --lost: #ef4444; --won: #22c55e; --open: #eab308;
    --border: #1f2937;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         background: var(--bg); color: var(--text); }
  header { padding: 20px 28px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: baseline; gap: 16px; flex-wrap: wrap; }
  header h1 { margin: 0; font-size: 20px; font-weight: 600; }
  header .sub { color: var(--muted); font-size: 13px; }
  main { padding: 24px 28px; max-width: 1400px; margin: 0 auto; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 28px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
  .card .label { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
  .card .value { font-size: 26px; font-weight: 600; margin-top: 6px; }
  .card .sub { font-size: 12px; color: var(--muted); margin-top: 4px; }
  .lost .value { color: var(--lost); }
  .won  .value { color: var(--won); }
  .open .value { color: var(--open); }
  section { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 18px 20px; margin-bottom: 24px; }
  section h2 { margin: 0 0 14px; font-size: 15px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  .charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 900px) { .charts { grid-template-columns: 1fr; } }
  canvas { max-height: 340px; }
  .toolbar { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
  .toolbar input, .toolbar select {
    background: #0b1220; color: var(--text); border: 1px solid var(--border);
    padding: 8px 10px; border-radius: 6px; font-size: 13px;
  }
  .toolbar input { min-width: 240px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 9px 10px; text-align: left; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; cursor: pointer; user-select: none; white-space: nowrap; }
  th[data-sort]:hover { color: var(--accent); }
  tbody tr:hover { background: #0b1220; }
  .status { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }
  .status.Lost { background: rgba(239, 68, 68, 0.15); color: var(--lost); }
  .status.Won  { background: rgba(34, 197, 94, 0.15); color: var(--won); }
  .status.Open { background: rgba(234, 179, 8, 0.15); color: var(--open); }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .muted { color: var(--muted); }
  .count-pill { background: rgba(56,189,248,0.1); color: var(--accent); padding: 1px 8px; border-radius: 999px; font-size: 12px; margin-left: 6px; }
</style>
</head>
<body>
<header>
  <div>
    <h1>Brazil Pipeline — HubSpot Deal Report</h1>
    <div class="sub" id="sub"></div>
  </div>
  <div class="sub" id="gen"></div>
</header>
<main>
  <div class="cards">
    <div class="card"><div class="label">Total de Deals</div><div class="value" id="c-total"></div><div class="sub" id="c-total-amount"></div></div>
    <div class="card open"><div class="label">Abertos</div><div class="value" id="c-open"></div></div>
    <div class="card won"><div class="label">Ganhos</div><div class="value" id="c-won"></div></div>
    <div class="card lost"><div class="label">Perdidos</div><div class="value" id="c-lost"></div><div class="sub" id="c-lost-amount"></div></div>
  </div>

  <section>
    <h2>Last Stage Before Lost</h2>
    <div class="charts">
      <canvas id="chart-last-before"></canvas>
      <canvas id="chart-current-stage"></canvas>
    </div>
  </section>

  <section>
    <h2>Deals <span class="count-pill" id="row-count"></span></h2>
    <div class="toolbar">
      <input id="search" placeholder="Buscar por nome, stage, motivo…" />
      <select id="filter-status">
        <option value="">Todos os status</option>
        <option value="Lost">Somente Lost</option>
        <option value="Won">Somente Won</option>
        <option value="Open">Somente Open</option>
      </select>
      <select id="filter-last-before">
        <option value="">Todos os 'last stage before lost'</option>
      </select>
    </div>
    <div style="overflow-x: auto;">
      <table id="deals-table">
        <thead>
          <tr>
            <th data-sort="name">Deal</th>
            <th data-sort="amount" class="num">Amount</th>
            <th data-sort="status">Status</th>
            <th data-sort="stage_label">Current Stage</th>
            <th data-sort="last_before_lost_label">Last Stage Before Lost</th>
            <th data-sort="closed_lost_reason">Motivo</th>
            <th data-sort="closedate">Close Date</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </section>
</main>

<script id="payload" type="application/json">__DATA__</script>
<script>
  const DATA = JSON.parse(document.getElementById('payload').textContent);
  const fmtMoney = v => (v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 });
  const fmtDate  = s => s ? new Date(s).toLocaleDateString('pt-BR') : '—';

  // Header
  document.getElementById('sub').textContent = `Pipeline: ${DATA.pipeline.label} (id=${DATA.pipeline.id})`;
  document.getElementById('gen').textContent = `Gerado em ${new Date(DATA.generated_at).toLocaleString('pt-BR')}`;

  // Cards
  document.getElementById('c-total').textContent = DATA.totals.deals;
  document.getElementById('c-total-amount').textContent = fmtMoney(DATA.totals.total_amount);
  document.getElementById('c-open').textContent = DATA.totals.open;
  document.getElementById('c-won').textContent = DATA.totals.won;
  document.getElementById('c-lost').textContent = DATA.totals.lost;
  document.getElementById('c-lost-amount').textContent = fmtMoney(DATA.totals.lost_amount) + ' perdidos';

  // Charts
  const chartOpts = {
    indexAxis: 'y',
    plugins: { legend: { display: false }, title: { display: true, color: '#94a3b8' } },
    scales: {
      x: { ticks: { color: '#94a3b8' }, grid: { color: '#1f2937' } },
      y: { ticks: { color: '#e5e7eb' }, grid: { display: false } },
    },
  };

  new Chart(document.getElementById('chart-last-before'), {
    type: 'bar',
    data: {
      labels: DATA.agg_last_before_lost.map(x => x.label),
      datasets: [{
        label: 'Deals perdidos',
        data: DATA.agg_last_before_lost.map(x => x.count),
        backgroundColor: 'rgba(239,68,68,0.7)',
        borderColor: 'rgba(239,68,68,1)', borderWidth: 1,
      }],
    },
    options: { ...chartOpts, plugins: { ...chartOpts.plugins, title: { ...chartOpts.plugins.title, text: 'Stage anterior ao Lost' } } },
  });

  new Chart(document.getElementById('chart-current-stage'), {
    type: 'bar',
    data: {
      labels: DATA.agg_current_stage.map(x => x.label),
      datasets: [{
        label: 'Deals',
        data: DATA.agg_current_stage.map(x => x.count),
        backgroundColor: 'rgba(56,189,248,0.7)',
        borderColor: 'rgba(56,189,248,1)', borderWidth: 1,
      }],
    },
    options: { ...chartOpts, plugins: { ...chartOpts.plugins, title: { ...chartOpts.plugins.title, text: 'Distribuição por stage atual' } } },
  });

  // Filter dropdown
  const selectLB = document.getElementById('filter-last-before');
  DATA.agg_last_before_lost.forEach(x => {
    const o = document.createElement('option'); o.value = x.label; o.textContent = `${x.label} (${x.count})`;
    selectLB.appendChild(o);
  });

  // Table
  const tbody = document.querySelector('#deals-table tbody');
  let sortKey = 'amount', sortDir = -1;

  function rowMatches(r, q, status, lb) {
    if (status && r.status !== status) return false;
    if (lb && (r.last_before_lost_label || '') !== lb) return false;
    if (q) {
      const hay = [r.name, r.stage_label, r.last_before_lost_label, r.closed_lost_reason, r.status].join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  function render() {
    const q = document.getElementById('search').value.toLowerCase().trim();
    const status = document.getElementById('filter-status').value;
    const lb = document.getElementById('filter-last-before').value;
    const filtered = DATA.rows.filter(r => rowMatches(r, q, status, lb));
    filtered.sort((a, b) => {
      const av = a[sortKey] ?? '', bv = b[sortKey] ?? '';
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * sortDir;
      return String(av).localeCompare(String(bv), 'pt-BR') * sortDir;
    });
    document.getElementById('row-count').textContent = `${filtered.length} / ${DATA.rows.length}`;
    tbody.innerHTML = filtered.map(r => `
      <tr>
        <td>${escapeHtml(r.name)} <span class="muted">#${r.id}</span></td>
        <td class="num">${fmtMoney(r.amount)}</td>
        <td><span class="status ${r.status}">${r.status}</span></td>
        <td>${escapeHtml(r.stage_label || '—')}</td>
        <td>${escapeHtml(r.last_before_lost_label || '—')}</td>
        <td>${escapeHtml(r.closed_lost_reason || '—')}</td>
        <td>${fmtDate(r.closedate)}</td>
      </tr>
    `).join('');
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
  }

  document.querySelectorAll('th[data-sort]').forEach(th => {
    th.addEventListener('click', () => {
      const key = th.dataset.sort;
      if (sortKey === key) sortDir *= -1; else { sortKey = key; sortDir = 1; }
      render();
    });
  });
  ['search', 'filter-status', 'filter-last-before'].forEach(id =>
    document.getElementById(id).addEventListener('input', render)
  );

  render();
</script>
</body>
</html>
"""


def render_html(context: dict) -> str:
    payload = json.dumps(context, ensure_ascii=False, default=str)
    # Escape `</script>` just in case a deal name contains it.
    payload = payload.replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__DATA__", payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate an HTML dashboard for Brazil Pipeline deals.")
    parser.add_argument("--pipeline", default="Brazil Pipeline", help="Pipeline label")
    parser.add_argument("--output", default="brazil_pipeline_report.html", help="HTML output path")
    parser.add_argument("--open", action="store_true", help="Open the generated file in your default browser")
    args = parser.parse_args()

    token = os.environ.get("HUBSPOT_TOKEN")
    if not token:
        print("ERROR: set HUBSPOT_TOKEN environment variable.", file=sys.stderr)
        return 1

    print(f"Fetching pipeline '{args.pipeline}' from HubSpot...")
    context = build_context(token, args.pipeline)
    print(f"  {context['totals']['deals']} deals  "
          f"({context['totals']['open']} open / {context['totals']['won']} won / {context['totals']['lost']} lost)")

    html = render_html(context)
    out_path = Path(args.output).resolve()
    out_path.write_text(html, encoding="utf-8")
    print(f"Wrote {out_path}")

    if args.open:
        webbrowser.open(out_path.as_uri())

    return 0


if __name__ == "__main__":
    sys.exit(main())
