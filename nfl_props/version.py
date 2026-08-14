"""Version metadata written into every history export.

Bump the model version only when projections/probabilities change; bump the
tier policy only when Core/Lean/Watch gates change; a schema bump alone means
extra fields were recorded, not that recommendations changed.
"""

HISTORY_SCHEMA_VERSION = 1
MODEL_VERSION = "nfl-epa-points-v1"
TIER_POLICY_VERSION = "core-lean-watch-v1"
