"""Restricted expression evaluator for option conditions and step templates.

Build-step conditions (``timer_qty == 2``) and instruction placeholders
(``"Insert {estop_qty + timer_qty} cable glands"``) are authored by users on
the Configurable Product form, so they must be evaluated without any sandbox-
escape surface. This is a ~50-line AST whitelist: boolean/comparison/arithmetic
expressions over the option context only — no calls, no attribute access, no
subscripts, no f-string tricks. Anything outside the whitelist raises
:class:`ConditionError`; callers surface it as a warning and skip the step
rather than crash the save.

Deliberately NOT ``frappe.safe_eval``: this package keeps the water-engine
invariant (stdlib only, no frappe import) so the pricing goldens run bench-free
in CI.
"""

import ast
import re

_ALLOWED_NODES = (
	ast.Expression,
	ast.BoolOp,
	ast.And,
	ast.Or,
	ast.UnaryOp,
	ast.Not,
	ast.USub,
	ast.UAdd,
	ast.Compare,
	ast.Eq,
	ast.NotEq,
	ast.Lt,
	ast.LtE,
	ast.Gt,
	ast.GtE,
	ast.In,
	ast.NotIn,
	ast.BinOp,
	ast.Add,
	ast.Sub,
	ast.Mult,
	ast.Div,
	ast.FloorDiv,
	ast.Mod,
	ast.IfExp,
	ast.Name,
	ast.Constant,
	ast.Load,
	ast.List,
	ast.Tuple,
)

_CONST_TYPES = (str, int, float, bool, type(None))

_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


class ConditionError(ValueError):
	"""A condition/template expression is invalid or references unknown names."""


def safe_eval_expr(expr, ctx):
	"""Evaluate a restricted expression against the option context.

	``ctx`` maps option keys to their selected values (Quantity -> int,
	Choice -> the choice code string, plus ``<key>_label`` strings).
	"""
	expr = (expr or "").strip()
	if not expr:
		return True
	try:
		tree = ast.parse(expr, mode="eval")
	except SyntaxError as e:
		raise ConditionError(f"Invalid expression {expr!r}: {e.msg}") from e
	for node in ast.walk(tree):
		if not isinstance(node, _ALLOWED_NODES):
			raise ConditionError(
				f"Expression {expr!r} uses disallowed syntax ({type(node).__name__})"
			)
		if isinstance(node, ast.Constant) and not isinstance(node.value, _CONST_TYPES):
			raise ConditionError(f"Expression {expr!r} uses a disallowed constant")
		if isinstance(node, ast.Name) and node.id not in ctx:
			raise ConditionError(f"Expression {expr!r} references unknown name {node.id!r}")
	try:
		return eval(  # noqa: S307 — AST-whitelisted above, builtins stripped
			compile(tree, "<configurator condition>", "eval"), {"__builtins__": {}}, dict(ctx)
		)
	except ZeroDivisionError as e:
		raise ConditionError(f"Expression {expr!r} divides by zero") from e


def _format_value(value):
	if isinstance(value, bool):
		return "yes" if value else "no"
	if isinstance(value, float):
		return f"{value:g}"
	return str(value)


def render_text(template, ctx):
	"""Render ``{expr}`` placeholders in an instruction/template string.

	Each placeholder body goes through :func:`safe_eval_expr`; a bad
	placeholder raises :class:`ConditionError` (callers keep the raw text and
	warn instead of failing the whole document).
	"""

	def _sub(match):
		return _format_value(safe_eval_expr(match.group(1), ctx))

	return _PLACEHOLDER.sub(_sub, template or "")
