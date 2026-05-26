"""
rule3_select_star.py
Detects SELECT * (missing select()) patterns in Eloquent queries and Query Builder.

Contexts detected:
  C1 — Model::all()                          HIGH
  C2 — Model::...->get() without select()    MEDIUM
  C3 — Model::...->first() without select()  LOW
  C4 — Model::with()->get() without select() HIGH  (both parent + related)
  C5 — get()/all() INSIDE a loop             VERY HIGH (Rule1 + Rule3 combined)
  C6 — DB::table()->get() without select()   MEDIUM (Query Builder SELECT*)
  C7 — DB::table()->first() without select() LOW    (Query Builder SELECT*)

False positive guards:
  - Non-DB facades excluded via facade_exclusions.py
  - lockForUpdate() / sharedLock() / lockForShare() → intentional locks, skip
  - selectRaw() / addSelect() / select(DB::raw()) → valid select, skip
  - DB::table() handled separately as query builder (C6/C7), not Eloquent
  - get()->first/last/filter/map() → in-memory collection ops, not DB terminal
"""

from typing import List, Dict, Any
from utils.facade_exclusions import is_non_db_facade

TERMINAL_METHODS = {"get", "all", "first", "paginate", "firstOrFail", "findOrFail"}

# Query builder terminal methods
QB_TERMINAL_METHODS = {"get", "first", "firstOrFail"}

# Transaction lock methods — intentional, never flag
LOCK_METHODS = {"lockForUpdate", "sharedLock", "lockForShare"}

# Valid select patterns — if any present in chain, query is already optimised
VALID_SELECT_METHODS = {"select", "selectRaw", "addSelect"}

# In-memory collection operations — get() here is not a DB terminal
COLLECTION_OPS = {"first", "last", "find", "filter", "map", "where",
                  "each", "reduce", "reject", "pluck", "keyBy", "groupBy"}

LOOP_TYPES = {"foreach_statement", "for_statement", "while_statement"}

DB_QUERY_BUILDER_ROOT = "DB"


def get_node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def get_line(node) -> int:
    return node.start_point[0] + 1


def collect_all_nodes(root) -> List:
    result = []
    queue = [root]
    while queue:
        node = queue.pop(0)
        result.append(node)
        queue.extend(node.children)
    return result


def get_ancestors(node) -> List:
    ancestors = []
    current = node.parent
    while current is not None:
        ancestors.append(current)
        current = current.parent
    return ancestors


def is_inside_loop(node) -> bool:
    for ancestor in get_ancestors(node):
        if ancestor.type in LOOP_TYPES:
            return True
    return False


def get_method_name(node) -> str:
    """Extract method name from member_call_expression or scoped_call_expression."""
    if node.type in {"member_call_expression", "scoped_call_expression"}:
        children = node.children
        for i, child in enumerate(children):
            if child.type == "name":
                if node.type == "scoped_call_expression" and i == 0:
                    continue
                return child.text.decode("utf-8")
    return ""


def get_chain_root_class(terminal_node) -> str:
    """
    Walk to the root of the call chain and return the class name.
    e.g. DB::table()->where()->get() → 'DB'
         User::where()->get()        → 'User'
    """
    current = terminal_node
    while current is not None:
        if current.type == "scoped_call_expression":
            if current.children:
                class_node = current.children[0]
                if class_node.type == "name":
                    return class_node.text.decode("utf-8")
            break
        if current.children:
            current = current.children[0]
        else:
            break
    return ""


def collect_chain_method_names(terminal_node) -> List[str]:
    """
    Walk the full method chain and collect all method names.
    e.g. Customer::select(...)->with(...)->get() → ['get', 'with', 'select']
    """
    names = []
    current = terminal_node

    while current is not None:
        name = get_method_name(current)
        if name:
            names.append(name)

        if current.children:
            obj = current.children[0]
            if obj.type in {"member_call_expression", "scoped_call_expression"}:
                current = obj
            else:
                break
        else:
            break

    return names


def chain_has_select(terminal_node) -> bool:
    """Check if any valid select method appears anywhere in the chain."""
    chain = collect_chain_method_names(terminal_node)
    return bool(VALID_SELECT_METHODS & set(chain))


def chain_has_lock(terminal_node) -> bool:
    """Check if chain contains a transaction lock method."""
    chain = collect_chain_method_names(terminal_node)
    return bool(LOCK_METHODS & set(chain))


