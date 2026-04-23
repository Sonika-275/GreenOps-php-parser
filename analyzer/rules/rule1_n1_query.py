"""
rule1_n1_query.py
Detects Eloquent N+1 query patterns in PHP/Laravel code.

Contexts detected:
  C1 — Relationship call inside foreach  ($user->posts()->get())
  C2 — Static model call inside foreach  (User::find($id))
  C3 — Nested foreach with DB call       (CRITICAL — N×M queries)
  C4 — N+1 hidden inside a function/method

Decision tree mirrors the docx exactly.
"""

from typing import List, Dict, Any

# Eloquent methods that execute a query (terminal methods)
ELOQUENT_TERMINAL_METHODS = {
    "get", "find", "first", "all", "paginate",
    "firstOrFail", "findOrFail", "findMany",
    "sole", "value", "pluck", "count",
    "exists", "doesntExist", "sum", "avg",
    "min", "max", "latest", "oldest",
}

# Static Eloquent entry points — Class::method()
ELOQUENT_STATIC_METHODS = {
    "find", "findMany", "findOrFail", "first",
    "firstOrFail", "all", "get", "where",
    "whereIn", "with", "select", "orderBy",
    "latest", "oldest", "paginate", "create",
    "destroy",
}

LOOP_TYPES = {"foreach_statement", "for_statement", "while_statement"}


# ── AST Traversal Helpers ────────────────────────────────────

def get_ancestors(node) -> List:
    """Walk up the AST and return all ancestor nodes."""
    ancestors = []
    current = node.parent
    while current is not None:
        ancestors.append(current)
        current = current.parent
    return ancestors


def is_inside_loop(node) -> bool:
    """Check if node is inside any loop."""
    for ancestor in get_ancestors(node):
        if ancestor.type in LOOP_TYPES:
            return True
    return False


def get_enclosing_loop(node):
    """Return the immediate enclosing loop node, or None."""
    for ancestor in get_ancestors(node):
        if ancestor.type in LOOP_TYPES:
            return ancestor
    return None


def is_inside_nested_loop(node) -> bool:
    """Check if node is inside at least two nested loops."""
    loop_count = 0
    for ancestor in get_ancestors(node):
        if ancestor.type in LOOP_TYPES:
            loop_count += 1
        if loop_count >= 2:
            return True
    return False


def is_inside_function(node) -> bool:
    """Check if node is inside a function/method definition."""
    for ancestor in get_ancestors(node):
        if ancestor.type in {
            "function_definition",
            "method_declaration",
            "arrow_function",
            "anonymous_function_creation_expression",
        }:
            return True
    return False


def get_node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def get_line(node) -> int:
    return node.start_point[0] + 1  # 1-indexed


# ── Chain Analysis ────────────────────────────────────────────

def get_method_chain_names(node, source: bytes) -> List[str]:
    """
    Walk up member_call_expression chain and collect all method names.
    e.g. User::where()->with()->get() → ['where', 'with', 'get']
    """
    names = []
    current = node
    while current is not None:
        if current.type == "member_call_expression":
            # name child is the method name
            for child in current.children:
                if child.type == "name":
                    names.append(child.text.decode("utf-8"))
        elif current.type == "scoped_call_expression":
            for child in current.children:
                if child.type == "name" and child != current.children[0]:
                    names.append(child.text.decode("utf-8"))
            break
        # walk down into the object/callee
        current = current.children[0] if current.children else None
    return names


def is_relationship_call(node, source: bytes) -> bool:
    """
    Detect: $model->relationship()->get()
    Pattern: member_call_expression → member_call_expression
    The outer call ends with an Eloquent terminal.
    The inner call is on a variable (not Class::).
    """
    if node.type != "member_call_expression":
        return False

    # Get the method name of this call
    method_name = None
    for child in node.children:
        if child.type == "name":
            method_name = child.text.decode("utf-8")

    if method_name not in ELOQUENT_TERMINAL_METHODS:
        return False

    # The object being called on must be a member_call_expression
    # (meaning it's $var->something()->get(), not Class::something()->get())
    obj = node.children[0] if node.children else None
    if obj is None:
        return False

    # $user->posts()->get() — obj is member_call_expression on a variable
    if obj.type == "member_call_expression":
        # Check the base is a variable, not a scoped (Class::)
        base = obj
        while base.children and base.type == "member_call_expression":
            base = base.children[0]
        if base.type == "variable_name":
            return True

    return False


