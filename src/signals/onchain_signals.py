"""
onchain_signals.py — exchange inflow/outflow, whale accumulation/dump
"""
import logging

logger = logging.getLogger(__name__)


def evaluate_onchain(events, config):
    """
    events: list from onchain_fetcher (each with score, reasons)
    Returns: {score, details, events}
    """
    if not events:
        return {"score": 0.0, "details": ["no on-chain events"], "events": []}
    total = sum(e.get("score", 0) for e in events)
    # cap
    total = max(-5, min(5, total))
    details = []
    for e in events[:3]:
        details.append(f"{e.get('title','')[:60]} -> {e.get('score'):+} {e.get('reasons',[])}")
    return {"score": float(total), "details": details, "events": events[:5]}
