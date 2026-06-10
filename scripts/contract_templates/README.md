# Contract template pipeline (docx → Jinja HTML)

The five Contract Template bodies under
[`erpnext_enhancements/templates/contracts/`](../../erpnext_enhancements/templates/contracts/)
are **generated** from Brian's agreement .docx suite (Apr 2026 revision) by the
two scripts here — never hand-retyped, so the legal text is transferred verbatim.

## When the source agreements are revised

1. `python scripts/contract_templates/convert.py <folder-with-the-docx-files>`
   — mechanical docx→HTML conversion (body order preserved, 6+ underscore runs
   become `{{ BLANK }}` tokens).
2. `python scripts/contract_templates/jinjify.py`
   — assertion-checked string surgery that injects the Jinja fill points
   (party/customer data, dates, money fields, checkbox states, milestone /
   equipment / phase / service-option loops) and the maintenance agreement's
   conditional payment-authorization block. **Every replacement asserts its
   expected match count** — if the revised docx moved or reworded a fill
   point, the script fails loudly at that spot instead of silently shipping a
   template with a dead blank; update the corresponding mapping and re-run
   (step 1 regenerates pristine inputs, so the pair is safe to re-run
   together any number of times).
3. Smoke-test: every template must parse and render with both an empty and a
   populated context (see `tests/test_project_contract.py`, which does this
   against a stub context on the bench).
4. Deploy note: the `seed_contract_templates` patch is **insert-only** — on
   sites where the Contract Template records already exist, paste the
   regenerated body into the record (or ship a deliberate update patch).

Render-time context (helpers + doc) is defined in
`project_enhancements/doctype/project_contract/project_contract.py::_render_context`.
