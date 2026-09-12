"""Version metadata written into every history export.

Bump the model version only when projections/probabilities change; bump the
tier policy only when Core/Lean/Watch gates change; a schema bump alone means
extra fields were recorded, not that recommendations changed.

The forecast-first cohort (see docs/PRODUCT_CONTRACT.md) is a separate version
family. The legacy price-screened identifiers stay frozen so old snapshots
keep grading under the rules they shipped with. Forecast snapshots are never
pooled with the legacy cohort.
"""

# --- Legacy price-screened cohort (frozen; historical grading only) ---------
HISTORY_SCHEMA_VERSION = 3
MODEL_VERSION = "nfl-epa-points-v1.1"
TIER_POLICY_VERSION = "core-lean-watch-v2"

# --- Forecast-first cohort --------------------------------------------------
FORECAST_MODEL_VERSION = "nfl-forecast-v1"
WINNER_MODEL_VERSION = "nfl-winner-logit-v1"
MARGIN_MODEL_VERSION = "nfl-margin-ridge-v1"
TOTAL_MODEL_VERSION = "nfl-total-ridge-v1"
FORECAST_HISTORY_SCHEMA_VERSION = 1
PRODUCT_POLICY_VERSION = "nfl-forecast-first-v1"
VALUE_POLICY_VERSION = "nfl-value-bands-v1"
FORECAST_GRADING_VERSION = "nfl-forecast-grading-v1"
