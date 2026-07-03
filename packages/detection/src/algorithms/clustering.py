"""
WalletClusterer — groups flagged ScoredEvents into WalletCluster objects.

Clustering logic:
  1. Group events by (pool_address, event_type)
  2. Within each group, find wallets that co-moved within
     CLUSTER_WINDOW_MINUTES of each other
  3. A cluster must have >= MIN_CLUSTER_SIZE wallets to qualify
  4. Single-wallet high-z events are also emitted as solo clusters
     if their z-score exceeds WHALE_THRESHOLD

This catches two distinct patterns:
  - Coordinated accumulation (multiple wallets, same pool, short window)
  - Single whale entry/exit (one wallet, very high z-score)
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterator

from src.models.candidate import ScoredEvent, WalletCluster

log = logging.getLogger(__name__)

CLUSTER_WINDOW_MINUTES = int(os.getenv("CLUSTER_WINDOW_MINUTES", "30"))
MIN_CLUSTER_SIZE       = int(os.getenv("MIN_CLUSTER_SIZE", "2"))
WHALE_THRESHOLD        = float(os.getenv("WHALE_THRESHOLD", "4.0"))


class WalletClusterer:
    """Groups ScoredEvents into WalletCluster objects."""

    def cluster(self, events: list[ScoredEvent]) -> list[WalletCluster]:
        """
        Given a list of flagged scored events, return wallet clusters.
        Events from multiple pools can produce multiple clusters.
        """
        if not events:
            return []

        clusters = []

        # Group by (pool_address, event_type)
        groups: dict[tuple, list[ScoredEvent]] = defaultdict(list)
        for e in events:
            groups[(e.pool_address, e.event_type)].append(e)

        for (pool, etype), group_events in groups.items():
            # Sort by timestamp
            group_events.sort(key=lambda e: e.timestamp)

            # Slide a time window and find co-moving wallets
            window_clusters = list(self._sliding_window_cluster(group_events))
            clusters.extend(window_clusters)

            # Also check for solo whales (single wallet, very high z-score)
            for e in group_events:
                if e.z_score >= WHALE_THRESHOLD:
                    # Check it's not already in a multi-wallet cluster
                    already_clustered = any(
                        e.wallet_address in c.wallets and c.wallet_count > 1
                        for c in clusters
                    )
                    if not already_clustered:
                        clusters.append(self._solo_cluster(e))

        log.info(
            "Clusterer: %d events → %d clusters",
            len(events), len(clusters),
        )
        return clusters

    def _sliding_window_cluster(
        self, events: list[ScoredEvent]
    ) -> Iterator[WalletCluster]:
        """
        Slide a CLUSTER_WINDOW_MINUTES window over sorted events.
        Emit a cluster when >= MIN_CLUSTER_SIZE distinct wallets found.
        """
        window = timedelta(minutes=CLUSTER_WINDOW_MINUTES)
        i = 0
        emitted_wallets: set[str] = set()

        while i < len(events):
            anchor = events[i]
            window_events = [anchor]

            for j in range(i + 1, len(events)):
                if events[j].timestamp - anchor.timestamp <= window:
                    window_events.append(events[j])
                else:
                    break

            wallets = list({e.wallet_address for e in window_events})

            if len(wallets) >= MIN_CLUSTER_SIZE:
                # Avoid re-emitting wallets already in a cluster
                new_wallets = [w for w in wallets if w not in emitted_wallets]
                if len(new_wallets) >= MIN_CLUSTER_SIZE:
                    cluster = self._build_cluster(window_events, new_wallets)
                    emitted_wallets.update(new_wallets)
                    yield cluster

            i += 1

    def _build_cluster(
        self, events: list[ScoredEvent], wallets: list[str]
    ) -> WalletCluster:
        return WalletCluster(
            wallets          = wallets,
            chain            = events[0].chain,
            pool_address     = events[0].pool_address,
            protocol         = events[0].protocol,
            event_type       = events[0].event_type,
            total_volume_usd = sum(e.amount_usd for e in events),
            z_score          = max(e.z_score for e in events),
            event_count      = len(events),
            first_seen       = min(e.timestamp for e in events),
            last_seen        = max(e.timestamp for e in events),
        )

    def _solo_cluster(self, event: ScoredEvent) -> WalletCluster:
        return WalletCluster(
            wallets          = [event.wallet_address],
            chain            = event.chain,
            pool_address     = event.pool_address,
            protocol         = event.protocol,
            event_type       = event.event_type,
            total_volume_usd = event.amount_usd,
            z_score          = event.z_score,
            event_count      = 1,
            first_seen       = event.timestamp,
            last_seen        = event.timestamp,
        )