def chain_has_with(terminal_node) -> bool:
    """Check if 'with' appears anywhere in the method chain."""
    return "with" in collect_chain_method_names(terminal_node)


def is_collection_operation(node) -> bool:
    """
    Detect ->get()->first() pattern — in-memory collection op, not DB terminal.
    """
    if node.type != "member_call_expression":
        return False
    method = get_method_name(node)
    if method not in COLLECTION_OPS:
        return False
    obj = node.children[0] if node.children else None
    if obj and obj.type == "member_call_expression":
        parent_method = get_method_name(obj)
        if parent_method == "get":
            return True
    return False


def is_terminal_eloquent_call(node) -> bool:
    """
    Check if this node is a terminal Eloquent call.
    Excludes DB::table() query builder chains.
    """
    method = get_method_name(node)
    if method not in TERMINAL_METHODS:
        return False

    # exclude DB::table() query builder — handled separately
    root_class = get_chain_root_class(node)
    if root_class == DB_QUERY_BUILDER_ROOT:
        return False

    if node.type == "scoped_call_expression":
        return True

    if node.type == "member_call_expression":
        base = node.children[0] if node.children else None
        while base and base.type == "member_call_expression":
            base = base.children[0] if base.children else None
        if base and base.type == "scoped_call_expression":
            return True

    return False


def is_query_builder_call(node) -> bool:
    """
    Detect DB::table()->get() or DB::table()->first() without select().
    Root must be DB, method must be a QB terminal.
    """
    method = get_method_name(node)
    if method not in QB_TERMINAL_METHODS:
        return False

    root_class = get_chain_root_class(node)
    if root_class != DB_QUERY_BUILDER_ROOT:
        return False

    # must have table() in chain — confirms it's DB::table() not DB::select()
    chain = collect_chain_method_names(node)
    if "table" not in chain:
        return False

    return True


