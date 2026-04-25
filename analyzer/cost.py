"""
cost.py
AWS cost estimation for each rule/context.

Target stack: Laravel on EC2 + RDS (ap-south-1)
EC2 is always-on — no per-ms billing. Costs are modelled only where
a real AWS bill line exists OR where throughput degradation forces
an instance tier upgrade (scaling cost delta).

Cost model:
  R1 (N+1)     → RDS I/O (direct bill) + EC2/RDS tier pressure (scaling delta)
  R2 (count()) → $0 direct bill; flagged as throughput degrader
  R3 (SELECT*) → RDS I/O (direct bill) + EC2/RDS tier pressure (scaling delta)

Constants (ap-south-1, 2024):
  RDS I/O:          $0.20 per 1M I/O requests
  Data transfer:    $0.00 same-AZ same-VPC (RDS→EC2 free)
  EC2 tier delta:   t3.medium $30/mo → t3.large $60/mo → t3.xlarge $120/mo
  RDS tier delta:   db.t3.medium $60/mo → db.t3.large $120/mo
"""

# ── AWS Constants ─────────────────────────────────────────────
RDS_IO_COST_PER_MILLION  = 0.20     # USD per 1M I/O requests, ap-south-1
DATA_TRANSFER_SAME_AZ    = 0.00     # RDS→EC2 same-AZ same-VPC: free

# ── Currency ──────────────────────────────────────────────────
USD_TO_INR               = 84       # approximate conversion rate

# ── EC2 Instance Tiers (ap-south-1, on-demand, Linux) ─────────
EC2_TIERS = [
    {"name": "t3.medium",  "usd_month": 30,  "max_rps": 8},
    {"name": "t3.large",   "usd_month": 60,  "max_rps": 16},
    {"name": "t3.xlarge",  "usd_month": 120, "max_rps": 32},
    {"name": "t3.2xlarge", "usd_month": 240, "max_rps": 64},
]

# ── RDS Instance Tiers (ap-south-1, MySQL single-AZ) ──────────
RDS_TIERS = [
    {"name": "db.t3.micro",  "usd_month": 15,  "max_qps": 100},
    {"name": "db.t3.medium", "usd_month": 60,  "max_qps": 500},
    {"name": "db.t3.large",  "usd_month": 120, "max_qps": 1500},
    {"name": "db.r5.large",  "usd_month": 220, "max_qps": 5000},
]

# ── Workload Assumptions ──────────────────────────────────────
DEFAULT_RUNS_PER_DAY     = 10_000
DAYS_PER_MONTH           = 30
BASELINE_LATENCY_MS      = 200      # typical Laravel endpoint response time

# ── Per-call extra latency / DB assumptions ───────────────────
DB_ROUND_TRIP_MS         = 50       # ms per extra RDS query, same-AZ
ASSUMED_N_ROWS           = 100      # rows in a typical foreach result
ASSUMED_M_ROWS           = 50       # inner loop row count (nested N+1)
EXTRA_COLUMNS            = 20       # columns fetched unnecessarily (SELECT *)
AVG_BYTES_PER_COLUMN     = 50       # bytes per extra column
RDS_PAGE_SIZE_BYTES      = 8192     # 8KB per RDS I/O page
ASSUMED_COLLECTION_SIZE  = 5_000    # collection size for count() rule


# ── Helper: RDS I/O direct cost ───────────────────────────────

