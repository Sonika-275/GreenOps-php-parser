"""
facade_exclusions.py
Non-DB Laravel facades that should never be flagged by GreenOps rules.
Any call chain rooted at these identifiers is not a database operation.

Handles:
  - Simple facades: Cache::get(), Session::get()
  - Backslash prefixed: \Session::get(), \Illuminate\Support\Facades\Http::get()
  - Fully qualified namespaces: strips namespace, checks last segment
"""

from typing import Optional

NON_DB_FACADES = {
    # ── Cache ────────────────────────────────────────────────
    "Cache",
    "Lock",

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
    "Collection",

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
    "Excel",
    "PDF",
    "Datatables",
    "DataTables",
    "Fractal",
    "Socialite",
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

    # ── Laravel Illuminate full namespace last segments ───────
    # \Illuminate\Support\Facades\Http → 'Http' already covered
    # but adding common Illuminate class names as safety net
    "Facades",          # \Illuminate\Support\Facades\...
    "Support",
    "Foundation",
    "Pipeline",
    "Container",
    "Manager",
}


def _extract_class_name(text: str) -> Optional[str]:
    """
    Extract the usable class name from various PHP class reference formats:
      - 'Cache'                              → 'Cache'
      - '\Session'                           → 'Session'
      - '\Illuminate\Support\Facades\Http'   → 'Http'
      - 'Illuminate\\Support\\Facades\\Http' → 'Http'
    """
    if not text:
        return None
    # strip leading backslash(es)
    text = text.lstrip("\\")
    # split on backslash — take last segment
    parts = text.split("\\")
    return parts[-1] if parts else None


def get_chain_root_name(node) -> Optional[str]:
    """
    Walk down the leftmost child of a call chain to find the root class name.
    Handles:
      - Simple: Cache::get()           → 'Cache'
      - Backslash: \Session::get()     → 'Session'
      - Qualified: \Illuminate\...\Http::get() → 'Http'
      - Member chain: $user->posts()->get() → None (variable)
    """
    current = node
    while current is not None:
        if current.type == "scoped_call_expression":
            if current.children:
                class_node = current.children[0]

                # simple name: Cache, Session, User
                if class_node.type == "name":
                    return class_node.text.decode("utf-8")

                # qualified name with backslash: \Session, \Illuminate\...\Http
                if class_node.type in {
                    "qualified_name",
                    "namespace_name",
                    "named_type",
                    "relative_scope",
                }:
                    raw = class_node.text.decode("utf-8")
                    return _extract_class_name(raw)

                # fallback — try to get text directly
                try:
                    raw = class_node.text.decode("utf-8")
                    extracted = _extract_class_name(raw)
                    if extracted:
                        return extracted
                except Exception:
                    pass

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