def detect(tree, source: bytes) -> List[Dict[str, Any]]:
    findings = []
    seen_lines = set()

    all_nodes = collect_all_nodes(tree.root_node)

    for node in all_nodes:
        # skip non-DB facade calls (Cache, Session, Redis, Http, File, Arr etc.)
        if is_non_db_facade(node, source):
            continue

        # skip in-memory collection operations e.g. ->get()->first()
        if is_collection_operation(node):
            continue

        line = get_line(node)
        if line in seen_lines:
            continue

        # ── Query Builder detection (DB::table()) ────────────
        if is_query_builder_call(node):

            # skip transaction locks
            if chain_has_lock(node):
                continue

            # already has select in chain — optimised, skip
            if chain_has_select(node):
                continue

            method = get_method_name(node)
            in_loop = is_inside_loop(node)

            if in_loop:
                findings.append({
                    "rule_id": "R3",
                    "context": 6,
                    "line": line,
                    "severity": "very high",
                    "weight": 80,
                    "title": "DB::table() SELECT * inside loop — full row fetch per iteration",
                    "description": (
                        f"DB::table()->{method}() without select() inside a loop — "
                        f"fetches ALL columns on every iteration. "
                        f"Query Builder equivalent of N+1 + SELECT * combined."
                    ),
                    "suggestion": (
                        "Move query outside the loop. "
                        "Add ->select(['col1','col2']) before ->get(). "
                        "Example: DB::table('table')->select('id','col1')->whereIn('id',$ids)->get()"
                    ),
                })
            elif method in {"first", "firstOrFail"}:
                findings.append({
                    "rule_id": "R3",
                    "context": 7,
                    "line": line,
                    "severity": "low",
                    "weight": 15,
                    "title": f"DB::table()->{method}() without select() — SELECT *",
                    "description": (
                        f"Query Builder {method}() fetches one row but selects all columns. "
                        f"Add select() to fetch only needed columns."
                    ),
                    "suggestion": (
                        f"Add ->select() before ->{method}(): "
                        f"DB::table('table')->select('col1','col2')->where(...)->{method}()"
                    ),
                })
            else:
                findings.append({
                    "rule_id": "R3",
                    "context": 6,
                    "line": line,
                    "severity": "medium",
                    "weight": 25,
                    "title": "DB::table()->get() without select() — SELECT *",
                    "description": (
                        "Query Builder get() fetches all columns. "
                        "Developer added WHERE conditions but left SELECT * untouched."
                    ),
                    "suggestion": (
                        "Add ->select(['col1','col2']) before ->get(): "
                        "DB::table('table')->select('col1','col2')->where(...)->get()"
                    ),
                })
            seen_lines.add(line)
            continue

        # ── Eloquent detection ────────────────────────────────
        if not is_terminal_eloquent_call(node):
            continue

        method = get_method_name(node)

        # skip transaction locks
        if chain_has_lock(node):
            continue

        # already has select()/selectRaw()/addSelect() in chain — optimised, skip
        if chain_has_select(node):
            continue

        in_loop = is_inside_loop(node)
        has_with = chain_has_with(node)

        # ── Context 5 — inside loop, no select (WORST) ───────
        if in_loop:
            findings.append({
                "rule_id": "R3",
                "context": 5,
                "line": line,
                "severity": "very high",
                "weight": 90,
                "title": "SELECT * inside loop — N+1 + full row fetch combined",
                "description": (
                    f"Eloquent {method}() without select() inside a loop — "
                    f"fetches ALL columns for N iterations. "
                    f"Compounds Rule 1 (N queries) and Rule 3 (full row) together."
                ),
                "suggestion": (
                    "Move the query outside the loop using whereIn(). "
                    "Add select() to fetch only needed columns. "
                    "Example: Model::select('id','col1','col2')"
                    "->whereIn('foreign_key', $ids)->get()->groupBy('foreign_key')"
                ),
            })
            seen_lines.add(line)
            continue

        # ── Context 1 — Model::all() ──────────────────────────
        if method == "all" and node.type == "scoped_call_expression":
            findings.append({
                "rule_id": "R3",
                "context": 1,
                "line": line,
                "severity": "high",
                "weight": 50,
                "title": "Model::all() — full table dump with SELECT *",
                "description": (
                    "all() fetches every row and every column. "
                    "No WHERE, no LIMIT, no column selection. "
                    "Entire table loaded into memory."
                ),
                "suggestion": (
                    "Replace with Model::select('id','col1','col2')->paginate(50). "
                    "If all rows needed: Model::select('id','col1','col2')->get()"
                ),
            })
            seen_lines.add(line)
            continue

        # ── Context 4 — with() present but no select ─────────
        if has_with:
            findings.append({
                "rule_id": "R3",
                "context": 4,
                "line": line,
                "severity": "high",
                "weight": 40,
                "title": "Eager load with() missing select() — SELECT * on both models",
                "description": (
                    "with() eager loading fixed the N+1 problem but "
                    "still fetches ALL columns on parent and related model."
                ),
                "suggestion": (
                    "Add select() on parent model and use a closure on related: "
                    "Model::select('id','name','email')"
                    "->with(['relationship' => fn($q) => "
                    "$q->select('id','foreign_key','col1')])->get(). "
                    "Always include the foreign key in the related select."
                ),
            })
            seen_lines.add(line)
            continue

        # ── Context 2 — get() without select ─────────────────
        if method == "get":
            findings.append({
                "rule_id": "R3",
                "context": 2,
                "line": line,
                "severity": "medium",
                "weight": 30,
                "title": "Eloquent get() without select() — SELECT *",
                "description": (
                    "get() executes SELECT * — fetches all columns. "
                    "Developer added WHERE but left SELECT * untouched."
                ),
                "suggestion": (
                    "Add select() before get(): "
                    "Model::select('id','col1','col2')->where(...)->get()"
                ),
            })
            seen_lines.add(line)
            continue

        # ── Context 3 — first() without select ───────────────
        if method in {"first", "firstOrFail"}:
            findings.append({
                "rule_id": "R3",
                "context": 3,
                "line": line,
                "severity": "low",
                "weight": 20,
                "title": f"Eloquent {method}() without select() — SELECT * on single row",
                "description": (
                    f"{method}() fetches one row but still selects all columns. "
                    "Multiplied across thousands of auth/lookup requests per day."
                ),
                "suggestion": (
                    f"Add select() before {method}(): "
                    f"Model::select('id','name','email')->where(...)->{method}()"
                ),
            })
            seen_lines.add(line)
            continue

        # ── paginate without select ───────────────────────────
        if method == "paginate":
            findings.append({
                "rule_id": "R3",
                "context": 2,
                "line": line,
                "severity": "medium",
                "weight": 25,
                "title": "Eloquent paginate() without select() — SELECT *",
                "description": (
                    "paginate() limits rows correctly but still fetches all columns."
                ),
                "suggestion": (
                    "Add select() before paginate(): "
                    "Model::select('id','col1','col2')->paginate(50)"
                ),
            })
            seen_lines.add(line)

    return findings