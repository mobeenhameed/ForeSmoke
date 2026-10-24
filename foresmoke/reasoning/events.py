"""Deterministic lifecycle IDs and human-readable, evidence-based summaries."""
from uuid import NAMESPACE_URL, UUID, uuid5
from foresmoke.shared.contracts import HazardEvent

def incident_id(run_id: str, camera_id: str, zone_id: str, ordinal: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"foresmoke|{run_id}|{camera_id}|{zone_id}|{ordinal}"))

def event_id(incident: str, event_type: str) -> str:
    return str(uuid5(UUID(incident), event_type))

def explain(event: HazardEvent) -> str:
    if event.event_type == "incident_confirmed":
        labels = ", ".join(sorted({c.label for c in event.evidence.objects}))
        overlap = max(c.zone_overlap for c in event.evidence.objects)
        return (f"{event.zone_id}: access occupied by {labels}; footprint overlap "
                f"{overlap:.2f}; {event.confirmation_evidence_ms / 1000:.1f} s positive evidence")
    return (f"{event.zone_id}: no configured obstruction observed for "
            f"{event.clear_evidence_ms / 1000:.1f} s; incident resolved")
