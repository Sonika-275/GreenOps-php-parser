"""
cost.py
AWS cost estimation for each rule/context.

Each rule hits different AWS layers — costs are summed per layer.

Constants (ap-south-1, 2024):
  Lambda: $0.0000166667 per GB-second (128MB = 0.125 GB)
  RDS I/O: $0.20 per 1 million I/O requests
  Data Transfer (cross-AZ): $0.01 per GB
  Data Transfer (same-AZ): $0.00 (free)

Assumptions documented per rule.
"""

# ── AWS Constants ────────────────────────────────────────────
LAMBDA_COST_PER_GB_SEC   = 0.0000166667   # USD, ap-south-1
LAMBDA_MEMORY_GB         = 0.125          # 128MB default function
RDS_IO_COST_PER_MILLION  = 0.20           # USD per 1M I/O requests
DATA_TRANSFER_PER_GB     = 0.01           # USD, cross-AZ RDS→Lambda
DATA_TRANSFER_SAME_AZ    = 0.00           # Free

# ── Workload Assumptions ──────────────────────────────────────
DEFAULT_RUNS_PER_DAY     = 10_000
DAYS_PER_MONTH           = 30

# ── Per-call extra latency assumptions ───────────────────────
DB_ROUND_TRIP_MS         = 50    # ms per extra RDS query, same-AZ
ASSUMED_N_ROWS           = 100   # rows in a typical foreach result
ASSUMED_M_ROWS           = 50    # inner loop row count (nested)
EXTRA_COLUMNS            = 20    # columns fetched unnecessarily (SELECT *)
AVG_BYTES_PER_COLUMN     = 50    # bytes per extra column
RDS_READ_SPEED_BYTES_SEC = 100 * 1024 * 1024   # 100 MB/s
RDS_PAGE_SIZE_BYTES      = 8192  # 8KB per RDS I/O
NETWORK_BANDWIDTH_BYTES  = 10 * 1024 * 1024 * 1024  # 10Gbps same-AZ
PHP_OPS_PER_SEC          = 100_000_000  # PHP simple operations/second

# Collection size assumed for count() recalculation
ASSUMED_COLLECTION_SIZE  = 5_000


def _lambda_cost(extra_ms: float, runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    """USD cost from extra Lambda execution time per month."""
    extra_sec = extra_ms / 1000
    gb_sec_per_call = LAMBDA_MEMORY_GB * extra_sec
    monthly_calls = runs_per_day * DAYS_PER_MONTH
    return gb_sec_per_call * monthly_calls * LAMBDA_COST_PER_GB_SEC


def _rds_io_cost(extra_queries: int, runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    """USD cost from extra RDS I/O requests per month."""
    monthly_extra_queries = extra_queries * runs_per_day * DAYS_PER_MONTH
    return (monthly_extra_queries / 1_000_000) * RDS_IO_COST_PER_MILLION


def _data_transfer_cost(extra_bytes: float, runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    """USD cost from extra data transfer RDS→Lambda (cross-AZ) per month."""
    monthly_bytes = extra_bytes * runs_per_day * DAYS_PER_MONTH
    monthly_gb = monthly_bytes / (1024 ** 3)
    return monthly_gb * DATA_TRANSFER_PER_GB


# ── Rule 1 — N+1 Query ───────────────────────────────────────
# Layers: RDS I/O + Lambda execution time
# C1/C2: N+1 queries (N=100 rows) → 99 extra queries
# C3:    N×M queries (N=100, M=50) → 4998 extra queries
# C4:    Same as C1/C2 (hidden in function, same DB cost)

def rule1_cost(context: int, runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    if context == 3:
        extra_queries = (ASSUMED_N_ROWS * ASSUMED_M_ROWS) - 2
    else:
        extra_queries = ASSUMED_N_ROWS - 1  # C1, C2, C4

    extra_ms = extra_queries * DB_ROUND_TRIP_MS

    rds   = _rds_io_cost(extra_queries, runs_per_day)
    lam   = _lambda_cost(extra_ms, runs_per_day)
    return round(rds + lam, 4)


# ── Rule 2 — count() Recalculation ───────────────────────────
# Layers: Lambda compute (CPU) only
# No DB hit. Pure CPU waste.
# count() on N-item collection called N times = N² ops
# Optimized = N ops
# Extra ops = N² - N ≈ N² for large N

def rule2_cost(context: int, runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    N = ASSUMED_COLLECTION_SIZE
    extra_ops = (N * N) - N   # N² - N
    extra_sec = extra_ops / PHP_OPS_PER_SEC
    extra_ms  = extra_sec * 1000

    # C1 (for) and C2 (while) have identical CPU cost pattern
    lam = _lambda_cost(extra_ms, runs_per_day)
    return round(lam, 4)


# ── Rule 3 — SELECT * ─────────────────────────────────────────
# Layers: RDS I/O + Data Transfer + Lambda (deserialize)
# Extra bytes per row = EXTRA_COLUMNS × AVG_BYTES_PER_COLUMN = 1000 bytes

def _rule3_base_cost(row_count: int, runs_per_day: int) -> float:
    extra_bytes_per_call = row_count * EXTRA_COLUMNS * AVG_BYTES_PER_COLUMN

    # RDS I/O — extra pages read
    extra_ios = max(1, extra_bytes_per_call // RDS_PAGE_SIZE_BYTES)
    rds = (extra_ios * runs_per_day * DAYS_PER_MONTH / 1_000_000) * RDS_IO_COST_PER_MILLION

    # Data transfer (cross-AZ assumption — conservative)
    transfer = _data_transfer_cost(extra_bytes_per_call, runs_per_day)

    # Lambda deserialization — ~10ms per 100KB extra
    deser_ms = (extra_bytes_per_call / (100 * 1024)) * 10
    lam = _lambda_cost(deser_ms, runs_per_day)

    return rds + transfer + lam


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
        "note": "Modelled estimate based on AWS ap-south-1 pricing. Actual cost varies.",
    }