"""log_marketing_event — record a marker on the marketing trend (gated write, v1.757.0).

An agent that changes something marketing will measure -- the SEO session rewriting a page title, a
post going out, a campaign starting -- logs it here, so a jump or a dip on the Marketing Performance
trend has its reason drawn beside it. Creates one Marketing Event (``marketing/performance/events``).

WRITES: listed in the gate's ``APP_MUTATING`` set (``_gate.py``), so with AI write gating on it returns
an ``awaiting_user_confirmation`` envelope and an AI Pending Action, and creates nothing until a person
confirms. Risk Low (one create, deleted as easily). The confirming person's own Marketing Event
permission applies, because the gate re-runs ``execute`` as them.
"""

from typing import Any

from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._gate import annotations_for

_TYPES = ("Site Edit", "Social Post", "Campaign", "Ad", "Press", "Offline", "Other")


class LogMarketingEvent(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "log_marketing_event"  # must match module filename
        self.description = (
            "Log a marketing event: something that happened on a day and may move the marketing "
            "numbers, drawn as a marker on the Marketing Performance trend. Use it after changing "
            "the website (a page title, a meta description, a new page), publishing, or starting a "
            "campaign. Give 'event_date' (YYYY-MM-DD), a short 'title' saying what changed, an "
            "'event_type', and optionally a 'link' (the page) and 'notes'. This tool WRITES: when AI "
            "write gating is on it returns status='awaiting_user_confirmation' with an action_id and "
            "creates nothing until a human confirms it in ERPNext."
        )
        self.category = "Marketing"
        self.source_app = "erpnext_enhancements"
        self.requires_permission = "Marketing Event"
        self.annotations = annotations_for(self.name)
        self.inputSchema = {
            "type": "object",
            "properties": {
                "event_date": {"type": "string", "description": "The day it happened, YYYY-MM-DD. Required."},
                "title": {"type": "string", "description": "What happened, in a few words. Required."},
                "event_type": {"type": "string", "enum": list(_TYPES), "default": "Other"},
                "link": {"type": "string", "description": "Optional http(s) address, e.g. the page edited."},
                "notes": {"type": "string", "description": "Optional detail: what changed, from what to what."},
            },
            "required": ["event_date", "title"],
        }

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from erpnext_enhancements.marketing.performance.events import create_event

        args = arguments or {}
        result = create_event(
            args.get("event_date"),
            args.get("title"),
            args.get("event_type") or "Other",
            args.get("link"),
            args.get("notes"),
            source="Assistant",
        )
        return {"success": True, **result}
