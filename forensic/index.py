"""Metadata index + nearest-neighbor similarity over tracklet embeddings."""
from __future__ import annotations

import numpy as np

from forensic.attributes import AttributeSet
from storage.database import Database


class BruteForceIndex:
    """Cosine top-k over pre-normalized vectors (dot product). Numpy only."""

    def find_similar(
        self, query: np.ndarray, candidates: list[tuple[int, np.ndarray]], k: int,
    ) -> list[tuple[int, float]]:
        if query.size == 0 or not candidates:
            return []
        scored: list[tuple[int, float]] = []
        for cid, vec in candidates:
            if vec.shape != query.shape:
                continue
            scored.append((cid, float(np.dot(query, vec))))
        scored.sort(key=lambda p: p[1], reverse=True)
        return scored[:k]


class MetadataIndex:
    """Metadata and embedding index for tracklet similarity search."""

    def __init__(self, db: Database, model_id: str = "osnet_x0_25") -> None:
        self._db = db
        self._model_id = model_id
        self._index = BruteForceIndex()

    def ensure_tracklet(self, source_id: int | None, track_id: int | None,
                        first_ts: float, now: float) -> int:
        """Ensure tracklet exists in database."""
        return self._db.add_tracklet(source_id, track_id, first_ts, now)

    def save_sample(self, tracklet_id: int, attributes: AttributeSet,
                    snapshot_path: str | None, now: float) -> None:
        """Save tracklet attributes and snapshot path."""
        self._db.update_tracklet_attributes(
            tracklet_id, height_band=attributes.height_band, build=attributes.build,
            upper_color=attributes.upper_color, lower_color=attributes.lower_color,
            clothing_type=attributes.clothing_type, accessories=attributes.accessories,
            attr_conf=attributes.attr_conf, snapshot_path=snapshot_path,
            last_ts=now, now=now,
        )

    def set_embedding(self, tracklet_id: int, vector: np.ndarray, now: float) -> None:
        """Store embedding vector for tracklet."""
        vec = np.asarray(vector, dtype=np.float32).reshape(-1)
        self._db.set_tracklet_embedding(
            tracklet_id, vec.tobytes(), int(vec.shape[0]), self._model_id, now)

    def find_similar(self, tracklet_id: int, k: int = 10,
                     filters: dict | None = None) -> list[tuple[int, float]]:
        """Find k most similar tracklets by embedding, optionally filtered by metadata."""
        emb = self._db.get_embedding(tracklet_id)
        if emb is None:
            return []
        query = np.frombuffer(emb[0], dtype=np.float32)
        cands = self._db.candidate_embeddings(tracklet_id, **(filters or {}))
        pairs = [(cid, np.frombuffer(b, dtype=np.float32)) for cid, b, _ in cands]
        return self._index.find_similar(query, pairs, k)

    def prune(self, cutoff: float) -> int:
        """Remove tracklets older than cutoff timestamp."""
        return self._db.prune_tracklets_older_than(cutoff)
