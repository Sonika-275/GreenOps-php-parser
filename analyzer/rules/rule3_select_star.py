"""
rule3_select_star.py
Detects SELECT * (missing select()) patterns in Eloquent queries.

Contexts detected:
  C1 — Model::all()                          HIGH
  C2 — Model::...->get() without select()    MEDIUM
  C3 — Model::...->first() without select()  MEDIUM
  C4 — Model::with()->get() without select() HIGH  (both parent + related)
  C5 — get()/all() INSIDE a loop             VERY HIGH (Rule1 + Rule3 combined)

Key logic:
  Walk the method chain upward from the terminal method.
  Check if select() appears anywhere in that chain.
  If not — flag.
"""

from typing import List, Dict, Any

TERMINAL_METHODS = {"get", "all", "first", "paginate", "firstOrFail", "findOrFail"}
# count() is excluded — it doesn't fetch row data

LOOP_TYPES = {"foreach_statement", "for_statement", "while_statement"}


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
            # For member_call: [obj, ->, name, arguments]
            # For scoped_call: [ClassName, ::, name, arguments]
            if child.type == "name":
                # Skip the class name in scoped (first child is class name)
                if node.type == "scoped_call_expression" and i == 0:
                    continue
                return child.text.decode("utf-8")
    return ""


def collect_chain_method_names(terminal_node) -> List[str]:
    """
    Walk the full method chain starting from terminal node upward.
    Returns all method names in the chain.
    e.g. Customer::select(...)->with(...)->get() → ['get', 'with', 'select']
    """
    names = []
    current = terminal_node

    while current is not None:
        name = get_method_name(current)
        if name:
            names.append(name)

        # Walk to the object/base of this call
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
    """Check if 'select' appears anywhere in the method chain."""
    return "select" in collect_chain_method_names(terminal_node)


def chain_has_with(terminal_node) -> bool:
    """Check if 'with' appears anywhere in the method chain."""
    return "with" in collect_chain_method_names(terminal_node)


def is_terminal_eloquent_call(node) -> bool:
    """
    Check if this node is a terminal Eloquent call:
    - Model::all()  — scoped_call_expression with method in TERMINAL_METHODS
    - Model::...->get() — member_call_expression with method in TERMINAL_METHODS
      where base chain starts from scoped_call_expression (Class::)
    """
    method = get_method_name(node)
    if method not in TERMINAL_METHODS:
        return False

    if node.type == "scoped_call_expression":
        # Class::all() — direct static terminal
        return True

    if node.type == "member_call_expression":
        # Walk to base — must originate from Class:: (scoped_call)
        base = node.children[0] if node.children else None
        while base and base.type == "member_call_expression":
            base = base.children[0] if base.children else None
        if base and base.type == "scoped_call_expression":
            return True

    return False


def detect(tree, source: bytes) -> List[Dict[str, Any]]:
    findings = []
    seen_lines = set()

    all_nodes = collect_all_nodes(tree.root_node)

    for node in all_nodes:
        if not is_terminal_eloquent_call(node):
            continue

        method = get_method_name(node)
        line = get_line(node)

        if line in seen_lines:
            continue

        # Already has select() in chain — optimised, skip
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
                "severity": "medium",
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