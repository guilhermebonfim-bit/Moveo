#!/usr/bin/env python3
"""
HubSpot — Brazil Pipeline deals report.

Lists all deals in the 'Brazil Pipeline' and, for deals in a closed-lost
stage, identifies the last non-lost stage the deal was in before being
moved to Lost (using the dealstage property history).

Usage:
    export HUBSPOT_TOKEN='pat-eu1-...'
    python hubspot_brazil_pipeline_report.py
    python hubspot_brazil_pipeline_report.py --pipeline "Brazil Pipeline" --output report.csv
    python hubspot_brazil_pipeline_report.py --only-lost
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import Any, Dict, List, Optional

import requests

HUBSPOT_BASE = "https://api.hubapi.com"
DEFAULT_PIPELINE = "Brazil Pipeline"


def hs_get(token: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    resp = requests.get(
        f"{HUBSPOT_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=30,
    )
    if not resp.ok:
        raise SystemExit(f"HubSpot GET {path} -> {resp.status_code}: {resp.text}")
    return resp.json()


def hs_post(token: str, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
    resp = requests.post(
        f"{HUBSPOT_BASE}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    if not resp.ok:
        raise SystemExit(f"HubSpot POST {path} -> {resp.status_code}: {resp.text}")
    return resp.json()


def find_pipeline(token: str, name: str) -> Dict[str, Any]:
    data = hs_get(token, "/crm/v3/pipelines/deals")
    pipelines = data.get("results", [])
    target = name.strip().lower()
    for p in pipelines:
        if p.get("label", "").strip().lower() == target:
            return p
    for p in pipelines:
        if target in p.get("label", "").strip().lower():
            return p
    available = ", ".join(repr(p.get("label")) for p in pipelines)
    raise SystemExit(f"Pipeline {name!r} not found. Available: {available}")


def build_stage_map(pipeline: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        s["id"]: {
            "label": s.get("label"),
            "displayOrder": s.get("displayOrder"),
            "metadata": s.get("metadata", {}) or {},
        }
        for s in pipeline.get("stages", [])
    }


def stage_is_closed_lost(stage_id: Optional[str], stage_map: Dict[str, Dict[str, Any]]) -> bool:
    if not stage_id:
        return False
    meta = stage_map.get(stage_id, {}).get("metadata", {})
    probability = str(meta.get("probability", "")).strip()
    is_closed = str(meta.get("isClosed", "")).lower() == "true"
    if is_closed and probability in ("0.0", "0"):
        return True
    return stage_id == "closedlost"


def fetch_deals_in_pipeline(token: str, pipeline_id: str) -> List[Dict[str, Any]]:
    """Fetch all deals in a pipeline with dealstage history."""
    deals: List[Dict[str, Any]] = []
    after: Optional[str] = None
    while True:
        body: Dict[str, Any] = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "pipeline", "operator": "EQ", "value": pipeline_id}
                    ]
                }
            ],
            "properties": [
                "dealname",
                "dealstage",
                "pipeline",
                "amount",
                "closedate",
                "createdate",
                "hubspot_owner_id",
                "closed_lost_reason",
            ],
            "propertiesWithHistory": ["dealstage"],
            "limit": 100,
            "sorts": [{"propertyName": "createdate", "direction": "DESCENDING"}],
        }
        if after:
            body["after"] = after
        payload = hs_post(token, "/crm/v3/objects/deals/search", body)
        deals.extend(payload.get("results", []))
        nxt = payload.get("paging", {}).get("next")
        if not nxt:
            break
        after = nxt.get("after")
    return deals


def last_stage_before_lost(
    history: List[Dict[str, Any]],
    current_stage: Optional[str],
    stage_map: Dict[str, Dict[str, Any]],
) -> Optional[str]:
    """
    HubSpot returns `propertiesWithHistory.dealstage` ordered most-recent first.
    Walk the history and return the first value that is NOT the current stage
    and NOT another closed-lost stage.
    """
    for entry in history or []:
        value = entry.get("value")
        if not value or value == current_stage:
            continue
        if stage_is_closed_lost(value, stage_map):
            continue
        return value
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline", default=DEFAULT_PIPELINE, help="Pipeline label to query")
    parser.add_argument("--output", help="Optional CSV output path")
    parser.add_argument("--only-lost", action="store_true", help="Only include lost deals in output")
    args = parser.parse_args()

    token = os.environ.get("HUBSPOT_TOKEN")
    if not token:
        print("ERROR: set HUBSPOT_TOKEN environment variable.", file=sys.stderr)
        return 1

    print(f"Locating pipeline '{args.pipeline}'...")
    pipeline = find_pipeline(token, args.pipeline)
    stage_map = build_stage_map(pipeline)
    print(f"  -> {pipeline['label']!r} (id={pipeline['id']}, stages={len(stage_map)})")

    print("Fetching deals...")
    deals = fetch_deals_in_pipeline(token, pipeline["id"])
    print(f"  -> {len(deals)} deals")

    rows: List[Dict[str, Any]] = []
    lost_count = 0
    for deal in deals:
        props = deal.get("properties", {}) or {}
        history = (deal.get("propertiesWithHistory", {}) or {}).get("dealstage", []) or []
        stage_id = props.get("dealstage")
        is_lost = stage_is_closed_lost(stage_id, stage_map)
        last_before_id = last_stage_before_lost(history, stage_id, stage_map) if is_lost else None

        rows.append({
            "deal_id": deal.get("id"),
            "dealname": props.get("dealname"),
            "amount": props.get("amount"),
            "createdate": props.get("createdate"),
            "closedate": props.get("closedate"),
            "current_stage_id": stage_id,
            "current_stage_label": stage_map.get(stage_id, {}).get("label"),
            "is_lost": is_lost,
            "last_stage_before_lost_id": last_before_id,
            "last_stage_before_lost_label": (
                stage_map.get(last_before_id, {}).get("label") if last_before_id else None
            ),
            "closed_lost_reason": props.get("closed_lost_reason"),
        })
        if is_lost:
            lost_count += 1

    if args.only_lost:
        rows = [r for r in rows if r["is_lost"]]

    print("\n=== Summary ===")
    print(f"Total deals: {len(deals)}")
    print(f"Lost deals:  {lost_count}")

    agg: Dict[str, int] = {}
    for r in rows:
        if r["is_lost"]:
            key = r["last_stage_before_lost_label"] or "(no prior stage in history)"
            agg[key] = agg.get(key, 0) + 1

    if agg:
        print("\n=== Last stage before Lost (count) ===")
        width = max(len(k) for k in agg) + 2
        for label, count in sorted(agg.items(), key=lambda kv: -kv[1]):
            print(f"  {label:<{width}} {count:>4}")

    if rows:
        print("\n=== Lost deals detail ===")
        for r in rows:
            if not r["is_lost"]:
                continue
            print(
                f"  #{r['deal_id']}  {r['dealname'] or '(no name)'}  "
                f"| last stage before lost: {r['last_stage_before_lost_label'] or '—'}  "
                f"| reason: {r['closed_lost_reason'] or '—'}"
            )

    if args.output:
        if not rows:
            print("\nNothing to write.")
        else:
            with open(args.output, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"\nWrote {len(rows)} rows to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
