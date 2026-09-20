"""Canonicalize a Choice decision request into a stable SHA-256 fingerprint.

The fingerprint covers everything that determines whether two requests are
equivalent for caching purposes: schema version, question, options (order-
independent), context, profile id, provider model, policy thresholds, and
the resolved abstain-option-id set. Changing any of these changes the
fingerprint, so a cached result is never reused across incompatible
evidence, model, policy, or profile-abstention configuration.
"""

from __future__ import annotations

import hashlib
import json

from jev_decisions.config import JevConfig
from jev_decisions.schemas import ChoiceRequest


def canonical_payload(
    request: ChoiceRequest,
    config: JevConfig,
    *,
    abstain_option_ids: frozenset[str] = frozenset(),
) -> dict[str, object]:
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
        "abstain_option_ids": sorted(abstain_option_ids),
    }


def compute_fingerprint(
    request: ChoiceRequest,
    config: JevConfig,
    *,
    abstain_option_ids: frozenset[str] = frozenset(),
) -> str:
    payload = canonical_payload(request, config, abstain_option_ids=abstain_option_ids)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
