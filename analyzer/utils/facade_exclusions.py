"""
facade_exclusions.py
Non-DB Laravel facades that should never be flagged by GreenOps rules.
Any call chain rooted at these identifiers is not a database operation.
"""

from typing import Optional

# Laravel facades and classes that have ->get() / ->first() / ->where()
# but are NOT Eloquent/QueryBuilder — must be excluded from all rules.
NON_DB_FACADES = {
    # ── Cache ────────────────────────────────────────────────
    "Cache",
    "Lock",                    # Cache::lock()->get()

    # ── Session ──────────────────────────────────────────────
    "Session",

    # ── Redis ────────────────────────────────────────────────
    "Redis",

    # ── Cookie ───────────────────────────────────────────────
    "Cookie",

    # ── HTTP Client ──────────────────────────────────────────
    "Http",
    "Response",
    "Request",

    # ── Filesystem ───────────────────────────────────────────
    "File",
    "Storage",

    # ── String / Array helpers ───────────────────────────────
    "Arr",
    "Str",
    "Collection",              # collect()->first() on in-memory collection

    # ── Date / Time ──────────────────────────────────────────
    "Carbon",
    "Date",

    # ── Self / static reference ──────────────────────────────
    "self",
    "static",
    "parent",

    # ── Queue / Jobs ─────────────────────────────────────────
    "Queue",
    "Bus",
    "Batch",

    # ── Events ───────────────────────────────────────────────
    "Event",

    # ── Mail / Notification ──────────────────────────────────
    "Mail",
    "Notification",

    # ── Logging ──────────────────────────────────────────────
    "Log",

    # ── Config ───────────────────────────────────────────────
    "Config",

    # ── Routing ──────────────────────────────────────────────
    "Route",
    "URL",
    "Redirect",

    # ── Views ────────────────────────────────────────────────
    "View",
    "Blade",

    # ── Validation ───────────────────────────────────────────
    "Validator",
    "Rule",

    # ── Auth ─────────────────────────────────────────────────
    "Auth",
    "Gate",
    "Password",

    # ── Broadcasting ─────────────────────────────────────────
    "Broadcast",

    # ── Hashing / Encryption ─────────────────────────────────
    "Hash",
    "Crypt",

    # ── Schema / Migration ───────────────────────────────────
    "Schema",
    "Blueprint",

    # ── Testing / App helpers ────────────────────────────────
    "App",
    "Artisan",

    # ── Third party common packages ──────────────────────────
    "Excel",               # Maatwebsite Excel
    "PDF",                 # barryvdh/laravel-dompdf
    "Datatables",          # yajra/laravel-datatables
    "DataTables",
    "Fractal",             # spatie/fractalistic
    "Socialite",           # laravel/socialite
    "Stripe",
    "Twilio",
    "Guzzle",
    "GuzzleHttp",
    "ZipArchive",
    "SplFileInfo",

    # ── PHP native classes ────────────────────────────────────
    "Exception",
    "Closure",
    "stdClass",
}


def get_chain_root_name(node) -> Optional[str]:
    """
    Walk down the leftmost child of a call chain to find the root identifier.
    e.g. Cache::lock()->get()  → 'Cache'
         Http::withHeaders()->get() → 'Http'
         $user->posts()->get() → None (variable, not a facade)
    """
    current = node
    while current is not None:
        if current.type == "scoped_call_expression":
            # Class::method() — get the class name (first child)
            if current.children:
                class_node = current.children[0]
                if class_node.type == "name":
                    return class_node.text.decode("utf-8")
            break
        if current.type == "member_call_expression":
            current = current.children[0] if current.children else None
        else:
            break
    return None


def is_non_db_facade(node, source: bytes) -> bool:
    """
    Returns True if the call chain is rooted at a non-DB Laravel facade.
    Use this at the top of each rule's detect loop to skip irrelevant nodes.
    """
    root_name = get_chain_root_name(node)
    if root_name and root_name in NON_DB_FACADES:
        return True
    return False