def is_static_model_call(node, source: bytes) -> bool:
    """
    Detect: Model::find($id), Model::where()->get() inside loop
    Pattern: scoped_call_expression OR member_call_expression on scoped_call_expression
    """
    if node.type == "scoped_call_expression":
        method_name = None
        for child in node.children:
            if child.type == "name" and child != node.children[0]:
                method_name = child.text.decode("utf-8")
        if method_name and method_name in ELOQUENT_TERMINAL_METHODS:
            return True

    if node.type == "member_call_expression":
        method_name = None
        for child in node.children:
            if child.type == "name":
                method_name = child.text.decode("utf-8")
        if method_name not in ELOQUENT_TERMINAL_METHODS:
            return False
        # Walk to base — must be scoped_call_expression (Class::)
        base = node.children[0] if node.children else None
        while base and base.type == "member_call_expression":
            base = base.children[0] if base.children else None
        if base and base.type == "scoped_call_expression":
            return True

    return False


# ── Main Traversal ────────────────────────────────────────────

def collect_all_nodes(root) -> List:
    """BFS collect all nodes in tree."""
    result = []
    queue = [root]
    while queue:
        node = queue.pop(0)
        result.append(node)
        queue.extend(node.children)
    return result


def detect(tree, source: bytes) -> List[Dict[str, Any]]:
    findings = []
    seen_lines = set()  # avoid duplicate findings on same line

    all_nodes = collect_all_nodes(tree.root_node)

    for node in all_nodes:
        line = get_line(node)
        if line in seen_lines:
            continue

        # ── Check relationship call ($var->rel()->get()) ──────
        if is_relationship_call(node, source):
            if not is_inside_loop(node):
                continue

            if line in seen_lines:
                continue

            in_nested = is_inside_nested_loop(node)
            in_function = is_inside_function(node)

            if in_nested:
                # Context 3 — CRITICAL
                findings.append({
                    "rule_id": "R1",
                    "context": 3,
                    "line": line,
                    "severity": "very high",
                    "weight": 90,
                    "title": "N+1: Eloquent relationship call in nested foreach",
                    "description": (
                        "Relationship accessed inside nested loops — "
                        "triggers N×M database queries."
                    ),
                    "suggestion": (
                        "Use eager loading with dot notation: "
                        "Order::with('items.product')->get() "
                        "before the loop."
                    ),
                })
            else:
                # Context 1 or Context 4
                if in_function:
                    fix = (
                        "Use $collection->load('relationship') before the loop "
                        "since you're inside a function. "
                        "Use with() if you control the query."
                    )
                else:
                    fix = (
                        "Use eager loading: Model::with('relationship')->get() "
                        "before the foreach loop."
                    )
                findings.append({
                    "rule_id": "R1",
                    "context": 4 if in_function else 1,
                    "line": line,
                    "severity": "high",
                    "weight": 60,
                    "title": "N+1: Eloquent relationship call inside foreach",
                    "description": (
                        "Relationship method called inside foreach — "
                        "executes one query per iteration."
                    ),
                    "suggestion": fix,
                })
            seen_lines.add(line)

        # ── Check static model call (Model::find() etc.) ──────
        elif is_static_model_call(node, source):
            if not is_inside_loop(node):
                continue

            if line in seen_lines:
                continue

            in_nested = is_inside_nested_loop(node)
            in_function = is_inside_function(node)

            if in_nested:
                findings.append({
                    "rule_id": "R1",
                    "context": 3,
                    "line": line,
                    "severity": "very high",
                    "weight": 90,
                    "title": "N+1: Static Eloquent call in nested foreach",
                    "description": (
                        "Model::find() or similar inside nested loops — "
                        "N×M queries triggered."
                    ),
                    "suggestion": (
                        "Use whereIn() + keyBy() to batch-load all records "
                        "before the loop. Example: "
                        "Model::whereIn('id', $ids)->get()->keyBy('id')"
                    ),
                })
            else:
                if in_function:
                    fix = (
                        "Collect all IDs before the function call, "
                        "use whereIn() + keyBy() to batch-load, "
                        "then loop over the in-memory map."
                    )
                else:
                    fix = (
                        "Use Model::whereIn('id', $ids)->get()->keyBy('id') "
                        "before the loop instead of querying inside it."
                    )
                findings.append({
                    "rule_id": "R1",
                    "context": 4 if in_function else 2,
                    "line": line,
                    "severity": "high",
                    "weight": 60,
                    "title": "N+1: Static Eloquent query inside foreach",
                    "description": (
                        "Model::find() or Model::where() called inside loop — "
                        "executes one query per iteration."
                    ),
                    "suggestion": fix,
                })
            seen_lines.add(line)

    return findings