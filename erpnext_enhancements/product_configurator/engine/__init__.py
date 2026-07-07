"""Pure product-configurator engine (stdlib only — no frappe).

Prices a configuration, builds its part number, explodes its parts list and
resolves its build instructions from a product-definition dict. Invariant:
nothing in this package may import ``frappe`` — that keeps the Excel-workbook
golden tests runnable bench-free (see tests/test_product_configurator_engine.py)
and lets the desk endpoints, the controller and the seed patch share one
implementation.
"""

from .buildsteps import render_build_steps
from .conditions import ConditionError, render_text, safe_eval_expr
from .partnumber import build_context, build_part_number, validate_selections
from .parts import explode_parts
from .pricing import price_configuration

__all__ = [
	"ConditionError",
	"build_context",
	"build_part_number",
	"explode_parts",
	"price_configuration",
	"render_build_steps",
	"render_text",
	"safe_eval_expr",
	"validate_selections",
]
