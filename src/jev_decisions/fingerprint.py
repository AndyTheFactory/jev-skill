"""Canonicalize a Choice decision request into a stable SHA-256 fingerprint.

The fingerprint covers everything that determines whether two requests are
equivalent for caching purposes: schema version, question, options (order-
independent), context, profile id, provider model, and policy thresholds.
Changing any of these changes the fingerprint, so a cached result is never
reused across incompatible evidence, model or policy configuration.
"""

from __future__ import annotations

import hashlib
import json

from jev_decisions.config import JevConfig
from jev_decisions.schemas import ChoiceRequest


def canonical_payload(request: ChoiceRequest, config: JevConfig) -> dict[str, object]:
    return {
        "schema_version": request.schema_version,
        "question": request.question,
        "options": sorted(
            ({"id": opt.id, "description": opt.description} for opt in request.options),
            key=lambda opt: opt["id"],
        ),
        "context": request.context,
        "profile": request.profile,
        "provider_model": config.provider.model,
        "policy": config.policy.model_dump(),
    }


def compute_fingerprint(request: ChoiceRequest, config: JevConfig) -> str:
    payload = canonical_payload(request, config)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
