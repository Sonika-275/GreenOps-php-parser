"""
cost.py
AWS cost estimation for each rule/context.

Target stack: Laravel on EC2 + RDS (ap-south-1)
EC2 is always-on — no per-ms billing. Costs modelled only where
a real AWS bill line exists.

Constants (ap-south-1, 2024):
  RDS I/O: $0.20 per 1 million I/O requests
  Data Transfer (same-AZ, same-VPC, RDS→EC2): $0.00 (free)

Rules for what gets a cost vs a flag:
  R1 (N+1)     → RDS I/O only (extra queries hit the DB)
  R2 (count()) → $0 direct AWS cost; flagged as throughput degrader
  R3 (SELECT*) → RDS I/O only (extra pages read); transfer is free same-AZ
"""

# ── AWS Constants ────────────────────────────────────────────
RDS_IO_COST_PER_MILLION  = 0.20           # USD per 1M I/O requests, ap-south-1
DATA_TRANSFER_SAME_AZ    = 0.00           # RDS→EC2 same-AZ, same-VPC: free

# ── Workload Assumptions ──────────────────────────────────────
DEFAULT_RUNS_PER_DAY     = 10_000
DAYS_PER_MONTH           = 30

# ── Per-call extra latency assumptions ───────────────────────
DB_ROUND_TRIP_MS         = 50    # ms per extra RDS query, same-AZ
ASSUMED_N_ROWS           = 100   # rows in a typical foreach result
ASSUMED_M_ROWS           = 50    # inner loop row count (nested)
EXTRA_COLUMNS            = 20    # columns fetched unnecessarily (SELECT *)
AVG_BYTES_PER_COLUMN     = 50    # bytes per extra column
RDS_PAGE_SIZE_BYTES      = 8192  # 8KB per RDS I/O
PHP_OPS_PER_SEC          = 100_000_000  # PHP simple operations/second

# Collection size assumed for count() recalculation
ASSUMED_COLLECTION_SIZE  = 5_000


def _rds_io_cost(extra_queries: int, runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    """USD cost from extra RDS I/O requests per month."""
    monthly_extra_queries = extra_queries * runs_per_day * DAYS_PER_MONTH
    return (monthly_extra_queries / 1_000_000) * RDS_IO_COST_PER_MILLION


# ── Rule 1 — N+1 Query ───────────────────────────────────────
# Layers: RDS I/O only
# EC2 execution time is absorbed into always-on instance cost — not billed per ms.
# C1/C2: N+1 queries (N=100 rows) → 99 extra queries
# C3:    N×M queries (N=100, M=50) → 4998 extra queries
# C4:    Same as C1/C2 (hidden in function, same DB cost)

def rule1_cost(context: int, runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    if context == 3:
        extra_queries = (ASSUMED_N_ROWS * ASSUMED_M_ROWS) - 2
    else:
        extra_queries = ASSUMED_N_ROWS - 1  # C1, C2, C4

    rds = _rds_io_cost(extra_queries, runs_per_day)
    return round(rds, 4)


# ── Rule 2 — count() Recalculation ───────────────────────────
# Layers: none (pure PHP CPU on EC2 — no AWS bill line)
# EC2 CPU is pre-paid 24/7. Redundant count() burns CPU that
# could serve other requests → throughput degradation, not a bill line.
# Cost returned as $0; callers should surface this as a performance flag.

def rule2_cost(context: int, runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    # No direct AWS billing impact on EC2.
    # Flag this as a throughput degrader in the rule metadata instead.
    return 0.0


# ── Rule 3 — SELECT * ─────────────────────────────────────────
# Layers: RDS I/O only
# Data transfer RDS→EC2 is free within the same AZ/VPC.
# EC2 deserialization of extra Eloquent columns is absorbed into instance cost.

def _rule3_base_cost(row_count: int, runs_per_day: int) -> float:
    extra_bytes_per_call = row_count * EXTRA_COLUMNS * AVG_BYTES_PER_COLUMN

    # RDS I/O — extra pages read due to wider rows
    extra_ios = max(1, extra_bytes_per_call // RDS_PAGE_SIZE_BYTES)
    rds = (extra_ios * runs_per_day * DAYS_PER_MONTH / 1_000_000) * RDS_IO_COST_PER_MILLION

    return rds


def rule3_cost(context: int, runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    row_counts = {
        1: 1000,   # C1 all() — full table
        2: 100,    # C2 get() — filtered result
        3: 1,      # C3 first() — single row
        4: 200,    # C4 with() — parent + related combined
        5: ASSUMED_N_ROWS * ASSUMED_N_ROWS,  # C5 — in loop, N iterations × N rows
    }
    rows = row_counts.get(context, 100)
    return round(_rule3_base_cost(rows, runs_per_day), 4)


# ── Public API ────────────────────────────────────────────────

def estimate_cost(rule_id: str, context: int,
                  runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> dict:
    """
    Returns monthly cost estimate in USD for a given rule/context.

    R2 returns $0 — callers should check 'is_throughput_degrader'
    and surface a performance warning instead of a cost tag.
    """
    if rule_id == "R1":
        usd = rule1_cost(context, runs_per_day)
    elif rule_id == "R2":
        usd = rule2_cost(context, runs_per_day)
    elif rule_id == "R3":
        usd = rule3_cost(context, runs_per_day)
    else:
        usd = 0.0

    return {
        "cost_usd_monthly": usd,
        "cost_inr_monthly": round(usd * 84, 2),   # USD→INR approx rate
        "runs_per_day": runs_per_day,
        "is_throughput_degrader": rule_id == "R2",
        "note": "Modelled on AWS ap-south-1 pricing (EC2+RDS, same-AZ). Actual cost varies.",
    }