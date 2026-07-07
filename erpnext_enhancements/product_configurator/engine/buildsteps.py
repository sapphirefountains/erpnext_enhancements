"""Config-aware build-instruction resolution.

Step templates carry an optional ``condition`` (restricted expression over the
option context — ``timer_qty == 2``) and ``{expr}`` placeholders inside the
instruction text. Resolution filters + renders them into flat printable rows.
Author mistakes degrade gracefully: a bad condition skips the step with a
warning; a bad placeholder keeps the raw text with a warning — a typo on the
product definition must never block saving a configuration.
"""

from .conditions import ConditionError, render_text, safe_eval_expr


def render_build_steps(steps, ctx):
	"""Filter and render step templates against the option context.

	Returns ``(rendered, warnings)`` where rendered rows keep their template
	order and carry ``section_title`` / ``step_type`` / ``instruction``.
	"""
	rendered = []
	warnings = []
	for step in steps or []:
		condition = (step.get("condition") or "").strip()
		if condition:
			try:
				if not safe_eval_expr(condition, ctx):
					continue
			except ConditionError as e:
				warnings.append(f"Step skipped ({step.get('section_title')}): {e}")
				continue

		instruction = step.get("instruction") or ""
		try:
			instruction = render_text(instruction, ctx)
		except ConditionError as e:
			warnings.append(f"Placeholder left unrendered ({step.get('section_title')}): {e}")

		rendered.append(
			{
				"section_title": step.get("section_title") or "",
				"step_type": step.get("step_type") or "Build",
				"instruction": instruction,
			}
		)
	return rendered, warnings
