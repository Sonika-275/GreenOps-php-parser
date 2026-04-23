"""
rule2_count_recalc.py
Detects count() recalculation in loop conditions.

Contexts detected:
  C1 — count() in for loop condition
  C2 — count() in while loop condition

Key false-positive guard:
  Skip if the array/collection being counted is MODIFIED inside the loop body.
  Plain PHP array count() is O(1) — but Laravel Collections are NOT.
  We flag both since we cannot reliably distinguish at static analysis time,
  but we note when it's likely a plain array.
"""

from typing import List, Dict, Any


LOOP_TYPES = {"for_statement", "while_statement"}


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


def get_variable_name_from_count_arg(count_call_node, source: bytes) -> str:
    """
    Extract the variable name passed to count().
    count($txns) → 'txns'
    """
    for child in count_call_node.children:
        if child.type == "arguments":
            for arg in child.children:
                if arg.type == "argument":
                    for sub in arg.children:
                        if sub.type == "variable_name":
                            for name_node in sub.children:
                                if name_node.type == "name":
                                    return name_node.text.decode("utf-8")
    return None


def is_array_modified_in_body(var_name: str, loop_body_node, source: bytes) -> bool:
    """
    Check if the variable is modified (push, pop, unset, reassigned)
    inside the loop body. If yes — skip to avoid false positive.
    Modifying operations: array_push, array_pop, array_splice,
    array_shift, unset, direct assignment $var = ...
    """
    if loop_body_node is None:
        return False

    modifying_functions = {
        "array_push", "array_pop", "array_splice",
        "array_shift", "array_unshift", "unset",
    }

    body_nodes = collect_all_nodes(loop_body_node)

    for node in body_nodes:
        # Direct assignment: $var = something
        if node.type == "assignment_expression":
            left = node.children[0] if node.children else None
            if left and left.type == "variable_name":
                for name_child in left.children:
                    if name_child.type == "name":
                        if name_child.text.decode("utf-8") == var_name:
                            return True

        # Function call with modifying function
        if node.type == "function_call_expression":
            func_name = None
            for child in node.children:
                if child.type == "name":
                    func_name = child.text.decode("utf-8")
            if func_name in modifying_functions:
                # Check if our variable is an argument
                for child in node.children:
                    if child.type == "arguments":
                        arg_text = get_node_text(child, source)
                        if var_name in arg_text:
                            return True

        # $var[] = value  (array append)
        if node.type == "assignment_expression":
            left = node.children[0] if node.children else None
            if left and left.type == "subscript_expression":
                base = left.children[0] if left.children else None
                if base and base.type == "variable_name":
                    for name_child in base.children:
                        if name_child.type == "name":
                            if name_child.text.decode("utf-8") == var_name:
                                return True

    return False


def find_for_loop_condition(for_node):
    """
    In a for_statement: for ($i=0; $i < count($x); $i++)
    The condition is the binary_expression between the two semicolons.
    Tree-sitter structures it with ; separating init, condition, update.
    """
    # Children of for_statement:
    # for ( assignment_expression ; binary_expression ; update_expression ) compound_statement
    # We need the binary_expression (condition part)
    children = for_node.children
    semicolon_count = 0
    for child in children:
        if child.type == ";":
            semicolon_count += 1
        elif semicolon_count == 1:
            # This is the condition part
            return child
    return None


def find_loop_body(loop_node):
    """Return the compound_statement (body) of a loop."""
    for child in loop_node.children:
        if child.type == "compound_statement":
            return child
    return None


def find_count_calls_in_node(node) -> List:
    """Find all count() function_call_expression nodes within a given node."""
    result = []
    queue = [node]
    while queue:
        current = queue.pop(0)
        if current.type == "function_call_expression":
            for child in current.children:
                if child.type == "name" and child.text == b"count":
                    result.append(current)
                    break
        queue.extend(current.children)
    return result


def detect(tree, source: bytes) -> List[Dict[str, Any]]:
    findings = []
    seen_lines = set()

    all_nodes = collect_all_nodes(tree.root_node)

    for node in all_nodes:
        if node.type not in LOOP_TYPES:
            continue

        if node.type == "for_statement":
            condition_node = find_for_loop_condition(node)
            if condition_node is None:
                continue

            count_calls = find_count_calls_in_node(condition_node)
            for count_call in count_calls:
                line = get_line(count_call)
                if line in seen_lines:
                    continue

                var_name = get_variable_name_from_count_arg(count_call, source)
                loop_body = find_loop_body(node)

                # False positive guard — skip if array is mutated in body
                if var_name and is_array_modified_in_body(var_name, loop_body, source):
                    continue

                fix_var = f"${var_name}" if var_name else "$collection"
                findings.append({
                    "rule_id": "R2",
                    "context": 1,
                    "line": line,
                    "severity": "medium",
                    "weight": 30,
                    "title": "count() recalculated on every for loop iteration",
                    "description": (
                        f"count({fix_var}) is evaluated each iteration — "
                        f"O(N²) operations for a collection of N items."
                    ),
                    "suggestion": (
                        f"Store count before the loop: "
                        f"$total = count({fix_var}); "
                        f"then use $total in the condition."
                    ),
                })
                seen_lines.add(line)

        elif node.type == "while_statement":
            # while ($processed < count($pending))
            # Condition is inside the parentheses — direct child of while_statement
            condition_node = None
            for child in node.children:
                # The condition in while is the expression between ( and )
                if child.type not in {"while", "(", ")", "compound_statement", ":"}:
                    condition_node = child
                    break

            if condition_node is None:
                continue

            count_calls = find_count_calls_in_node(condition_node)
            for count_call in count_calls:
                line = get_line(count_call)
                if line in seen_lines:
                    continue

                var_name = get_variable_name_from_count_arg(count_call, source)
                loop_body = find_loop_body(node)

                if var_name and is_array_modified_in_body(var_name, loop_body, source):
                    continue

                fix_var = f"${var_name}" if var_name else "$collection"
                findings.append({
                    "rule_id": "R2",
                    "context": 2,
                    "line": line,
                    "severity": "medium",
                    "weight": 25,
                    "title": "count() recalculated on every while loop iteration",
                    "description": (
                        f"count({fix_var}) in while condition — "
                        f"recounted every iteration."
                    ),
                    "suggestion": (
                        f"Store count before the loop: "
                        f"$total = count({fix_var}); "
                        f"Use $total in while condition. "
                        f"Only skip this if the collection changes inside the loop."
                    ),
                })
                seen_lines.add(line)

    return findings