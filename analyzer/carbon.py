"""
carbon.py
Carbon emission estimation for each rule/context.

Target stack: Laravel on EC2 + RDS (ap-south-1)

Formula (Green Software Foundation SCI spec):
  carbon_kg = extra_energy_kwh * INDIA_GRID_KG_PER_KWH

Energy constants:
  EC2_KWH_PER_SEC:
    t3/m5 class EC2 draws ~17W TDP under moderate load (AWS published).
    Per second: 17W × PUE(1.135) / 3_600_000 = 5.272e-6 kWh/s
    (Previous constant 2.778e-6 omitted PUE — corrected here.)

  RDS_KWH_PER_IO:
    ~0.000001 kWh per I/O (NVMe SSD + DB compute, AWS RDS benchmarks).

  INDIA_GRID_KG_PER_KWH:
    India CEA CO2 Baseline Document 2023 = 0.708 kg CO2/kWh
    Source: cea.nic.in — used for ESG/BRSR Scope 3 reporting.
    NOTE: 0.82 is the older 2019 figure — do not use for BRSR compliance.

  AWS_PUE:
    AWS Mumbai (ap-south-1) PUE = 1.135
    Source: AWS 2023 Sustainability Report.

Output includes 1x / 10x / 100x traffic projections for BRSR use.
R2 (count()) retains carbon even though cost = $0 — wasted CPU burns electricity.
"""

# ── Constants ─────────────────────────────────────────────────
AWS_PUE                  = 1.135    # AWS Mumbai data center PUE
EC2_WATTS                = 17.0     # t3/m5 class TDP (AWS published)
EC2_KWH_PER_SEC          = (EC2_WATTS * AWS_PUE) / 3_600_000   # ~5.272e-6
RDS_KWH_PER_IO           = 1.0e-6   # kWh per RDS I/O operation
INDIA_GRID_KG_PER_KWH    = 0.708    # CEA 2023 — correct figure for BRSR

DEFAULT_RUNS_PER_DAY     = 10_000
DAYS_PER_MONTH           = 30

# ── Workload constants (mirrors cost.py) ──────────────────────
DB_ROUND_TRIP_SEC        = 0.05     # seconds per extra RDS query
ASSUMED_N_ROWS           = 100
ASSUMED_M_ROWS           = 50
ASSUMED_COLLECTION_SIZE  = 5_000
PHP_OPS_PER_SEC          = 100_000_000
RDS_PAGE_SIZE_BYTES      = 8192
EXTRA_COLUMNS            = 20
AVG_BYTES_PER_COLUMN     = 50
RDS_READ_BYTES_PER_SEC   = 100 * 1024 * 1024   # 100 MB/s

# ── Rule 3: extra RDS read time per context (seconds) ─────────
# row_count × (bytes_per_row / RDS_read_speed)
# Lambda deserialisation overhead removed — EC2 absorbs into instance CPU.
def _rule3_extra_seconds(context: int) -> float:
    row_counts = {1: 1000, 2: 100, 3: 1, 4: 200,
                  5: ASSUMED_N_ROWS * ASSUMED_N_ROWS}
    rows        = row_counts.get(context, 100)
    extra_bytes = rows * EXTRA_COLUMNS * AVG_BYTES_PER_COLUMN
    return extra_bytes / RDS_READ_BYTES_PER_SEC


# ── Core energy → carbon helpers ─────────────────────────────