def _rds_io_cost(extra_queries: int,
                 runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    """USD/month from extra RDS I/O requests."""
    monthly = extra_queries * runs_per_day * DAYS_PER_MONTH
    return (monthly / 1_000_000) * RDS_IO_COST_PER_MILLION


# ── Helper: Throughput degradation ratio ─────────────────────

def _throughput_degradation(extra_latency_ms: float) -> float:
    """
    How many times more capacity is needed due to extra latency.
    degradation_ratio = efficient_rps / degraded_rps
    Example: +4950ms extra on 200ms baseline → ratio ~25.75
    """
    efficient_rps = 1000 / BASELINE_LATENCY_MS
    degraded_rps  = 1000 / (BASELINE_LATENCY_MS + extra_latency_ms)
    return efficient_rps / degraded_rps


# ── Helper: EC2 tier for a given RPS need ────────────────────

def _ec2_tier_for_rps(rps: float) -> dict:
    for tier in EC2_TIERS:
        if rps <= tier["max_rps"]:
            return tier
    return EC2_TIERS[-1]   # return largest if beyond all tiers


def _rds_tier_for_qps(qps: float) -> dict:
    for tier in RDS_TIERS:
        if qps <= tier["max_qps"]:
            return tier
    return RDS_TIERS[-1]


# ── Helper: Scaling cost delta ────────────────────────────────

def _scaling_cost_delta(extra_latency_ms: float,
                        extra_queries_per_req: int,
                        runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> dict:
    """
    Returns the monthly USD delta caused by needing a higher
    EC2 and/or RDS tier due to the inefficiency.

    peak_rps assumed = 5x average (common rule of thumb).
    """
    avg_rps  = runs_per_day / 86_400
    peak_rps = avg_rps * 5

    degradation      = _throughput_degradation(extra_latency_ms)
    degraded_rps     = peak_rps * degradation

    efficient_ec2    = _ec2_tier_for_rps(peak_rps)
    degraded_ec2     = _ec2_tier_for_rps(degraded_rps)
    ec2_delta        = degraded_ec2["usd_month"] - efficient_ec2["usd_month"]

    avg_qps          = runs_per_day / 86_400
    degraded_qps     = avg_qps * extra_queries_per_req
    efficient_rds    = _rds_tier_for_qps(avg_qps)
    degraded_rds     = _rds_tier_for_qps(avg_qps + degraded_qps)
    rds_delta        = degraded_rds["usd_month"] - efficient_rds["usd_month"]

    return {
        "ec2_delta_usd":          ec2_delta,
        "rds_delta_usd":          rds_delta,
        "total_scaling_delta_usd": ec2_delta + rds_delta,
        "efficient_ec2_tier":     efficient_ec2["name"],
        "degraded_ec2_tier":      degraded_ec2["name"],
        "efficient_rds_tier":     efficient_rds["name"],
        "degraded_rds_tier":      degraded_rds["name"],
        "throughput_degradation": round(degradation, 2),
    }


# ── Rule 1 — N+1 Query ───────────────────────────────────────
# Bill layers: RDS I/O (direct) + EC2/RDS tier pressure (scaling delta)
# C1/C2/C4: N-1 extra queries (N=100 rows)
# C3:       N×M extra queries (N=100, M=50) — nested loop

def rule1_cost(context: int,
               runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> dict:
    if context == 3:
        extra_queries = (ASSUMED_N_ROWS * ASSUMED_M_ROWS) - 2
    else:
        extra_queries = ASSUMED_N_ROWS - 1

    extra_latency_ms = extra_queries * DB_ROUND_TRIP_MS

    rds_io   = _rds_io_cost(extra_queries, runs_per_day)
    scaling  = _scaling_cost_delta(extra_latency_ms, extra_queries, runs_per_day)
    total    = rds_io + scaling["total_scaling_delta_usd"]

    return {
        "rds_io_usd":    round(rds_io, 4),
        "scaling":       scaling,
        "total_usd":     round(total, 4),
    }


# ── Rule 2 — count() Recalculation ───────────────────────────
# No direct AWS bill line on EC2 (CPU is always-on pre-paid).
# Returns $0 cost; flagged as throughput degrader for callers.

def rule2_cost(context: int,
               runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> dict:
    return {
        "rds_io_usd":  0.0,
        "scaling":     None,
        "total_usd":   0.0,
    }


# ── Rule 3 — SELECT * ─────────────────────────────────────────
# Bill layers: RDS I/O (extra pages read) + EC2/RDS tier pressure
# Data transfer RDS→EC2 is free same-AZ/same-VPC — excluded.

def _rule3_extra_queries(row_count: int) -> int:
    """Extra RDS I/O page reads from wider rows."""
    extra_bytes = row_count * EXTRA_COLUMNS * AVG_BYTES_PER_COLUMN
    return max(1, extra_bytes // RDS_PAGE_SIZE_BYTES)


def rule3_cost(context: int,
               runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> dict:
    row_counts = {
        1: 1000,                              # C1 all() — full table
        2: 100,                               # C2 get() — filtered
        3: 1,                                 # C3 first() — single row
        4: 200,                               # C4 with() — parent+related
        5: ASSUMED_N_ROWS * ASSUMED_N_ROWS,   # C5 in loop — N×N rows
    }
    rows         = row_counts.get(context, 100)
    extra_ios    = _rule3_extra_queries(rows)
    extra_bytes  = rows * EXTRA_COLUMNS * AVG_BYTES_PER_COLUMN

    # Extra latency from reading wider pages (RDS read speed ~100 MB/s)
    RDS_READ_BYTES_PER_SEC = 100 * 1024 * 1024
    extra_latency_ms = (extra_bytes / RDS_READ_BYTES_PER_SEC) * 1000

    rds_io  = _rds_io_cost(extra_ios, runs_per_day)
    scaling = _scaling_cost_delta(extra_latency_ms, extra_ios, runs_per_day)
    total   = rds_io + scaling["total_scaling_delta_usd"]

    return {
        "rds_io_usd":  round(rds_io, 4),
        "scaling":     scaling,
        "total_usd":   round(total, 4),
    }


# ── Public API ────────────────────────────────────────────────

def estimate_cost(rule_id: str, context: int,
                  runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> dict:
    """
    Returns monthly cost estimate for a given rule/context.

    R2 returns $0 — check 'is_throughput_degrader' and surface
    a performance warning instead of a cost tag.

    scaling.ec2_delta_usd / rds_delta_usd show the instance tier
    upgrade cost caused by the inefficiency — the real dollar story.
    """
    if rule_id == "R1":
        breakdown = rule1_cost(context, runs_per_day)
    elif rule_id == "R2":
        breakdown = rule2_cost(context, runs_per_day)
    elif rule_id == "R3":
        breakdown = rule3_cost(context, runs_per_day)
    else:
        breakdown = {"rds_io_usd": 0.0, "scaling": None, "total_usd": 0.0}

    usd = breakdown["total_usd"]

    return {
        "cost_usd_monthly":       usd,
        "cost_inr_monthly":       round(usd * USD_TO_INR, 2),
        "breakdown":              breakdown,
        "runs_per_day":           runs_per_day,
        "is_throughput_degrader": rule_id == "R2",
        "note": (
            "EC2+RDS ap-south-1 model. Direct cost = RDS I/O billing. "
            "Scaling delta = instance tier upgrade cost forced by throughput degradation. "
            "Actual cost varies with workload profile."
        ),
    }