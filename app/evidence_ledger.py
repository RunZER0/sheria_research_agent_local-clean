from __future__ import annotations

from typing import List, Dict
from .schemas import EvidenceCard


class EvidenceLedger:
    def __init__(self):
        self.accepted: List[EvidenceCard] = []
        self.rejected: List[Dict] = []
        self.inspected_source_ids: set[str] = set()

    def add_candidate(self, card: EvidenceCard):
        # candidates are accepted by default here; higher-level logic may move to rejected
        self.accepted.append(card)
        self.inspected_source_ids.add(card.source_id)

    def reject(self, source_id: str, reason: str):
        self.rejected.append({"source_id": source_id, "reason": reason})

    def supports_gap(self, gap_description: str) -> List[EvidenceCard]:
        hits: List[EvidenceCard] = []
        key = gap_description.lower()
        for c in self.accepted:
            excerpt = (c.excerpt or "").lower()
            title = (c.title or "").lower()
            if key in excerpt or key in title:
                hits.append(c)
        return hits

    def counts_by_authority(self) -> Dict[str, int]:
        primary = 0
        official = 0
        persuasive = 0
        context = 0
        for c in self.accepted:
            lvl = getattr(c, "authority_level", "unknown") or "unknown"
            if lvl == "primary":
                primary += 1
            elif lvl == "official":
                official += 1
            elif lvl in {"background", "persuasive"}:
                persuasive += 1
            else:
                context += 1
        return {"primary": primary, "official": official, "persuasive": persuasive, "context": context}
