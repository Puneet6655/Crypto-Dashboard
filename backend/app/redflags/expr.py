"""A small, safe expression language for rule conditions.

Rule packs are written by people outside the code base (an authority, a compliance team), so a
condition is never run with eval(). It is parsed with Python's ast module and only these parts are
allowed:

* numbers, True/False, and fact names (lower case letters, digits and "_")
* arithmetic: + - * / and unary minus
* comparisons: < <= > >= == != (chains such as 1 < x < 5 work)
* and, or, not, and brackets
* the functions min(), max() and abs()

Anything else (attribute access, indexing, strings, lambdas, other calls) is rejected when the pack
is loaded, so a bad pack fails early with a clear message instead of at lookup time.

A fact whose value is unknown (missing or None) makes the whole condition "not checked" rather than
false: a rule that could not run must never look like a clean result.
"""
from __future__ import annotations

import ast
import re

MAX_LENGTH = 500
FUNCTIONS = {"min": min, "max": max, "abs": abs}
NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class ExpressionError(ValueError):
    """The condition is not valid in the rule language."""


class MissingFacts(Exception):
    """The condition needs facts that are not available for this address."""

    def __init__(self, names: set[str]):
        super().__init__(", ".join(sorted(names)))
        self.names = names


_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
        ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b if b else float("inf")}
_CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


class Condition:
    """A parsed condition. names = the facts it reads."""

    def __init__(self, text: str):
        if not isinstance(text, str) or not text.strip():
            raise ExpressionError("The condition is empty.")
        if len(text) > MAX_LENGTH:
            raise ExpressionError(f"The condition is longer than {MAX_LENGTH} characters.")
        try:
            tree = ast.parse(text.strip(), mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"Not a valid condition: {exc.msg}") from None
        self.text = text.strip()
        self.names: set[str] = set()
        self._check(tree.body)
        self._tree = tree.body

    def _check(self, node: ast.AST) -> None:
        if isinstance(node, ast.BoolOp):
            for value in node.values:
                self._check(value)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub)):
            self._check(node.operand)
        elif isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            self._check(node.left)
            self._check(node.right)
        elif isinstance(node, ast.Compare) and all(type(op) in _CMP for op in node.ops):
            self._check(node.left)
            for comparator in node.comparators:
                self._check(comparator)
        elif isinstance(node, ast.Constant) and (isinstance(node.value, (int, float)) or node.value in (True, False)):
            pass
        elif isinstance(node, ast.Name):
            if node.id in FUNCTIONS:
                raise ExpressionError(f"'{node.id}' is a function: call it like {node.id}(a, b).")
            if not NAME.match(node.id):
                raise ExpressionError(f"Fact names are lower case with underscores: '{node.id}' is not allowed.")
            self.names.add(node.id)
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS
              and not node.keywords and node.args):
            for arg in node.args:
                self._check(arg)
        else:
            raise ExpressionError(f"'{ast.unparse(node)}' is not allowed in a condition "
                                  "(use numbers, fact names, + - * /, comparisons, and/or/not, min/max/abs).")

    def evaluate(self, facts: dict) -> bool:
        """True or False. Raises MissingFacts when any fact it reads is unknown."""
        missing = {n for n in self.names if facts.get(n) is None}
        if missing:
            raise MissingFacts(missing)
        return bool(self._eval(self._tree, facts))

    def _eval(self, node: ast.AST, facts: dict):
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return all(self._eval(v, facts) for v in node.values)
            return any(self._eval(v, facts) for v in node.values)
        if isinstance(node, ast.UnaryOp):
            value = self._eval(node.operand, facts)
            return (not value) if isinstance(node.op, ast.Not) else -value
        if isinstance(node, ast.BinOp):
            return _BIN[type(node.op)](self._eval(node.left, facts), self._eval(node.right, facts))
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, facts)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval(comparator, facts)
                if not _CMP[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            value = facts[node.id]
            return float(value) if isinstance(value, bool) else value
        if isinstance(node, ast.Call):
            return FUNCTIONS[node.func.id](*(self._eval(a, facts) for a in node.args))
        raise ExpressionError("unreachable")  # _check rejected everything else
