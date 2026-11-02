# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Tools an agent may call.

A tool is pure metadata here — the executable registry lives in the
service layer (P2) and maps `code` to a handler. `tier_min` gates a
tool to the capability tier of the model actually connected, so a 4B
local model never receives a tool calling instruction it cannot follow.
"""

from odoo import api, fields, models


class NovaAiTool(models.Model):
    _name = "nova.ai.tool"
    _description = "Nova AI Tool"
    _order = "tier_min, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Selection(
        [
            ("search_records", "Search records"),
            ("read_record", "Read record"),
            ("count_records", "Count records"),
            ("open_view", "Open view"),
            ("summarize_thread", "Summarize chatter thread"),
            ("translate_message", "Translate message"),
            ("draft_reply", "Draft reply"),
            ("next_steps", "Suggest next steps"),
            ("create_draft", "Create draft record"),
            ("log_note", "Log note"),
            ("schedule_activity", "Schedule activity"),
            ("confirm_sale_order", "Confirm sale order"),
            ("validate_invoice", "Validate invoice"),
            ("set_field", "Set field value"),
            ("copy_record", "Duplicate record"),
            ("read_related", "Read related lines"),
            ("unlink_record", "Delete record"),
            ("execute_method", "Run business method"),
            ("cancel_invoice", "Cancel invoice"),
            ("cancel_sale_order", "Cancel sale order"),
            ("confirm_purchase_order", "Confirm purchase order"),
            ("validate_delivery", "Validate delivery"),
            ("invoice_order", "Invoice sale order"),
            ("add_order_line", "Add order line"),
            ("register_payment", "Register invoice payment"),
            ("reconcile_moves", "Reconcile invoice"),
            ("reverse_move", "Credit note"),
            ("resolve_ticket", "Resolve ticket"),
            ("log_timesheet", "Log timesheet"),
        ("read_group", "Aggregate records"),
        ("fields_get", "List model fields"),
        ("inspect_method", "Inspect method signature"),
        ("ask_user", "Ask the user a question"),
        ("list_attachments", "List attachments"),
            ("digitize_bill", "Digitize vendor bill"),
            ("fetch_url", "Fetch web page"),
            ("search_knowledge", "Search knowledge sources"),
            ("read_attachment", "Read attachment"),
            ("read_knowledge", "Read a knowledge concept"),
            ("write_knowledge", "Propose a knowledge concept"),
            ("list_flows", "List saved flows"),
            ("run_flow", "Run a saved flow"),
            ("list_flow_runs", "List flow runs"),
            ("set_fields", "Update several fields at once"),
            ("build_domain", "Build a domain"),
            ("list_models", "List usable models"),
            ("validate_write", "Check a write before making it"),
            ("diagnose_access", "Diagnose what the user may touch"),
            ("scan_codebase", "Scan custom code into knowledge"),
            ("draft_flow", "Save a plan as a reusable flow"),
            ("update_flow", "Update a saved flow"),
            ("export_flow", "Export a saved flow"),
            ("import_flow", "Import a flow from JSON"),
        # --- business-tool specs (generated; nova_ai_business_tools owns names/descriptions) ---
        ("add_bom_line", "Add bom line"),
        ("add_event_attendees", "Add event attendees"),
        ("add_order_note", "Add order note"),
        ("add_partner_bank", "Add partner bank"),
        ("add_po_line", "Add po line"),
        ("add_task_follower", "Add task follower"),
        ("adjust_quantity", "Adjust quantity"),
        ("approve_expense", "Approve expense"),
        ("approve_leave", "Approve leave"),
        ("approve_purchase_order", "Approve purchase order"),
        ("archive_any_record", "Archive any record"),
        ("archive_quotation", "Archive quotation"),
        ("assign_picking", "Assign picking"),
        ("assign_task", "Assign task"),
        ("bill_purchase_order", "Bill purchase order"),
        ("block_partner", "Block partner"),
        ("cancel_mo", "Cancel mo"),
        ("categorize_partner", "Categorize partner"),
        ("check_availability", "Check availability"),
        ("check_invoice_payments", "Check invoice payments"),
        ("check_mo_availability", "Check mo availability"),
        ("check_order_delivery", "Check order delivery"),
        ("check_po_receipt", "Check po receipt"),
        ("check_reorder", "Check reorder"),
        ("close_task", "Close task"),
        ("confirm_mo", "Confirm mo"),
        ("create_applicant", "Create applicant"),
        ("create_bank_account", "Create bank account"),
        ("create_bom", "Create bom"),
        ("create_bom_multilevel", "Create bom multilevel"),
        ("create_child_contact", "Create child contact"),
        ("create_customer_full", "Create customer full"),
        ("create_customer_invoice", "Create customer invoice"),
        ("create_delivery_order", "Create delivery order"),
        ("create_expense", "Create expense"),
        ("create_internal_transfer", "Create internal transfer"),
        ("create_lead", "Create lead"),
        ("create_leave", "Create leave"),
        ("create_lot", "Create lot"),
        ("create_milestone", "Create milestone"),
        ("create_misc_entry", "Create misc entry"),
        ("create_mo", "Create mo"),
        ("create_product_full", "Create product full"),
        ("create_project", "Create project"),
        ("create_quotation", "Create quotation"),
        ("create_reminder_activity", "Create reminder activity"),
        ("create_rfq", "Create rfq"),
        ("create_subtask", "Create subtask"),
        ("create_task", "Create task"),
        ("create_todo_activity", "Create todo activity"),
        ("create_unbuild", "Create unbuild"),
        ("create_vendor", "Create vendor"),
        ("create_vendor_bill", "Create vendor bill"),
        ("create_vendor_bill_draft", "Create vendor bill draft"),
        ("create_vendor_full", "Create vendor full"),
        ("duplicate_any_record", "Duplicate any record"),
        ("duplicate_bom", "Duplicate bom"),
        ("duplicate_invoice", "Duplicate invoice"),
        ("duplicate_quotation", "Duplicate quotation"),
        ("duplicate_rfq", "Duplicate rfq"),
        ("finish_mo", "Finish mo"),
        ("log_lead_call", "Log lead call"),
        ("mark_activity_done", "Mark activity done"),
        ("mark_lost", "Mark lost"),
        ("mark_won", "Mark won"),
        ("move_task_stage", "Move task stage"),
        ("notify_record_followers", "Notify record followers"),
        ("post_channel_message", "Post channel message"),
        ("qualify_lead", "Qualify lead"),
        ("reassign_lead", "Reassign lead"),
        ("refuse_leave", "Refuse leave"),
        ("reopen_quotation", "Reopen quotation"),
        ("reset_move_to_draft", "Reset move to draft"),
        ("schedule_lead_meeting", "Schedule lead meeting"),
        ("schedule_meeting_event", "Schedule meeting event"),
        ("scrap_product", "Scrap product"),
        ("send_email_record", "Send email record"),
        ("send_invoice_email", "Send invoice email"),
        ("send_quotation_email", "Send quotation email"),
        ("send_rfq_email", "Send rfq email"),
        ("set_applicant_stage", "Set applicant stage"),
        ("set_event_location", "Set event location"),
        ("set_invoice_due_date", "Set invoice due date"),
        ("set_invoice_payment_terms", "Set invoice payment terms"),
        ("set_invoice_reference", "Set invoice reference"),
        ("set_journal_on_draft", "Set journal on draft"),
        ("set_lead_stage", "Set lead stage"),
        ("set_mo_quantity", "Set mo quantity"),
        ("set_move_quantity_done", "Set move quantity done"),
        ("set_order_client_ref", "Set order client ref"),
        ("set_order_commitment_date", "Set order commitment date"),
        ("set_order_discount", "Set order discount"),
        ("set_order_payment_terms", "Set order payment terms"),
        ("set_order_pricelist", "Set order pricelist"),
        ("set_order_salesperson", "Set order salesperson"),
        ("set_partner_payment_terms", "Set partner payment terms"),
        ("set_picking_date", "Set picking date"),
        ("set_po_buyer", "Set po buyer"),
        ("set_po_date_planned", "Set po date planned"),
        ("set_po_payment_terms", "Set po payment terms"),
        ("set_product_category", "Set product category"),
        ("set_product_cost", "Set product cost"),
        ("set_product_flags", "Set product flags"),
        ("set_product_price", "Set product price"),
        ("set_product_supplier", "Set product supplier"),
        ("set_task_deadline", "Set task deadline"),
        ("set_task_priority", "Set task priority"),
        ("set_vendor_price", "Set vendor price"),
        ("start_mo", "Start mo"),
        ("submit_expense", "Submit expense"),
        ("unreserve_picking", "Unreserve picking"),
        ("validate_receipt", "Validate receipt"),
        ("validate_vendor_profile", "Validate vendor profile"),
        ],
        required=True,
    )
    description = fields.Text(
        required=True,
        help="What the model is told this tool does. Written for the model.",
    )
    schema = fields.Json(
        help="JSON Schema (object) describing the tool parameters.",
    )
    example = fields.Json(
        help="One copy-paste example call, for weak models and the "
             "discovery route.",
    )
    timeout_s = fields.Integer(
        default=60,
        help="Seconds a single call may take; network tools honor "
             "it, external clients use it as their client timeout.",
    )
    max_retries = fields.Integer(
        default=0,
        help="Transient failures retried this many times by the loop. "
             "Refusals (access errors) are never retried.",
    )
    rate_limit_per_hour = fields.Integer(
        default=0,
        help="How often this tool may run, per user, per rolling hour. "
             "0 means unlimited. An over-limit call is refused with the "
             "reset window named. Heavy calls (fetch, digitize) and "
             "expensive ones are the candidates - cheap reads stay "
             "unlimited.",
    )
    group_ids = fields.Many2many(
        "res.groups", string="Allowed groups",
        help="Empty = every user who may use the agent. Otherwise only "
             "members: the tool is hidden from discovery, the model "
             "never sees it, and queued cards for it cannot run.")
    tier_min = fields.Selection(
        [("t1", "T1 Basic"), ("t2", "T2 Assistant"), ("t3", "T3 Agent")],
        required=True,
        default="t2",
        help="Minimum model tier. T1 models are served deterministic "
             "server-side flows instead, and only see T1 tools.",
    )
    write_kind = fields.Selection(
        [("none", "Read only"), ("confirm", "Confirm required"),
         ("auto", "Auto apply")],
        required=True,
        default="none",
        help="Confirm tools always produce a card the user approves. "
             "Auto tools are rejected unless the agent runs auto mode.",
    )
    allowed_models = fields.Char(
        help="Comma-separated model allow-list (empty = every model).",
    )
    active = fields.Boolean(default=True)
    read_only = fields.Boolean(
        compute="_compute_hints", store=True,
        help="True when the tool never writes (MCP readOnlyHint).")
    destructive = fields.Boolean(
        compute="_compute_hints", store=True,
        help="True when the tool deletes or runs arbitrary methods "
             "(MCP destructiveHint): external clients must confirm.")

    _code_uniq = models.Constraint(
        "UNIQUE(code)", "One tool definition per code.")

    @api.depends("code", "write_kind")
    def _compute_hints(self):
        for tool in self:
            tool.read_only = tool.write_kind == "none"
            tool.destructive = tool.code in (
                "unlink_record", "execute_method")