def _carbon_from_time(extra_seconds: float,
                      runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    """kg CO2/month from extra EC2 CPU/wait time."""
    energy_kwh     = extra_seconds * EC2_KWH_PER_SEC
    monthly_energy = energy_kwh * runs_per_day * DAYS_PER_MONTH
    return monthly_energy * INDIA_GRID_KG_PER_KWH


def _carbon_from_ios(extra_ios: int,
                     runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    """kg CO2/month from extra RDS I/O operations."""
    energy_kwh     = extra_ios * RDS_KWH_PER_IO
    monthly_energy = energy_kwh * runs_per_day * DAYS_PER_MONTH
    return monthly_energy * INDIA_GRID_KG_PER_KWH


# ── Scaling projection helper ─────────────────────────────────

def _scale_projections(base_kg_monthly: float) -> dict:
    """
    Projects carbon at 1x, 10x, 100x traffic scale.
    Also computes annualised figures for BRSR Scope 3 reporting.
    """
    return {
        "kg_monthly_1x":    round(base_kg_monthly, 6),
        "kg_monthly_10x":   round(base_kg_monthly * 10, 6),
        "kg_monthly_100x":  round(base_kg_monthly * 100, 6),
        "kg_annual_1x":     round(base_kg_monthly * 12, 4),
        "kg_annual_10x":    round(base_kg_monthly * 120, 4),
        "kg_annual_100x":   round(base_kg_monthly * 1200, 4),
    }


# ── Rule 1 — N+1 ─────────────────────────────────────────────
# Energy: EC2 wait time (blocked on DB) + RDS I/O energy

def rule1_carbon(context: int,
                 runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    if context == 3:
        extra_queries = (ASSUMED_N_ROWS * ASSUMED_M_ROWS) - 2
    else:
        extra_queries = ASSUMED_N_ROWS - 1

    extra_seconds = extra_queries * DB_ROUND_TRIP_SEC
    ec2_carbon    = _carbon_from_time(extra_seconds, runs_per_day)
    rds_carbon    = _carbon_from_ios(extra_queries, runs_per_day)
    return ec2_carbon + rds_carbon


# ── Rule 2 — count() recalculation ───────────────────────────
# Energy: EC2 CPU only — no DB involved.
# Carbon is real even though cost.py returns $0 (CPU is pre-paid on EC2,
# but it still burns electricity and emits CO2).

def rule2_carbon(context: int,
                 runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    N           = ASSUMED_COLLECTION_SIZE
    extra_ops   = (N * N) - N
    extra_secs  = extra_ops / PHP_OPS_PER_SEC
    return _carbon_from_time(extra_secs, runs_per_day)


# ── Rule 3 — SELECT * ─────────────────────────────────────────
# Energy: RDS I/O (extra pages) + EC2 wait (extra read time)
# Data transfer energy same-AZ is negligible — omitted.

def rule3_carbon(context: int,
                 runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> float:
    extra_seconds = _rule3_extra_seconds(context)

    row_counts  = {1: 1000, 2: 100, 3: 1, 4: 200,
                   5: ASSUMED_N_ROWS * ASSUMED_N_ROWS}
    rows        = row_counts.get(context, 100)
    extra_bytes = rows * EXTRA_COLUMNS * AVG_BYTES_PER_COLUMN
    extra_ios   = max(1, extra_bytes // RDS_PAGE_SIZE_BYTES)

    ec2_carbon  = _carbon_from_time(extra_seconds, runs_per_day)
    rds_carbon  = _carbon_from_ios(extra_ios, runs_per_day)
    return ec2_carbon + rds_carbon


# ── Public API ────────────────────────────────────────────────

def estimate_carbon(rule_id: str, context: int,
                    runs_per_day: int = DEFAULT_RUNS_PER_DAY) -> dict:
    """
    Returns monthly + projected carbon estimate in kg CO2.

    Includes 1x/10x/100x traffic projections for BRSR Scope 3 reporting.
    R2 returns non-zero carbon — wasted CPU still consumes electricity.

    Grid intensity: India CEA 2023 = 0.708 kg CO2/kWh (not 0.82 — that
    is the outdated 2019 figure and will fail BRSR auditor verification).
    """
    if rule_id == "R1":
        kg = rule1_carbon(context, runs_per_day)
    elif rule_id == "R2":
        kg = rule2_carbon(context, runs_per_day)
    elif rule_id == "R3":
        kg = rule3_carbon(context, runs_per_day)
    else:
        kg = 0.0

    projections = _scale_projections(kg)

    return {
        "carbon_kg_monthly":   projections["kg_monthly_1x"],
        "projections":         projections,
        "grid_intensity":      INDIA_GRID_KG_PER_KWH,
        "grid_source":         "India CEA CO2 Baseline Document 2023 (cea.nic.in)",
        "methodology":         "Green Software Foundation SCI spec + AWS sustainability report (PUE 1.135)",
        "brsr_scope":          "Scope 3 Category 11 — Use of sold products",
        "runs_per_day":        runs_per_day,
        "note": (
            "Modelled estimate for EC2+RDS stack. "
            "Projections assume linear traffic scaling. "
            "Actual emissions depend on workload profile and instance utilisation."
        ),
    }