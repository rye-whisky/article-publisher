#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI entry point for the article publisher pipeline."""

import argparse
import json
import sys
from pathlib import Path

from services.pipeline_service import PipelineService
from utils.logging_config import setup_logging


def main():
    parser = argparse.ArgumentParser(description="ChainThink Article Publisher Pipeline")
    parser.add_argument("--source", choices=["stcn", "techflow", "blockbeats", "chaincatcher", "odaily", "bestblogs", "all"], default="all")
    parser.add_argument("--since-today-0700", action="store_true")
    parser.add_argument("--republish", nargs="*", default=[])
    parser.add_argument("--republish-refetched", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--refetch-stcn-url", nargs="*", default=[])
    parser.add_argument("--refetch-techflow-id", nargs="*", default=[])
    parser.add_argument("--refetch-blockbeats-url", nargs="*", default=[])
    parser.add_argument("--refetch-chaincatcher-url", nargs="*", default=[])
    parser.add_argument("--refetch-odaily-url", nargs="*", default=[])
    parser.add_argument("--refetch-bestblogs-url", nargs="*", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    setup_logging(base_dir)

    svc = PipelineService.create(base_dir)
    result = svc.run(
        source=args.source,
        since_today_0700=args.since_today_0700,
        republish_ids=args.republish,
        skip_fetch=args.skip_fetch,
        refetch_stcn_urls=args.refetch_stcn_url or None,
        refetch_techflow_ids=args.refetch_techflow_id or None,
        refetch_blockbeats_urls=args.refetch_blockbeats_url or None,
        refetch_chaincatcher_urls=args.refetch_chaincatcher_url or None,
        refetch_odaily_urls=args.refetch_odaily_url or None,
        refetch_bestblogs_urls=args.refetch_bestblogs_url or None,
        dry_run=args.dry_run,
        republish_refetched=args.republish_refetched,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
