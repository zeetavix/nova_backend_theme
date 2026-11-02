# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Business action tools — 100+ typed Odoo tools, one framework.

Every tool here is a small declarative SPEC; one generic runner
queues its confirm card and one generic approver applies it. That is
what makes 114 tools production-grade instead of 114 copy-pasted
handlers: validation, dupe guards, state guards, confirm cards,
approve-time rechecks and audit behave identically for all of them.

Ops: create | write | method | copy | note | activity | email |
read (returns data, never a card) | custom (per-tool runner below).

Rules every spec obeys:
* Reads run as the invoking user; record rules apply. No sudo reads.
* Writes never execute inline — queue time validates, approve time
  re-validates (state, required fields, access) inside a savepoint.
* Selection/m2o/date values coerce through the same helpers as the
  core tools, so "Acme" resolves to the partner and "tomorrow" to a
  date, and ambiguity asks instead of guessing.
"""

import re

from odoo import _, fields as _odoo_fields
from odoo.exceptions import AccessError

# ------------------------------------------------------------------
# spec helpers (specs are data; behavior lives in the runner)
# ------------------------------------------------------------------
# param kinds: str int float bool date datetime m2o(model) sel lines
# "req": True marks required. m2o accepts an id or a name.


def _specs():
    S = []

    def add(code, name, desc, op, model=None, tier="t2", **kw):
        spec = {"code": code, "name": name, "desc": desc, "op": op,
                "model": model, "tier": tier}
        spec.update(kw)
        S.append(spec)
        return spec

    P = lambda *params: list(params)  # noqa: E731
    R = lambda n, k="str", **kw: {"name": n, "kind": k, "req": True, **kw}
    O = lambda n, k="str", **kw: {"name": n, "kind": k, **kw}

    # ============================ SALES (14) ============================
    add("create_quotation", "Create quotation", "Draft a sale quotation for a customer.",
        "create", "sale.order", params=P(
            R("partner_id", "m2o:res.partner"), O("date_order", "date"),
            O("validity_date", "date"), O("pricelist_id", "m2o:product.pricelist"),
            O("payment_term_id", "m2o:account.payment.term"),
            O("user_id", "m2o:res.users"), O("client_order_ref"),
            O("note", "str")),
        title="Create quotation")
    add("send_quotation_email", "Send quotation email", "Email the quotation to the customer (card sends on approve).",
        "email", params=P(R("res_id", "int"), O("to"), O("subject"), O("body")),
        email_model="sale.order")
    add("set_order_pricelist", "Set order pricelist", "Change the pricelist on a draft quotation.",
        "write", "sale.order", params=P(R("res_id", "int"), R("pricelist_id", "m2o:product.pricelist")),
        states={"field": "state", "allow": ["draft", "sent"]})
    add("set_order_discount", "Set order discount", "Set the same discount % on every line of a draft order.",
        "custom", "sale.order", params=P(R("res_id", "int"), R("discount", "float")),
        states={"field": "state", "allow": ["draft", "sent"]})
    add("duplicate_quotation", "Duplicate quotation", "Copy a quotation as a new draft.",
        "copy", "sale.order", params=P(R("res_id", "int")))
    add("set_order_commitment_date", "Set commitment date", "Set the promised delivery date on an order.",
        "write", "sale.order", params=P(R("res_id", "int"), R("commitment_date", "datetime")),
        states={"field": "state", "allow": ["draft", "sent", "sale"]})
    add("set_order_salesperson", "Set salesperson", "Assign the salesperson on an order.",
        "write", "sale.order", params=P(R("res_id", "int"), R("user_id", "m2o:res.users")),
        states={"field": "state", "allow": ["draft", "sent"]})
    add("add_order_note", "Add order note", "Post an internal note on a sale order.",
        "note", "sale.order", params=P(R("res_id", "int"), R("body")))
    add("check_order_delivery", "Check order delivery", "Read-only: delivery pickings and their states for an order.",
        "read", "sale.order", params=P(R("res_id", "int")))
    add("set_order_payment_terms", "Set payment terms", "Change payment terms on a draft quotation.",
        "write", "sale.order", params=P(R("res_id", "int"), R("payment_term_id", "m2o:account.payment.term")),
        states={"field": "state", "allow": ["draft", "sent"]})
    add("set_order_client_ref", "Set customer reference", "Set the customer's PO number on an order.",
        "write", "sale.order", params=P(R("res_id", "int"), R("client_order_ref")),
        states={"field": "state", "allow": ["draft", "sent", "sale"]})
    add("reopen_quotation", "Reopen quotation", "Reset a cancelled order back to draft.",
        "method", "sale.order", method="action_draft", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["cancel"]})
    add("archive_quotation", "Archive quotation", "Archive a quotation (hide, keep history).",
        "write", "sale.order", params=P(R("res_id", "int")),
        fixed={"active": False})

    # =========================== PURCHASE (12) ============================
    add("create_rfq", "Create RFQ", "Draft a request for quotation to a vendor.",
        "create", "purchase.order", params=P(
            R("partner_id", "m2o:res.partner"), O("date_planned", "datetime"),
            O("payment_term_id", "m2o:account.payment.term"),
            O("user_id", "m2o:res.users"), O("notes")),
        title="Create RFQ")
    add("add_po_line", "Add PO line", "Add a product line to a draft RFQ.",
        "create", "purchase.order.line", params=P(
            R("order_id", "m2o:purchase.order"), R("product_id", "m2o:product.product"),
            O("product_qty", "float"), O("price_unit", "float"),
            O("date_planned", "datetime"), O("name")),
        title="Add PO line")
    add("send_rfq_email", "Send RFQ email", "Email the RFQ to the vendor (card sends on approve).",
        "email", params=P(R("res_id", "int"), O("to"), O("subject"), O("body")),
        email_model="purchase.order")
    add("create_vendor", "Create vendor", "Create a vendor with contact, VAT and address; refuses duplicates.",
        "create", "res.partner", params=P(
            R("name"), O("email"), O("phone"), O("vat"), O("street"),
            O("city"), O("zip"), O("country_id", "m2o:res.country"),
            O("comment")),
        fixed={"supplier_rank": 1, "company_type": "company"},
        dupe="partner", title="Create vendor")
    add("set_vendor_price", "Set vendor price", "Set a vendor pricelist line for a product.",
        "create", "product.supplierinfo", params=P(
            R("partner_id", "m2o:res.partner"),
            R("product_tmpl_id", "m2o:product.template"),
            O("price", "float"), O("min_qty", "float"), O("delay", "int")),
        title="Set vendor price")
    add("approve_purchase_order", "Approve purchase order", "Manager-approve a PO waiting approval.",
        "method", "purchase.order", method="button_approve", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["to approve"]})
    add("set_po_date_planned", "Set PO date planned", "Change the expected arrival date on a draft RFQ.",
        "write", "purchase.order", params=P(R("res_id", "int"), R("date_planned", "datetime")),
        states={"field": "state", "allow": ["draft", "sent"]})
    add("check_po_receipt", "Check PO receipt", "Read-only: receipts and bills linked to a purchase order.",
        "read", "purchase.order", params=P(R("res_id", "int")))
    add("bill_purchase_order", "Bill purchase order", "Draft a vendor bill from PO lines (qty billed minus qty invoiced).",
        "custom", "purchase.order", params=P(R("res_id", "int"), O("date", "date")))
    add("set_po_payment_terms", "Set PO payment terms", "Change payment terms on a draft RFQ.",
        "write", "purchase.order", params=P(R("res_id", "int"), R("payment_term_id", "m2o:account.payment.term")),
        states={"field": "state", "allow": ["draft", "sent"]})
    add("duplicate_rfq", "Duplicate RFQ", "Copy an RFQ as a new draft.",
        "copy", "purchase.order", params=P(R("res_id", "int")))
    add("set_po_buyer", "Set PO buyer", "Assign the buyer on a purchase order.",
        "write", "purchase.order", params=P(R("res_id", "int"), R("user_id", "m2o:res.users")),
        states={"field": "state", "allow": ["draft", "sent"]})

    # ============================= STOCK (12) =============================
    add("create_internal_transfer", "Create internal transfer", "Draft an internal move between two locations with lines.",
        "custom", "stock.picking", params=P(
            R("location_id", "m2o:stock.location"),
            R("location_dest_id", "m2o:stock.location"),
            O("scheduled_date", "datetime"), O("origin"),
            O("lines", "lines")))
    add("validate_receipt", "Validate receipt", "Validate an assigned incoming receipt.",
        "method", "stock.picking", method="button_validate", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["assigned", "confirmed"]})
    add("scrap_product", "Scrap move quantity", "Scrap the done quantity of a stock move (v20 scraps moves, not products).",
        "custom", "stock.move", params=P(
            R("move_id", "m2o:stock.move"), O("quantity", "float")))
    add("adjust_quantity", "Adjust quantity", "Set the counted quantity for a product in a location.",
        "custom", "stock.quant", params=P(
            R("product_id", "m2o:product.product"),
            R("location_id", "m2o:stock.location"), R("quantity", "float")))
    add("create_lot", "Create lot", "Create a lot/serial number for a product.",
        "create", "stock.lot", params=P(
            R("product_id", "m2o:product.product"), R("name"),
            O("ref")), title="Create lot")
    add("set_move_quantity_done", "Set move quantity done", "Set the done quantity on a stock move.",
        "write", "stock.move", params=P(R("res_id", "int"), R("quantity", "float")))
    add("check_availability", "Check availability", "Read-only: free quantity of a product per location.",
        "read", "stock.quant", params=P(R("product_id", "m2o:product.product")))
    add("set_picking_date", "Set picking date", "Change the scheduled date on a picking.",
        "write", "stock.picking", params=P(R("res_id", "int"), R("scheduled_date", "datetime")),
        states={"field": "state", "allow": ["draft", "confirmed", "assigned"]})
    add("assign_picking", "Reserve picking", "Reserve stock for a confirmed picking.",
        "method", "stock.picking", method="action_assign", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["confirmed"]})
    add("unreserve_picking", "Unreserve picking", "Release the reservation on a picking.",
        "method", "stock.picking", method="do_unreserve", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["assigned", "partially_available"]})
    add("create_delivery_order", "Create delivery order", "Draft an outgoing delivery with lines.",
        "custom", "stock.picking", params=P(
            R("partner_id", "m2o:res.partner"),
            R("location_id", "m2o:stock.location"),
            R("location_dest_id", "m2o:stock.location"),
            O("scheduled_date", "datetime"), O("origin"), O("lines", "lines")),
        picking_type="outgoing")
    add("check_reorder", "Check reorder", "Read-only: reordering rules and suggested quantities for a product.",
        "read", "stock.warehouse.orderpoint", params=P(R("product_id", "m2o:product.product")))

    # ============================ MRP / BOM (12) ==========================
    add("create_bom", "Create BOM", "Create a bill of materials with component lines.",
        "custom", "mrp.bom", params=P(
            R("product_tmpl_id", "m2o:product.template"),
            O("product_qty", "float"), O("type", "sel:bom_type"),
            O("lines", "lines")))
    add("add_bom_line", "Add BOM line", "Add a component line to a BOM.",
        "create", "mrp.bom.line", params=P(
            R("bom_id", "m2o:mrp.bom"), R("product_id", "m2o:product.product"),
            O("product_qty", "float")),
        title="Add BOM line")
    add("create_mo", "Create manufacturing order", "Create an MO for a product and quantity (BOM auto-picked when unique).",
        "custom", "mrp.production", params=P(
            R("product_id", "m2o:product.product"), R("product_qty", "float"),
            O("bom_id", "m2o:mrp.bom"), O("date_start", "datetime")))
    add("confirm_mo", "Confirm MO", "Confirm a draft manufacturing order.",
        "method", "mrp.production", method="action_confirm", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["draft"]})
    add("start_mo", "Start MO", "Reserve components for a confirmed manufacturing order.",
        "method", "mrp.production", method="action_assign", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["confirmed"]})
    add("finish_mo", "Finish MO", "Mark a manufacturing order as done.",
        "method", "mrp.production", method="button_mark_done", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["confirmed", "progress", "to_close"]})
    add("cancel_mo", "Cancel MO", "Cancel a manufacturing order.",
        "method", "mrp.production", method="action_cancel", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["draft", "confirmed", "progress", "to_close"]})
    add("create_bom_multilevel", "Create multilevel BOM", "End-to-end: create a parent BOM plus nested component BOMs level by level.",
        "custom", "mrp.bom", params=P(R("tree", "tree")),
        desc_detail="tree: {product_tmpl_id, product_qty?, lines: [{product_id, product_qty, components: [...]}, ...]}. "
                   "Components that are themselves manufactured get their own BOM created first, bottom-up. "
                   "Every level queues inside one approval card; nothing is created before Approve.")
    add("duplicate_bom", "Duplicate BOM", "Copy a BOM for another product.",
        "copy", "mrp.bom", params=P(R("res_id", "int")))
    add("set_mo_quantity", "Set MO quantity", "Change the quantity to produce on a draft/confirmed MO.",
        "write", "mrp.production", params=P(R("res_id", "int"), R("product_qty", "float")),
        states={"field": "state", "allow": ["draft", "confirmed"]})
    add("create_unbuild", "Create unbuild order", "Unbuild a quantity of a product back into components.",
        "custom", "mrp.unbuild", params=P(
            R("product_id", "m2o:product.product"), R("product_qty", "float"),
            R("location_id", "m2o:stock.location"),
            O("location_dest_id", "m2o:stock.location")))
    add("check_mo_availability", "Check MO availability", "Read-only: component reservation state of a manufacturing order.",
        "read", "mrp.production", params=P(R("res_id", "int")))

    # =========================== ACCOUNTING (12) ==========================
    add("create_vendor_bill", "Create vendor bill", "Draft a vendor bill with lines (purchase journal auto-picked when omitted).",
        "custom", "account.move", params=P(
            R("partner_id", "m2o:res.partner"),
            O("invoice_date", "date"), O("ref"), O("journal_id", "m2o:account.journal"),
            O("lines", "movelines")),
        fixed={"move_type": "in_invoice"})
    add("create_customer_invoice", "Create customer invoice", "Draft a customer invoice with lines.",
        "create", "account.move", params=P(
            R("partner_id", "m2o:res.partner"),
            O("invoice_date", "date"), O("ref"), O("journal_id", "m2o:account.journal"),
            O("lines", "movelines")),
        fixed={"move_type": "out_invoice"}, title="Create customer invoice")
    add("send_invoice_email", "Send invoice email", "Email a posted invoice to the customer (card sends on approve).",
        "email", params=P(R("res_id", "int"), O("to"), O("subject"), O("body")),
        email_model="account.move")
    add("set_invoice_payment_terms", "Set invoice payment terms", "Change payment terms on a draft invoice/bill.",
        "write", "account.move", params=P(R("res_id", "int"), R("invoice_payment_term_id", "m2o:account.payment.term")),
        states={"field": "state", "allow": ["draft"]})
    add("set_invoice_due_date", "Set invoice due date", "Change the due date on a draft invoice/bill.",
        "write", "account.move", params=P(R("res_id", "int"), R("invoice_date_due", "date")),
        states={"field": "state", "allow": ["draft"]})
    add("create_misc_entry", "Create misc entry", "Draft a balanced miscellaneous journal entry (debits must equal credits).",
        "custom", "account.move", params=P(
            R("journal_id", "m2o:account.journal"), R("date", "date"),
            R("lines", "movelines"), O("ref")))
    add("check_invoice_payments", "Check invoice payments", "Read-only: payments, residual and state of an invoice/bill.",
        "read", "account.move", params=P(R("res_id", "int")))
    add("set_journal_on_draft", "Set journal on draft", "Move a draft entry to another journal.",
        "write", "account.move", params=P(R("res_id", "int"), R("journal_id", "m2o:account.journal")),
        states={"field": "state", "allow": ["draft"]})
    add("duplicate_invoice", "Duplicate invoice", "Copy a draft invoice/bill.",
        "copy", "account.move", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["draft"]})
    add("set_invoice_reference", "Set invoice reference", "Set the vendor/customer reference on an entry.",
        "write", "account.move", params=P(R("res_id", "int"), R("ref")))
    add("create_bank_account", "Create bank account", "Add a bank account number to a partner (refuses duplicates).",
        "custom", "res.partner.bank", params=P(
            R("partner_id", "m2o:res.partner"), R("acc_number"),
            O("bank_id", "m2o:res.bank")))
    add("reset_move_to_draft", "Reset entry to draft", "Reset a posted entry back to draft (frozen periods still refuse).",
        "method", "account.move", method="button_draft", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["posted", "cancel"]})

    # ========================= PROJECT / TASKS (10) =======================
    add("create_project", "Create project", "Create a project with customer and manager.",
        "create", "project.project", params=P(
            R("name"), O("partner_id", "m2o:res.partner"),
            O("user_id", "m2o:res.users"), O("date_start", "date"),
            O("description")),
        title="Create project")
    add("create_task", "Create task", "Create a task in a project with assignees and deadline.",
        "create", "project.task", params=P(
            R("project_id", "m2o:project.project"), R("name"),
            O("user_ids", "m2m:res.users"), O("date_deadline", "datetime"),
            O("description"), O("priority", "sel:priority")),
        title="Create task")
    add("assign_task", "Assign task", "Set assignees on a task.",
        "write", "project.task", params=P(R("res_id", "int"), R("user_ids", "m2m:res.users")))
    add("move_task_stage", "Move task stage", "Move a task to a stage of its own project.",
        "custom", "project.task", params=P(R("res_id", "int"), R("stage_id", "m2o:project.task.type")))
    add("create_subtask", "Create subtask", "Create a subtask under a parent task.",
        "create", "project.task", params=P(
            R("parent_id", "m2o:project.task"), R("name"),
            O("user_ids", "m2m:res.users"), O("date_deadline", "datetime")),
        title="Create subtask")
    add("set_task_deadline", "Set task deadline", "Change the deadline on a task.",
        "write", "project.task", params=P(R("res_id", "int"), R("date_deadline", "datetime")))
    add("close_task", "Close task", "Move a task to its project's final stage.",
        "custom", "project.task", params=P(R("res_id", "int")))
    add("add_task_follower", "Add task follower", "Subscribe partners to a task's chatter.",
        "custom", "project.task", params=P(R("res_id", "int"), R("partner_ids", "m2m:res.partner")))
    add("create_milestone", "Create milestone", "Create a project milestone with a deadline.",
        "create", "project.milestone", params=P(
            R("project_id", "m2o:project.project"), R("name"),
            O("deadline", "date")),
        title="Create milestone")
    add("set_task_priority", "Set task priority", "Set the star priority on a task.",
        "write", "project.task", params=P(R("res_id", "int"), R("priority", "sel:priority")))

    # =============================== CRM (8) ============================
    add("create_lead", "Create lead", "Create a lead/opportunity with contact details; refuses duplicates.",
        "create", "crm.lead", params=P(
            R("name"), O("contact_name"), O("email_from"), O("phone"),
            O("expected_revenue", "float"), O("team_id", "m2o:crm.team"),
            O("user_id", "m2o:res.users"), O("description")),
        dupe="lead", title="Create lead")
    add("qualify_lead", "Qualify lead", "Convert a lead into an opportunity with probability.",
        "write", "crm.lead", params=P(R("res_id", "int"), O("probability", "float")),
        fixed={"type": "opportunity"})
    add("mark_won", "Mark won", "Mark an opportunity as won.",
        "method", "crm.lead", method="action_set_won", params=P(R("res_id", "int")))
    add("mark_lost", "Mark lost", "Mark an opportunity as lost with an optional reason.",
        "method", "crm.lead", method="action_set_lost", params=P(
            R("res_id", "int"), O("lost_reason_id", "m2o:crm.lost.reason")))
    add("schedule_lead_meeting", "Schedule lead meeting", "Create a calendar meeting linked to a lead.",
        "custom", "calendar.event", params=P(
            R("lead_id", "m2o:crm.lead"), R("name"), R("start", "datetime"),
            O("stop", "datetime"), O("description")))
    add("log_lead_call", "Log lead call", "Log a call note on a lead plus a follow-up activity.",
        "custom", "crm.lead", params=P(
            R("res_id", "int"), R("body"), O("followup"), O("date_deadline", "date")))
    add("set_lead_stage", "Set lead stage", "Move a lead to another stage.",
        "write", "crm.lead", params=P(R("res_id", "int"), R("stage_id", "m2o:crm.stage")))
    add("reassign_lead", "Reassign lead", "Assign a lead to a salesperson/team.",
        "write", "crm.lead", params=P(R("res_id", "int"), O("user_id", "m2o:res.users"), O("team_id", "m2o:crm.team")))

    # ========================= HR / EXPENSE / LEAVE (8) =================
    add("create_expense", "Create expense", "Create an expense line for an employee.",
        "create", "hr.expense", params=P(
            R("name"), R("employee_id", "m2o:hr.employee"),
            O("unit_amount", "float"), O("product_id", "m2o:product.product"),
            O("date", "date")),
        title="Create expense")
    add("submit_expense", "Submit expense", "Submit a draft expense for approval.",
        "method", "hr.expense", method="action_submit", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["draft"]})
    add("approve_expense", "Approve expense", "Approve a submitted expense.",
        "method", "hr.expense", method="action_approve", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["submitted"]})
    add("create_leave", "Create leave request", "Create and confirm a time-off request.",
        "custom", "hr.leave", params=P(
            R("employee_id", "m2o:hr.employee"),
            R("holiday_status_id", "m2o:hr.leave.type"),
            R("date_from", "datetime"), R("date_to", "datetime"),
            O("notes")))
    add("approve_leave", "Approve leave", "Approve a confirmed leave request.",
        "method", "hr.leave", method="action_approve", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["confirm"]})
    add("refuse_leave", "Refuse leave", "Refuse a leave request.",
        "method", "hr.leave", method="action_refuse", params=P(R("res_id", "int")),
        states={"field": "state", "allow": ["confirm"]})
    add("create_applicant", "Create applicant", "Create a job applicant.",
        "create", "hr.applicant", params=P(
            R("name"), O("partner_name"), O("email_from"), O("phone"),
            O("job_id", "m2o:hr.job"), O("description")),
        title="Create applicant")
    add("set_applicant_stage", "Set applicant stage", "Move an applicant to another recruitment stage.",
        "write", "hr.applicant", params=P(R("res_id", "int"), R("stage_id", "m2o:hr.recruitment.stage")))

    # ============================== COMMS (12) ==========================
    add("send_email_record", "Send email", "Send an email about any record (card sends on approve).",
        "email", params=P(R("model"), R("res_id", "int"), R("to"), R("subject"), R("body")))
    add("schedule_meeting_event", "Schedule meeting", "Create a calendar meeting with attendees.",
        "create", "calendar.event", params=P(
            R("name"), R("start", "datetime"), O("stop", "datetime"),
            O("partner_ids", "m2m:res.partner"), O("description"),
            O("location")),
        title="Schedule meeting")
    add("create_reminder_activity", "Remind me", "Schedule a to-do activity for yourself on a record.",
        "activity", params=P(R("model"), R("res_id", "int"), R("summary"), O("date_deadline", "date")))
    add("post_channel_message", "Post in channel", "Post a message in a Discuss channel.",
        "custom", "discuss.channel", params=P(R("channel_id", "m2o:discuss.channel"), R("body")))
    add("mark_activity_done", "Mark activity done", "Close an activity with feedback.",
        "method", "mail.activity", method="action_feedback", params=P(R("res_id", "int"), O("feedback")))
    add("notify_record_followers", "Notify followers", "Post a note that notifies a record's followers.",
        "custom", params=P(R("model"), R("res_id", "int"), R("body")))
    add("create_todo_activity", "Create to-do", "Create a dated to-do activity for someone on a record.",
        "activity", params=P(R("model"), R("res_id", "int"), R("summary"),
                             R("user_id", "m2o:res.users"), O("date_deadline", "date"),
                             O("note")))
    add("archive_any_record", "Archive record", "Archive any record (hide, keep history).",
        "write", None, params=P(R("model"), R("res_id", "int")),
        fixed={"active": False}, any_model=True)
    add("duplicate_any_record", "Duplicate record", "Copy any record as a new draft.",
        "copy", None, params=P(R("model"), R("res_id", "int")), any_model=True)
    add("create_child_contact", "Create contact", "Create a contact person under a company.",
        "create", "res.partner", params=P(
            R("parent_id", "m2o:res.partner"), R("name"),
            O("email"), O("phone"), O("function")),
        fixed={"type": "contact"}, title="Create contact")
    add("add_event_attendees", "Add event attendees", "Add attendees to a calendar meeting.",
        "custom", "calendar.event", params=P(R("res_id", "int"), R("partner_ids", "m2m:res.partner")))
    add("set_event_location", "Set event location", "Set the location of a calendar meeting.",
        "write", "calendar.event", params=P(R("res_id", "int"), R("location")))

    # ========================= VENDOR / PARTNER (8) =====================
    add("create_vendor_full", "Create vendor (full)", "Create a vendor with VAT, address, terms and bank; refuses duplicates.",
        "custom", "res.partner", params=P(
            R("name"), O("email"), O("phone"), O("vat"), O("street"),
            O("city"), O("zip"), O("country_id", "m2o:res.country"),
            O("payment_term_id", "m2o:account.payment.term"),
            O("bank_acc"), O("comment")))
    add("validate_vendor_profile", "Validate vendor profile", "Read-only: report what a vendor profile is missing (VAT, email, bank, terms).",
        "read", "res.partner", params=P(R("res_id", "int")))
    add("create_customer_full", "Create customer (full)", "Create a customer with address, terms and pricelist; refuses duplicates.",
        "custom", "res.partner", params=P(
            R("name"), O("email"), O("phone"), O("vat"), O("street"),
            O("city"), O("zip"), O("country_id", "m2o:res.country"),
            O("payment_term_id", "m2o:account.payment.term"),
            O("pricelist_id", "m2o:product.pricelist"), O("comment")))
    add("add_partner_bank", "Add partner bank", "Add a bank account number to a partner (refuses duplicates).",
        "custom", "res.partner.bank", params=P(
            R("partner_id", "m2o:res.partner"), R("acc_number"),
            O("bank_id", "m2o:res.bank")))
    add("categorize_partner", "Categorize partner", "Set contact tags on a partner.",
        "write", "res.partner", params=P(R("res_id", "int"), R("category_id", "m2m:res.partner.category")))
    add("set_partner_payment_terms", "Set partner payment terms", "Set purchase/sales payment terms on a partner.",
        "custom", "res.partner", params=P(
            R("res_id", "int"),
            O("property_supplier_payment_term_id", "m2o:account.payment.term"),
            O("property_payment_term_id", "m2o:account.payment.term")))
    add("block_partner", "Block partner", "Archive a partner (hide everywhere, keep history).",
        "write", "res.partner", params=P(R("res_id", "int")),
        fixed={"active": False})
    add("create_vendor_bill_draft", "Draft bill for vendor", "Draft an empty vendor bill shell for a vendor (lines added later).",
        "create", "account.move", params=P(R("partner_id", "m2o:res.partner"), O("ref")),
        fixed={"move_type": "in_invoice"}, title="Draft vendor bill")

    # ============================= PRODUCT (6) ==========================
    add("create_product_full", "Create product", "Create a storable/service product with price, cost, UoM and taxes; refuses duplicates.",
        "create", "product.template", params=P(
            R("name"), O("type", "sel:categ_type"), O("list_price", "float"),
            O("standard_price", "float"), O("uom_id", "m2o:uom.uom"),
            O("uom_po_id", "m2o:uom.uom"), O("categ_id", "m2o:product.category"),
            O("default_code"), O("taxes_id", "m2m:account.tax"),
            O("sale_ok", "bool"), O("purchase_ok", "bool")),
        dupe="product", title="Create product")
    add("set_product_price", "Set product price", "Change the sales price of a product.",
        "write", "product.template", params=P(R("res_id", "int"), R("list_price", "float")))
    add("set_product_cost", "Set product cost", "Change the cost of a product.",
        "write", "product.template", params=P(R("res_id", "int"), R("standard_price", "float")))
    add("set_product_flags", "Set product flags", "Toggle can-be-sold / can-be-purchased on a product.",
        "write", "product.template", params=P(
            R("res_id", "int"), O("sale_ok", "bool"), O("purchase_ok", "bool")))
    add("set_product_category", "Set product category", "Move a product to another category.",
        "write", "product.template", params=P(R("res_id", "int"), R("categ_id", "m2o:product.category")))
    add("set_product_supplier", "Set product supplier", "Set the main vendor price for a product.",
        "create", "product.supplierinfo", params=P(
            R("product_tmpl_id", "m2o:product.template"),
            R("partner_id", "m2o:res.partner"),
            O("price", "float"), O("min_qty", "float"), O("delay", "int")),
        title="Set product supplier")

    return S


_SPECS = None


def specs():
    global _SPECS
    if _SPECS is None:
        built = _specs()
        for spec in built:
            names = {p["name"] for p in spec.get("params") or []}
            # Batch ids are a documented input on every method tool.
            if spec.get("op") == "method" and "res_ids" not in names:
                spec["params"] = list(spec.get("params") or []) + [{
                    "name": "res_ids", "kind": "ids",
                    "desc": "Batch: several record ids at once."}]
            # force=true skips duplicate guards; declared, not a backdoor.
            if spec.get("dupe") and "force" not in names:
                spec["params"] = list(spec.get("params") or []) + [{
                    "name": "force", "kind": "bool",
                    "desc": "Skip the duplicate guard (you checked)."}]
        _SPECS = {s["code"]: s for s in built}
    return _SPECS


# ------------------------------------------------------------------
# schemas (tool records + model validation)
# ------------------------------------------------------------------
def schema_for(spec):
    props, required = {}, []
    for param in spec.get("params") or []:
        name, kind = param["name"], param.get("kind", "str")
        base = kind.split(":")[0]
        if base in ("int",):
            prop = {"type": "integer"}
        elif base in ("float",):
            prop = {"type": "number"}
        elif base in ("bool",):
            prop = {"type": "boolean"}
        elif base in ("ids",):
            prop = {"type": "array", "items": {"type": "integer"}}
        elif base in ("lines", "movelines", "tree"):
            prop = {"type": "array", "items": {"type": "object"}}
        elif base in ("m2m",):
            prop = {"type": "array", "items": {"type": "integer"}}
        else:
            prop = {"type": "string"}
        if param.get("desc"):
            prop["description"] = param["desc"]
        if kind.startswith("m2o:"):
            prop["description"] = (prop.get("description", "")
                                    + " (id or name)").strip()
        props[name] = prop
        if param.get("req"):
            required.append(name)
    out = {"type": "object", "properties": props}
    if required:
        out["required"] = required
    return out


# ------------------------------------------------------------------
# runner — queue time (validates, never writes)
# ------------------------------------------------------------------
def _helpers():
    from odoo.addons.nova_ai.models.nova_ai_tools import (
        _chain_args, _chain_target, _check_settable, _coerce_create_value,
        _coerce_field_value, _confirm_card, _denied, _gone_or_denied,
        _missing_required, _model_allowed, _preview_value,
        _refuse_exact_dupe, _refuse_lead_dupe, _side_effect_allowed,
    )
    return {
        "chain_args": _chain_args, "chain_target": _chain_target,
        "check_settable": _check_settable,
        "coerce_create": _coerce_create_value,
        "coerce_field": _coerce_field_value,
        "confirm_card": _confirm_card, "denied": _denied,
        "gone": _gone_or_denied, "missing": _missing_required,
        "allowed": _model_allowed, "preview": _preview_value,
        "dupe_lead": _refuse_lead_dupe, "dupe_exact": _refuse_exact_dupe,
        "side_effect": _side_effect_allowed,
    }


def _need_model(env, H, model):
    """Gate a model: installed + allowed. Enterprise-only models on a
    Community database deny cleanly instead of KeyErroring."""
    if not model:
        raise AccessError(env._("A model is required."))
    try:
        env[model]
    except KeyError:
        raise AccessError(env._(
            "%(model)s is not installed here.") % {"model": model})
    if not H["allowed"](env, model):
        raise H["denied"](model, env)
    return model


def _record(env, H, model, res_id, args):
    _need_model(env, H, model)
    try:
        rid = int(res_id or 0)
    except (TypeError, ValueError):
        rid = 0
    rec = env[model].browse(rid).exists() if rid else None
    if rec:
        return rec
    H["chain_args"](args or {})
    raise AccessError(H["gone"](env, model, res_id))


def _guard_state(env, H, spec, record):
    states = spec.get("states")
    if not states or record is None:
        return
    field = states.get("field", "state")
    allowed = states.get("allow") or []
    try:
        current = record[field]
        current = getattr(current, "id", current)
        current = str(current or "")
    except Exception:
        return
    if current not in [str(a) for a in allowed]:
        raise AccessError(env._(
            "%(tool)s needs state %(allow)s, but %(record)s is "
            "%(state)s.") % {
                "tool": spec["name"], "allow": "/".join(
                    str(a) for a in allowed),
                "record": record.display_name, "state": current or "—"})


def _m2m_command(env, model, field_name, raw):
    """ids (or names resolved by name_search) -> [(6, 0, ids)].

    Never silently drops: an unresolvable entry refuses loudly,
    because a [(6, 0, [])] would WIPE the field.
    """
    from odoo.addons.nova_ai.models.nova_ai_tools import (
        _resolve_many2one_name)
    try:
        comodel = env[model]._fields[field_name].comodel_name
    except KeyError:
        raise AccessError(env._("No field %(field)s on %(model)s.") % {
            "field": field_name, "model": model})
    ids = []
    for item in (raw or []):
        try:
            num = int(item)
        except (TypeError, ValueError):
            num = 0
        if num:
            if num not in ids:
                ids.append(num)
            continue
        if isinstance(item, str) and item.strip():
            target = _resolve_many2one_name(
                env, comodel, item.strip())
            if target and target.id not in ids:
                ids.append(target.id)
            continue
        raise AccessError(env._(
            "Cannot resolve %(item)r for %(field)s — pass ids or "
            "exact names.") % {"item": item, "field": field_name})
    if not ids:
        raise AccessError(env._(
            "No valid entries for %(field)s.") % {"field": field_name})
    return [(6, 0, ids)]


def _coerce_activity_user(env, H, raw):
    """Activity assignee: id stays, name resolves, garbage refuses."""
    if raw in (None, "", 0, "0"):
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        pass
    if isinstance(raw, str) and raw.strip():
        found = env["res.users"].name_search(raw.strip(), limit=2)
        if len(found) == 1:
            return found[0][0]
    raise AccessError(env._(
        "No user %(raw)r for the activity.") % {"raw": raw})


def run_biz(env, args, ctx, spec):
    """Queue a business action card. Dispatches on spec op."""
    H = _helpers()
    op = spec.get("op")
    model = spec.get("model") or (args.get("model") or "")
    if op in ("create", "write", "method", "copy", "note", "activity",
              "read", "custom") and not model and op != "custom":
        # any-model ops carry their model in args (validated below).
        pass
    if op == "read":
        return _run_read(env, args, ctx, spec, H)
    if op == "custom":
        return _run_custom(env, args, ctx, spec, H)
    if not model:
        raise AccessError(env._("%(tool)s needs a model.") % {
            "tool": spec["name"]})
    # The gate covers spec models AND any-model ops (model from args).
    if not H["allowed"](env, model):
        raise H["denied"](model, env)
    if op == "create":
        return _queue_create(env, args, ctx, spec, H, model)
    if op == "write":
        return _queue_write(env, args, ctx, spec, H, model)
    if op == "method":
        return _queue_method(env, args, ctx, spec, H, model)
    if op == "copy":
        return _queue_copy(env, args, ctx, spec, H, model)
    if op == "note":
        if not H["allowed"](env, model):
            raise H["denied"](model, env)
        record = _record(env, H, model, args.get("res_id"), args)
        if not hasattr(record, "message_post"):
            raise AccessError(env._(
                "%(model)s has no chatter for notes.") % {"model": model})
        _guard_state(env, H, spec, record)
        return H["confirm_card"](
            spec["code"], env._("%(tool)s on %(record)s") % {
                "tool": spec["name"], "record": record.display_name},
            (args.get("body") or "")[:120],
            {"biz": spec["code"], "op": "note", "model": model,
             "res_id": record.id, "body": args.get("body") or "",
             "expect_state": (spec.get("states") or {}).get("allow") or []})
    if op == "activity":
        if not H["allowed"](env, model):
            raise H["denied"](model, env)
        record = _record(env, H, model, args.get("res_id"), args)
        if not hasattr(record, "activity_schedule"):
            raise AccessError(env._(
                "%(model)s has no activities.") % {"model": model})
        return H["confirm_card"](
            spec["code"], env._("%(tool)s on %(record)s") % {
                "tool": spec["name"], "record": record.display_name},
            (args.get("summary") or "")[:120],
        {"biz": spec["code"], "op": "activity", "model": model,
         "res_id": record.id,
         "summary": args.get("summary") or "Follow up",
         "date": args.get("date_deadline") or args.get("date") or "",
         "user_id": _coerce_activity_user(env, H, args.get("user_id")),
         "note": args.get("note") or "",
         "expect_state": (spec.get("states") or {}).get("allow") or []})
    if op == "email":
        return _queue_email(env, args, ctx, spec, H)
    raise AccessError(env._("Tool %(tool)s is not runnable.") % {
        "tool": spec["name"]})


def _values_from_args(env, H, model, args, spec, for_create=True):
    """Coerce declared params into ORM values (unknown params refuse)."""
    values, dropped = {}, []
    for param in spec.get("params") or []:
        name = param["name"]
        if name in ("res_id", "model"):
            continue
        if name not in args or args.get(name) in (None, ""):
            continue
        kind = (param.get("kind") or "str").split(":")[0]
        raw = args.get(name)
        if kind == "m2m":
            values[name] = _m2m_command(env, model, name, raw)
            continue
        if kind in ("lines", "movelines"):
            # Lists of value dicts become coerced (0, 0, vals) commands:
            # every sub-field validates NOW (bad account/product fails
            # at queue), not at approve.
            if not isinstance(raw, list) or not all(
                    isinstance(item, dict) for item in raw):
                raise AccessError(env._(
                    "%(tool)s needs %(field)s as a list of objects.") % {
                        "tool": spec["name"], "field": name})
            try:
                sub_model = env[model]._fields[name].comodel_name
            except KeyError:
                raise AccessError(env._(
                    "No field %(field)s on %(model)s.") % {
                        "field": name, "model": model})
            coerced_lines = []
            for item in raw:
                inner = {}
                for sub_key, sub_val in item.items():
                    try:
                        sub_field = env[sub_model]._fields[sub_key]
                    except KeyError:
                        raise AccessError(env._(
                            "No field %(field)s on %(model)s.") % {
                                "field": sub_key, "model": sub_model})
                    inner[sub_key] = H["coerce_create"](
                        env, sub_model, sub_field, sub_val)
                coerced_lines.append((0, 0, inner))
            values[name] = coerced_lines
            continue
        try:
            field = env[model]._fields[name]
        except KeyError:
            dropped.append(name)
            continue
        if for_create:
            values[name] = H["coerce_create"](env, model, field, raw)
        else:
            _check = H["check_settable"](env, model, name)
            coerced, _display = H["coerce_field"](
                env, _check, raw, None)
            values[name] = coerced
    # fixed values (supplier_rank, move_type, active...) always apply.
    for name, val in (spec.get("fixed") or {}).items():
        values[name] = val
    # field renames (quantity -> quantity_done).
    for src, dst in (spec.get("field_map") or {}).items():
        if src in values:
            values[dst] = values.pop(src)
    if dropped:
        raise AccessError(env._(
            "%(tool)s does not use %(fields)s.") % {
                "tool": spec["name"], "fields": ", ".join(dropped[:4])})
    # required params (res_id/model excluded from values, checked before).
    missing = [p["name"] for p in (spec.get("params") or [])
               if p.get("req") and p["name"] not in ("res_id", "model")
               and p["name"] not in values
               and p["name"] not in (spec.get("fixed") or {})]
    if missing:
        # Missing ORM-required fields are reported by name; a value the
        # caller never sent is not the same as a value Odoo defaults.
        extra = H["missing"](env, model, values)
        raise AccessError(env._(
            "%(tool)s still needs %(fields)s%(extra)s.") % {
                "tool": spec["name"], "fields": ", ".join(missing[:6]),
                "extra": (" (also: %s)" % ", ".join(extra[:4]))
                if extra else ""})
    return values


def _queue_create(env, args, ctx, spec, H, model):
    values = _values_from_args(env, H, model, args, spec, True)
    if spec.get("dupe") == "lead" and not (args.get("force") is True):
        H["dupe_lead"](env, values, ctx)
    if spec.get("dupe") == "partner" and not (args.get("force") is True):
        H["dupe_exact"](env, "res.partner", values, ctx)
    if spec.get("dupe") == "product" and not (args.get("force") is True):
        H["dupe_exact"](env, "product.template", values, ctx)
    missing = H["missing"](env, model, values)
    title = spec.get("title") or spec["name"]
    card = H["confirm_card"](
        spec["code"], title,
        env._("%d field(s) prepared") % len(values),
        {"biz": spec["code"], "op": "create", "model": model,
         "values": values})
    preview = [H["preview"](env, model, key, value)
               for key, value in list(values.items())[:8]]
    if missing:
        preview.append(env._("Still missing required: %s")
                       % ", ".join(missing[:6]))
    card["preview"] = preview
    return card


def _queue_write(env, args, ctx, spec, H, model):
    record = _record(env, H, model, args.get("res_id"), args)
    _guard_state(env, H, spec, record)
    values = _values_from_args(env, H, model, args, spec, False)
    for key in values:
        if key not in env[model]._fields:
            # fixed{} values (active, type, ...) must exist on the
            # model — archive_any_record on account.move refuses here,
            # naming the model instead of dying at approve.
            raise AccessError(env._(
                "%(tool)s cannot set %(field)s on %(model)s.") % {
                    "tool": spec["name"], "field": key,
                    "model": model})
    if not values:
        raise AccessError(env._("%(tool)s changes nothing.") % {
            "tool": spec["name"]})
    card = H["confirm_card"](
        spec["code"], env._("%(tool)s on %(record)s") % {
            "tool": spec["name"], "record": record.display_name},
        ", ".join("%s" % key for key in list(values)[:6]),
        {"biz": spec["code"], "op": "write", "model": model,
         "res_id": record.id, "values": values,
         "expect_state": (spec.get("states") or {}).get("allow") or []})
    card["preview"] = [H["preview"](env, model, key, value)
                       for key, value in list(values.items())[:8]]
    return card


def _queue_method(env, args, ctx, spec, H, model):
    ids = args.get("res_ids") or [args.get("res_id")]
    clean = []
    for raw in ids:
        try:
            num = int(raw or 0)
        except (TypeError, ValueError):
            continue
        if num and num not in clean:
            clean.append(num)
    if not clean:
        raise AccessError(H["gone"](env, model, args.get("res_id")))
    if not H["side_effect"](env, model, spec["method"]):
        raise AccessError(env._(
            "%(tool)s is not enabled by the deployer.") % {
                "tool": spec["name"]})
    records = env[model].browse(clean).exists()
    if len(records) != len(clean):
        raise AccessError(H["gone"](env, model, args.get("res_id")))
    for record in records:
        _guard_state(env, H, spec, record)
    margs = {}
    for param in spec.get("params") or []:
        if param["name"] in ("res_id", "res_ids", "model"):
            continue
        if param["name"] in args and args[param["name"]] not in (None, ""):
            # Method args that name real fields coerce like writes
            # (ids stay ids, names resolve, selections validate);
            # anything else travels verbatim (e.g. feedback text).
            # A bad value for a real field refuses here, not on Approve.
            if param["name"] not in env[model]._fields:
                margs[param["name"]] = args[param["name"]]
            else:
                _check = H["check_settable"](env, model, param["name"])
                coerced, _display = H["coerce_field"](
                    env, _check, args[param["name"]], None)
                margs[param["name"]] = coerced
    card = H["confirm_card"](
        spec["code"], env._("%(tool)s: %(records)s") % {
            "tool": spec["name"],
            "records": ", ".join(records.mapped("display_name"))[:120]},
        spec.get("method", ""),
        {"biz": spec["code"], "op": "method", "model": model,
         "res_ids": clean, "method": spec["method"],
         "method_args": margs,
         "expect_state": (spec.get("states") or {}).get("allow") or []})
    return card


def _queue_copy(env, args, ctx, spec, H, model):
    record = _record(env, H, model, args.get("res_id"), args)
    _guard_state(env, H, spec, record)
    card = H["confirm_card"](
        spec["code"], env._("%(tool)s %(record)s") % {
            "tool": spec["name"], "record": record.display_name},
        env._("A new draft copy; sequences never carry over."),
        {"biz": spec["code"], "op": "copy", "model": model,
         "res_id": record.id,
         "expect_state": (spec.get("states") or {}).get("allow") or []})
    return card


def _queue_email(env, args, ctx, spec, H):
    model = spec.get("email_model") or args.get("model") or ""
    if not model:
        raise AccessError(env._("%(tool)s needs a model.") % {
            "tool": spec["name"]})
    if not H["allowed"](env, model):
        raise H["denied"](model, env)
    to = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body = args.get("body") or ""
    res_id = 0
    if args.get("res_id"):
        record = _record(env, H, model, args.get("res_id"), args)
        res_id = record.id
        if not to and "email" in record._fields:
            try:
                to = (record.email or "").strip()
            except Exception:
                to = ""
        if not subject:
            subject = record.display_name or spec["name"]
    if not to or not subject:
        raise AccessError(env._(
            "%(tool)s needs a recipient and a subject.") % {
                "tool": spec["name"]})
    return H["confirm_card"](
        spec["code"], env._("%(tool)s: %(subject)s") % {
            "tool": spec["name"], "subject": subject[:80]},
        env._("To %s") % to,
        {"biz": spec["code"], "op": "email", "model": model,
         "res_id": res_id, "to": to, "subject": subject, "body": body})


# ------------------------------------------------------------------
# custom + read runners (per-tool logic too specific for the table)
# ------------------------------------------------------------------
def _lines_arg(args, key="lines"):
    lines = args.get(key) or []
    if not isinstance(lines, list):
        raise AccessError(_("Lines must be a list of objects."))
    return [dict(item) for item in lines if isinstance(item, dict)]


def _run_custom(env, args, ctx, spec, H):
    code = spec["code"]
    fn = _CUSTOM.get(code)
    if not fn:
        raise AccessError(env._("Tool %(tool)s is not runnable.") % {
            "tool": spec["name"]})
    _need_model(env, H, spec.get("model") or args.get("model") or "")
    return fn(env, args, ctx, spec, H)


def _run_read(env, args, ctx, spec, H):
    code = spec["code"]
    fn = _READS.get(code)
    if not fn:
        raise AccessError(env._("Tool %(tool)s is not runnable.") % {
            "tool": spec["name"]})
    _need_model(env, H, spec.get("model") or "")
    return fn(env, args, ctx, spec, H)


def _c_set_order_discount(env, args, ctx, spec, H):
    record = _record(env, H, "sale.order", args.get("res_id"), args)
    _guard_state(env, H, spec, record)
    try:
        pct = float(args.get("discount"))
    except (TypeError, ValueError):
        raise AccessError(env._("A discount number is required."))
    if not 0 <= pct <= 100:
        raise AccessError(env._("Discount must be 0-100."))
    lines = record.order_line.filtered(lambda line: not line.display_type)
    return H["confirm_card"](
        spec["code"], env._("Set %(pct)s%% on %(order)s (%(n)s lines)") % {
            "pct": pct, "order": record.display_name, "n": len(lines)},
        ", ".join(lines.mapped("product_id.display_name") or [])[:160],
        {"biz": spec["code"], "op": "custom:order_discount",
         "model": "sale.order", "res_id": record.id, "discount": pct,
         "expect_state": ["draft", "sent"]})


def _c_bill_purchase_order(env, args, ctx, spec, H):
    record = _record(env, H, "purchase.order", args.get("res_id"), args)
    if record.state not in ("purchase", "done"):
        raise AccessError(env._(
            "Bill %(order)s only when it is confirmed (now %(state)s).") % {
                "order": record.display_name, "state": record.state})
    if not record.order_line:
        raise AccessError(env._("That order has no lines to bill."))
    return H["confirm_card"](
        spec["code"], env._("Bill %(order)s") % {
            "order": record.display_name},
        env._("%(n)s line(s) from %(partner)s") % {
            "n": len(record.order_line),
            "partner": record.partner_id.display_name or "—"},
        {"biz": spec["code"], "op": "custom:bill_po",
         "model": "purchase.order", "res_id": record.id,
         "date": args.get("date") or ""})


def _c_transfer(env, args, ctx, spec, H, picking_type, default_locs):
    lines = _lines_arg(args)
    if not lines:
        raise AccessError(env._("Add at least one line "
                                "{product_id, quantity}."))
    partner = None
    if picking_type == "outgoing":
        if not args.get("partner_id"):
            raise AccessError(env._("A delivery needs a customer."))
        partner = _record(env, H, "res.partner",
                          args.get("partner_id"), args)
    # Source and destination always resolve now (outgoing used to
    # skip the source): store ids, never raw input.
    src = _record(env, H, "stock.location",
                  args.get("location_id"), args)
    dest = _record(env, H, "stock.location",
                   args.get("location_dest_id"), args)
    coerced = []
    for item in lines:
        try:
            product = env["product.product"].browse(
                int(item.get("product_id") or 0)).exists()
        except (TypeError, ValueError):
            product = None
        if not product:
            raise AccessError(env._(
                "No product with id %(raw)s.") % {
                    "raw": item.get("product_id")})
        try:
            qty = float(item.get("quantity")
                        or item.get("product_uom_qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            raise AccessError(env._(
                "Quantity must be positive for %(product)s.") % {
                    "product": product.display_name})
        coerced.append({"product_id": product.id,
                        "product_uom_qty": qty,
                        "quantity": qty})
    return H["confirm_card"](
        spec["code"], spec["name"],
        env._("%(n)s line(s)") % {"n": len(coerced)},
        {"biz": spec["code"], "op": "custom:transfer",
         "picking_type": picking_type,
         "location_id": src.id,
         "location_dest_id": dest.id,
         "partner_id": partner.id if partner else 0,
         "scheduled_date": args.get("scheduled_date") or "",
         "origin": args.get("origin") or "",
         "lines": coerced})


def _c_create_transfer(env, args, ctx, spec, H):
    return _c_transfer(env, args, ctx, spec, H, "internal", None)


def _c_create_delivery(env, args, ctx, spec, H):
    return _c_transfer(env, args, ctx, spec, H, "outgoing", None)


def _c_scrap(env, args, ctx, spec, H):
    # v20 scraps stock moves, not products: set the done quantity,
    # then action_scrap moves it to the scrap location.
    move = _record(env, H, "stock.move", args.get("move_id"), args)
    qty = None
    if args.get("quantity") not in (None, ""):
        try:
            qty = float(args.get("quantity"))
        except (TypeError, ValueError):
            raise AccessError(env._("Quantity must be a number."))
        if qty <= 0:
            raise AccessError(env._("Quantity must be positive."))
    return H["confirm_card"](
        spec["code"], env._("Scrap %(move)s") % {
            "move": move.display_name},
        env._("%s × %s") % (
            qty if qty is not None else move.quantity,
            move.product_id.display_name),
        {"biz": spec["code"], "op": "custom:scrap",
         "res_id": move.id,
         "quantity": qty,
         "expect_state": ["draft", "confirmed", "assigned",
                          "partially_available"]})


def _c_adjust(env, args, ctx, spec, H):
    product = _record(env, H, "product.product",
                      args.get("product_id"), args)
    location = _record(env, H, "stock.location",
                       args.get("location_id"), args)
    try:
        qty = float(args.get("quantity"))
    except (TypeError, ValueError):
        raise AccessError(env._("A counted quantity is required."))
    return H["confirm_card"](
        spec["code"], env._("Count %(qty)s × %(product)s at %(loc)s") % {
            "qty": qty, "product": product.display_name,
            "loc": location.display_name},
        env._("Applies as the new on-hand quantity."),
        {"biz": spec["code"], "op": "custom:adjust",
         "product_id": product.id, "location_id": location.id,
         "quantity": qty})


def _c_bank_account(env, args, ctx, spec, H):
    partner = _record(env, H, "res.partner",
                      args.get("partner_id"), args)
    acc = re.sub(r"\s+", "", (args.get("acc_number") or "").strip())
    if not acc:
        raise AccessError(env._("A bank account number is required."))
    dupe = env["res.partner.bank"].search([
        ("partner_id", "=", partner.id),
        ("acc_number", "in", [acc, args.get("acc_number")])], limit=1)
    if dupe and not (args.get("force") is True):
        raise AccessError(env._(
            "%(partner)s already has account %(acc)s.") % {
                "partner": partner.display_name, "acc": acc})
    bank_id = 0
    if args.get("bank_id"):
        bank = _record(env, H, "res.bank", args.get("bank_id"), args)
        bank_id = bank.id
    return H["confirm_card"](
        spec["code"], "%s: %s" % (spec["name"], partner.display_name),
        acc,
        {"biz": spec["code"], "op": "custom:bank_account",
         "partner_id": partner.id, "acc_number": acc,
         "bank_id": bank_id})


def _c_vendor_bill(env, args, ctx, spec, H):
    # A bill without a journal posts nowhere: auto-pick the first
    # purchase journal, refuse loudly when the CoA has none.
    if not args.get("journal_id"):
        journal = env["account.journal"].search(
            [("type", "=", "purchase")], limit=1)
        if not journal:
            raise AccessError(env._(
                "No purchase journal exists — create one first."))
        args = dict(args, journal_id=journal.id)
    create_spec = dict(spec, op="create")
    return _queue_create(env, args, ctx, create_spec, H,
                         "account.move")


def _c_create_bom(env, args, ctx, spec, H):
    try:
        tmpl = env["product.template"].browse(
            int(args.get("product_tmpl_id") or 0)).exists()
    except (TypeError, ValueError):
        tmpl = None
    if not tmpl:
        raise AccessError(H["gone"](
            env, "product.template", args.get("product_tmpl_id")))
    if args.get("product_qty") not in (None, ""):
        try:
            qty = float(args["product_qty"])
        except (TypeError, ValueError):
            raise AccessError(env._(
                "Quantity must be a number, got %(raw)r.") % {
                    "raw": args.get("product_qty")})
        if qty <= 0:
            raise AccessError(env._("Quantity must be positive."))
    else:
        qty = 1
    lines = _lines_arg(args)
    coerced = []
    for item in lines:
        try:
            product = env["product.product"].browse(
                int(item.get("product_id") or 0)).exists()
        except (TypeError, ValueError):
            product = None
        if not product:
            raise AccessError(env._(
                "No product with id %(raw)s.") % {
                    "raw": item.get("product_id")})
        try:
            lqty = float(item.get("product_qty")
                        or item.get("quantity") or 0) or 0
        except (TypeError, ValueError):
            lqty = 0
        if lqty <= 0:
            raise AccessError(env._(
                "Quantity must be positive for %(product)s.") % {
                    "product": product.display_name})
        coerced.append({"product_id": product.id, "product_qty": lqty})
    btype = (args.get("type") or "normal").strip()
    if btype not in ("normal", "phantom"):
        raise AccessError(env._("BOM type is normal or phantom."))
    return H["confirm_card"](
        spec["code"], env._("Create BOM for %(product)s") % {
            "product": tmpl.display_name},
        env._("%(n)s component(s) × %(qty)s") % {
            "n": len(coerced), "qty": qty},
        {"biz": spec["code"], "op": "custom:bom",
         "product_tmpl_id": tmpl.id, "product_qty": qty,
         "type": btype, "lines": coerced})


def _bom_tree_create(env, H, node, level=0):
    """Recursive bottom-up BOM creation. Returns (bom_id, describe)."""
    if level > 6:
        raise AccessError(env._("BOM tree is deeper than 6 levels."))
    try:
        tmpl = env["product.template"].browse(
            int(node.get("product_tmpl_id")
                or node.get("product_template_id") or 0)).exists()
    except (TypeError, ValueError):
        tmpl = None
    if not tmpl:
        raise AccessError(env._("A tree node names no product template."))
    if node.get("product_qty") not in (None, ""):
        try:
            qty = float(node["product_qty"])
        except (TypeError, ValueError):
            raise AccessError(env._(
                "Quantity must be a number, got %(raw)r.") % {
                    "raw": node.get("product_qty")})
        if qty <= 0:
            raise AccessError(env._("Quantity must be positive."))
    else:
        qty = 1
    line_cmds = []
    made = []
    for item in (node.get("lines") or node.get("components") or []):
        if not isinstance(item, dict):
            continue
        subs = item.get("components") or item.get("lines")
        if subs:
            # Manufactured component: resolve its template directly,
            # or through the given variant — then its own BOM first,
            # bottom-up.
            sub_tmpl = None
            if item.get("product_tmpl_id"):
                try:
                    sub_tmpl = env["product.template"].browse(
                        int(item["product_tmpl_id"])).exists() or None
                except (TypeError, ValueError):
                    sub_tmpl = None
            elif item.get("product_id"):
                try:
                    _variant = env["product.product"].browse(
                        int(item["product_id"])).exists()
                    sub_tmpl = _variant.product_tmpl_id if _variant \
                        else None
                except (TypeError, ValueError):
                    sub_tmpl = None
            if not sub_tmpl:
                raise AccessError(env._(
                    "Component %r names no valid template.") % (
                        item.get("product_tmpl_id")
                        or item.get("product_id")))
            sub_bom_id, sub_desc = _bom_tree_create(
                env, H, {"product_tmpl_id": sub_tmpl.id,
                         "product_qty": item.get("product_qty") or 1,
                         "lines": subs}, level + 1)
            made.append(sub_desc)
            variant = env["product.product"].search(
                [("product_tmpl_id", "=", sub_tmpl.id)], limit=1)
            if not variant:
                raise AccessError(env._(
                    "Template %(t)s has no variant to consume.") % {
                        "t": sub_tmpl.display_name})
            try:
                lqty = float(item.get("product_qty")
                             or item.get("quantity") or 1) or 1
            except (TypeError, ValueError):
                lqty = 1
            line_cmds.append((0, 0, {"product_id": variant.id,
                                     "product_qty": lqty}))
        else:
            try:
                product = env["product.product"].browse(
                    int(item.get("product_id") or 0)).exists()
            except (TypeError, ValueError):
                product = None
            if not product:
                raise AccessError(env._(
                    "No product with id %(raw)s.") % {
                        "raw": item.get("product_id")})
            try:
                lqty = float(item.get("product_qty")
                             or item.get("quantity") or 0) or 0
            except (TypeError, ValueError):
                lqty = 0
            if lqty <= 0:
                raise AccessError(env._(
                    "Quantity must be positive for %(product)s.") % {
                        "product": product.display_name})
            line_cmds.append((0, 0, {"product_id": product.id,
                                     "product_qty": lqty}))
    bom = env["mrp.bom"].create({
        "product_tmpl_id": tmpl.id, "product_qty": qty,
        "type": "normal", "bom_line_ids": line_cmds})
    describe = "%s ×%s (%d lines)" % (tmpl.display_name, qty,
                                      len(line_cmds))
    return bom.id, [describe] + made


def _c_bom_tree(env, args, ctx, spec, H):
    tree = args.get("tree") or {}
    if not isinstance(tree, dict) or not tree:
        raise AccessError(env._(
            "create_bom_multilevel needs a tree {product_tmpl_id, "
            "lines: [{product_id, product_qty, components?}]}."))
    # Dry-run the shape now (no writes): unknown products refuse here,
    # and the approval applies the identical walk.
    problems = []

    # Dry-run the FULL shape now (no writes): unknown templates,
    # products and bad quantities refuse here — not after Approve.
    def _walk(node, level=0):
        if level > 6:
            problems.append("deeper than 6 levels")
            return
        try:
            tmpl = env["product.template"].browse(
                int(node.get("product_tmpl_id")
                    or node.get("product_template_id") or 0)).exists()
        except (TypeError, ValueError):
            tmpl = None
        if node is tree and not tmpl:
            problems.append("the root names no product template")
        for item in (node.get("lines") or node.get("components")
                     or []):
            if not isinstance(item, dict):
                problems.append("a tree line is not an object")
                continue
            try:
                qty = float(item.get("product_qty")
                            or item.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                problems.append("a line has no positive quantity")
            subs = item.get("components") or item.get("lines")
            if subs:
                # A manufactured component resolves its template
                # directly, or through the variant's own template.
                sub_t = None
                if item.get("product_tmpl_id"):
                    try:
                        sub_t = env["product.template"].browse(
                            int(item["product_tmpl_id"])).exists()
                    except (TypeError, ValueError):
                        sub_t = None
                elif item.get("product_id"):
                    try:
                        variant = env["product.product"].browse(
                            int(item["product_id"])).exists()
                        sub_t = variant.product_tmpl_id if variant \
                            else None
                    except (TypeError, ValueError):
                        sub_t = None
                if not sub_t:
                    problems.append(
                        "a manufactured component %r has no valid "
                        "template" % (item.get("product_tmpl_id")
                                      or item.get("product_id")))
                _walk({"product_tmpl_id": (
                    sub_t.id if sub_t else None),
                    "lines": subs}, level + 1)
            elif not item.get("product_id"):
                problems.append("a component line names no product")

    _walk(tree)
    if problems:
        raise AccessError(env._(
            "That tree does not validate: %s.") % "; ".join(
                problems[:5]))
    return H["confirm_card"](
        spec["code"], env._("Create multilevel BOM"),
        env._("Bottom-up: components first, parent last. "
               "Nothing is created before Approve."),
        {"biz": spec["code"], "op": "custom:bom_tree", "tree": tree})


def _c_create_mo(env, args, ctx, spec, H):
    product = _record(env, H, "product.product",
                      args.get("product_id"), args)
    try:
        qty = float(args.get("product_qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        raise AccessError(env._("Quantity to produce must be positive."))
    bom_id = 0
    if args.get("bom_id"):
        bom = _record(env, H, "mrp.bom", args.get("bom_id"), args)
        bom_id = bom.id
    return H["confirm_card"](
        spec["code"], env._("Produce %(qty)s × %(product)s") % {
            "qty": qty, "product": product.display_name},
        env._("BOM auto-picked when unique.") if not bom_id else "",
        {"biz": spec["code"], "op": "custom:mo",
         "product_id": product.id, "product_qty": qty,
         "bom_id": bom_id,
         "date_start": args.get("date_start") or ""})


def _c_unbuild(env, args, ctx, spec, H):
    product = _record(env, H, "product.product",
                      args.get("product_id"), args)
    location = _record(env, H, "stock.location",
                       args.get("location_id"), args)
    dest = None
    if args.get("location_dest_id"):
        dest = _record(env, H, "stock.location",
                       args.get("location_dest_id"), args)
    try:
        qty = float(args.get("product_qty") or 0)
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        raise AccessError(env._("Quantity must be positive."))
    return H["confirm_card"](
        spec["code"], env._("Unbuild %(qty)s × %(product)s") % {
            "qty": qty, "product": product.display_name},
        location.display_name,
        {"biz": spec["code"], "op": "custom:unbuild",
         "product_id": product.id, "product_qty": qty,
         "location_id": location.id,
         "location_dest_id": dest.id if dest else 0})


def _c_misc_entry(env, args, ctx, spec, H):
    journal = _record(env, H, "account.journal",
                      args.get("journal_id"), args)
    lines = args.get("lines") or []
    if not isinstance(lines, list) or len(lines) < 2:
        raise AccessError(env._(
            "A journal entry needs at least a debit and a credit line "
            "{account_id, debit?, credit?, name?}."))
    debit = credit = 0.0
    coerced = []
    for item in lines:
        if not isinstance(item, dict):
            continue
        try:
            account = env["account.account"].browse(
                int(item.get("account_id") or 0)).exists()
        except (TypeError, ValueError):
            account = None
        if not account:
            raise AccessError(env._(
                "No account with id %(raw)s.") % {
                    "raw": item.get("account_id")})
        try:
            dr = float(item.get("debit") or 0)
            cr = float(item.get("credit") or 0)
        except (TypeError, ValueError):
            raise AccessError(env._("Debits and credits are numbers."))
        if (dr and cr) or (not dr and not cr):
            raise AccessError(env._(
                "Each line is either a debit or a credit."))
        debit += dr
        credit += cr
        coerced.append({"account_id": account.id, "debit": dr,
                        "credit": cr, "name": item.get("name") or "/"})
    if abs(debit - credit) > 0.005:
        raise AccessError(env._(
            "Out of balance: debit %(dr)s vs credit %(cr)s.") % {
                "dr": debit, "cr": credit})
    return H["confirm_card"](
        spec["code"], env._("Draft %(journal)s entry %(dr)s") % {
            "journal": journal.display_name, "dr": debit},
        env._("%(n)s line(s), balanced.") % {"n": len(coerced)},
        {"biz": spec["code"], "op": "custom:misc",
         "journal_id": journal.id, "date": args.get("date") or "",
         "ref": args.get("ref") or "", "lines": coerced})


def _c_leave(env, args, ctx, spec, H):
    employee = _record(env, H, "hr.employee",
                       args.get("employee_id"), args)
    ltype = _record(env, H, "hr.leave.type",
                    args.get("holiday_status_id"), args)
    # datetimes are validated now (approve requires them), not later.
    # A bare date means the whole day: 00:00 start, 23:59 end.
    normed = {}
    for key, default_time in (("date_from", "00:00"),
                              ("date_to", "23:59")):
        text = (args.get(key) or "").strip()
        if len(text) < 10:
            raise AccessError(env._(
                "A leave needs %(key)s as YYYY-MM-DD [HH:MM].") % {
                    "key": key})
        if len(text) == 10:
            text = "%s %s" % (text, default_time)
        try:
            _odoo_fields.Date.from_string(text[:10])
            _odoo_fields.Datetime.from_string(text[:16])
        except ValueError:
            raise AccessError(env._(
                "Bad date %(val)r: use YYYY-MM-DD [HH:MM].") % {
                    "val": text[:16]})
        normed[key] = text[:16]
    args = dict(args, **normed)
    return H["confirm_card"](
        spec["code"], env._("Leave for %(employee)s") % {
            "employee": employee.display_name},
        "%s -> %s" % (args.get("date_from") or "?",
                       args.get("date_to") or "?"),
        {"biz": spec["code"], "op": "custom:leave",
         "employee_id": employee.id,
         "holiday_status_id": ltype.id,
         "date_from": args.get("date_from") or "",
         "date_to": args.get("date_to") or "",
         "notes": args.get("notes") or ""})


def _c_vendor_full(env, args, ctx, spec, H, customer=False):
    name = (args.get("name") or "").strip()
    if not name:
        raise AccessError(env._("A name is required."))
    values = {"name": name}
    for key in ("email", "phone", "vat", "street", "city", "zip",
                "comment", "ref"):
        if args.get(key) not in (None, ""):
            values[key] = args.get(key)
    if args.get("country_id"):
        country = _record(env, H, "res.country",
                          args.get("country_id"), args)
        values["country_id"] = country.id
    values["company_type"] = "company"
    if customer:
        values["customer_rank"] = 1
        if args.get("payment_term_id"):
            term = _record(env, H, "account.payment.term",
                           args.get("payment_term_id"), args)
            values["property_payment_term_id"] = term.id
        if args.get("pricelist_id"):
            pl = _record(env, H, "product.pricelist",
                         args.get("pricelist_id"), args)
            values["property_product_pricelist"] = pl.id
        if not (args.get("force") is True):
            H["dupe_exact"](env, "res.partner", values, ctx)
    else:
        values["supplier_rank"] = 1
        if args.get("payment_term_id"):
            term = _record(env, H, "account.payment.term",
                           args.get("payment_term_id"), args)
            values["property_supplier_payment_term_id"] = term.id
        if not (args.get("force") is True):
            H["dupe_exact"](env, "res.partner", values, ctx)
    bank = (args.get("bank_acc") or "").strip()
    card = H["confirm_card"](
        spec["code"], spec["name"] + ": " + name,
        env._("VAT %(vat)s") % {"vat": values.get("vat") or "—"},
        {"biz": spec["code"],
         "op": "custom:vendor_full" if not customer
         else "custom:customer_full",
         "values": values, "bank_acc": bank})
    card["preview"] = [H["preview"](env, "res.partner", key, value)
                       for key, value in list(values.items())[:8]]
    return card


def _c_create_vendor_full(env, args, ctx, spec, H):
    return _c_vendor_full(env, args, ctx, spec, H, customer=False)


def _c_create_customer_full(env, args, ctx, spec, H):
    return _c_vendor_full(env, args, ctx, spec, H, customer=True)


def _c_channel_post(env, args, ctx, spec, H):
    channel = _record(env, H, "discuss.channel",
                      args.get("channel_id"), args)
    body = (args.get("body") or "").strip()
    if not body:
        raise AccessError(env._("A message body is required."))
    return H["confirm_card"](
        spec["code"], env._("Post in %(channel)s") % {
            "channel": channel.display_name},
        body[:120],
        {"biz": spec["code"], "op": "custom:channel",
         "channel_id": channel.id, "body": body})


def _c_notify_followers(env, args, ctx, spec, H):
    model = args.get("model") or ""
    if not H["allowed"](env, model):
        raise H["denied"](model, env)
    record = _record(env, H, model, args.get("res_id"), args)
    if not hasattr(record, "message_post"):
        raise AccessError(env._(
            "%(model)s has no chatter to notify.") % {"model": model})
    body = (args.get("body") or "").strip()
    if not body:
        raise AccessError(env._("A message body is required."))
    return H["confirm_card"](
        spec["code"], env._("Notify followers of %(record)s") % {
            "record": record.display_name},
        body[:120],
        {"biz": spec["code"], "op": "custom:notify",
         "model": model, "res_id": record.id, "body": body})


def _c_close_task(env, args, ctx, spec, H):
    record = _record(env, H, "project.task", args.get("res_id"), args)
    stages = env["project.task.type"].search(
        [("project_ids", "in", [record.project_id.id])]
        if record.project_id else [], order="sequence desc", limit=1)
    if not stages:
        stages = env["project.task.type"].search(
            [], order="sequence desc", limit=1)
    if not stages:
        raise AccessError(env._("No task stage exists to close into."))
    return H["confirm_card"](
        spec["code"], env._("Close %(task)s") % {
            "task": record.display_name},
        stages.display_name,
        {"biz": spec["code"], "op": "custom:close_task",
         "res_id": record.id, "stage_id": stages.id})


def _c_move_stage(env, args, ctx, spec, H):
    record = _record(env, H, "project.task", args.get("res_id"), args)
    stage = _record(env, H, "project.task.type",
                    args.get("stage_id"), args)
    if record.project_id and stage.project_ids and \
            record.project_id not in stage.project_ids:
        raise AccessError(env._(
            "%(stage)s does not belong to %(project)s.") % {
                "stage": stage.display_name,
                "project": record.project_id.display_name})
    return H["confirm_card"](
        spec["code"], env._("Move %(task)s to %(stage)s") % {
            "task": record.display_name, "stage": stage.display_name},
        "",
        {"biz": spec["code"], "op": "custom:move_task_stage",
         "model": "project.task",
         "res_id": record.id, "stage_id": stage.id})


def _c_subscribe(env, args, ctx, spec, H):
    record = _record(env, H, "project.task", args.get("res_id"), args)
    ids = []
    for item in (args.get("partner_ids") or []):
        try:
            num = int(item)
        except (TypeError, ValueError):
            raise AccessError(env._(
                "Cannot resolve follower %(item)r — pass partner ids.") % {
                    "item": item})
        if not num or num in ids:
            raise AccessError(env._(
                "Cannot resolve follower %(item)r.") % {"item": item})
        ids.append(num)
    if not ids:
        raise AccessError(env._("Pick at least one follower."))
    partners = env["res.partner"].browse(ids).exists()
    if len(partners) != len(ids):
        raise AccessError(env._("One of those partners does not exist."))
    return H["confirm_card"](
        spec["code"], env._("Follow %(task)s") % {
            "task": record.display_name},
        ", ".join(partners.mapped("display_name"))[:160],
        {"biz": spec["code"], "op": "custom:subscribe",
         "res_id": record.id, "partner_ids": ids})


def _c_event_attendees(env, args, ctx, spec, H):
    record = _record(env, H, "calendar.event", args.get("res_id"), args)
    ids = []
    for item in (args.get("partner_ids") or []):
        try:
            num = int(item)
        except (TypeError, ValueError):
            raise AccessError(env._(
                "Cannot resolve attendee %(item)r — pass partner ids."
            ) % {"item": item})
        if not num or num in ids:
            raise AccessError(env._(
                "Cannot resolve attendee %(item)r.") % {"item": item})
        ids.append(num)
    if not ids:
        raise AccessError(env._("Pick at least one attendee."))
    partners = env["res.partner"].browse(ids).exists()
    if len(partners) != len(ids):
        raise AccessError(env._("One of those partners does not exist."))
    return H["confirm_card"](
        spec["code"], env._("Invite to %(event)s") % {
            "event": record.display_name},
        env._("%(n)s attendee(s)") % {"n": len(ids)},
        {"biz": spec["code"], "op": "custom:attendees",
         "res_id": record.id, "partner_ids": ids})


def _c_partner_terms(env, args, ctx, spec, H):
    # Property fields bypass the stored-field fence: they live in
    # ir.property, write through the normal path, and read back here
    # so the card shows old -> new like every other write.
    record = _record(env, H, "res.partner", args.get("res_id"), args)
    values, shown = {}, []
    for key in ("property_supplier_payment_term_id",
                "property_payment_term_id"):
        if args.get(key) in (None, "", 0):
            continue
        term = _record(env, H, "account.payment.term",
                       args.get(key), args)
        values[key] = term.id
        try:
            old = record[key]
            old_name = old.display_name if old else "—"
        except Exception:
            old_name = "—"
        shown.append("%s: %s -> %s" % (key.replace(
            "property_", "").replace("_", " "),
            old_name, term.display_name))
    if not values:
        raise AccessError(env._("%(tool)s changes nothing.") % {
            "tool": spec["name"]})
    card = H["confirm_card"](
        spec["code"], env._("%(tool)s on %(record)s") % {
            "tool": spec["name"], "record": record.display_name},
        "; ".join(shown)[:160],
        {"biz": spec["code"], "op": "custom:partner_terms",
         "res_id": record.id, "values": values})
    card["preview"] = shown
    return card


def _c_lead_meeting(env, args, ctx, spec, H):
    lead = _record(env, H, "crm.lead", args.get("lead_id"), args)
    name = (args.get("name") or "").strip()
    if not name:
        raise AccessError(env._("A meeting subject is required."))
    partners = [lead.partner_id.id] if lead.partner_id else []
    return H["confirm_card"](
        spec["code"], env._("Meet about %(lead)s") % {
            "lead": lead.display_name},
        "%s @ %s" % (name[:80], args.get("start") or "?"),
        {"biz": spec["code"], "op": "custom:lead_meeting",
         "lead_id": lead.id, "name": name,
         "start": args.get("start") or "",
         "stop": args.get("stop") or "",
         "description": args.get("description") or "",
         "partner_ids": partners})


def _c_lead_call(env, args, ctx, spec, H):
    record = _record(env, H, "crm.lead", args.get("res_id"), args)
    body = (args.get("body") or "").strip()
    if not body:
        raise AccessError(env._("A call note is required."))
    return H["confirm_card"](
        spec["code"], env._("Log call on %(lead)s") % {
            "lead": record.display_name},
        body[:120],
        {"biz": spec["code"], "op": "custom:lead_call",
         "res_id": record.id, "body": body,
         "followup": args.get("followup") or "",
         "date_deadline": args.get("date_deadline") or ""})


_CUSTOM = {
    "set_order_discount": _c_set_order_discount,
    "bill_purchase_order": _c_bill_purchase_order,
    "create_internal_transfer": _c_create_transfer,
    "create_delivery_order": _c_create_delivery,
    "scrap_product": _c_scrap,
    "adjust_quantity": _c_adjust,
    "create_bom": _c_create_bom,
    "create_bom_multilevel": _c_bom_tree,
    "create_mo": _c_create_mo,
    "create_unbuild": _c_unbuild,
    "create_misc_entry": _c_misc_entry,
    "create_leave": _c_leave,
    "create_vendor_full": _c_create_vendor_full,
    "create_customer_full": _c_create_customer_full,
    "create_vendor_bill": _c_vendor_bill,
    "create_bank_account": _c_bank_account,
    "add_partner_bank": _c_bank_account,
    "post_channel_message": _c_channel_post,
    "notify_record_followers": _c_notify_followers,
    "close_task": _c_close_task,
    "move_task_stage": _c_move_stage,
    "add_task_follower": _c_subscribe,
    "add_event_attendees": _c_event_attendees,
    "schedule_lead_meeting": _c_lead_meeting,
    "log_lead_call": _c_lead_call,
    "set_partner_payment_terms": _c_partner_terms,
}


# ------------------------------- reads -------------------------------
def _r_order_delivery(env, args, ctx, spec, H):
    record = _record(env, H, "sale.order", args.get("res_id"), args)
    pickings = record.picking_ids
    return {"order": record.display_name, "state": record.state,
            "pickings": [{
                "name": p.name, "state": p.state,
                "scheduled": str(p.scheduled_date or ""),
                "origin": p.origin or ""} for p in pickings]}


def _r_po_receipt(env, args, ctx, spec, H):
    record = _record(env, H, "purchase.order", args.get("res_id"), args)
    # Draft orders are named "New" — matching bills on that string
    # would link unrelated bills.
    bills = env["account.move"].search([
        ("invoice_origin", "ilike", record.name or ""),
        ("move_type", "=", "in_invoice")], limit=10) \
        if record.name and record.name not in (False, "New") else []
    return {"order": record.display_name, "state": record.state,
            "pickings": [{
                "name": p.name, "state": p.state} for p in
                record.picking_ids],
            "bills": [{
                "name": b.name, "state": b.state,
                "amount": b.amount_total} for b in bills]}


def _r_availability(env, args, ctx, spec, H):
    product = _record(env, H, "product.product",
                      args.get("product_id"), args)
    quants = env["stock.quant"].search([
        ("product_id", "=", product.id),
        ("location_id.usage", "=", "internal")])
    rows = [{
        "location": q.location_id.display_name,
        "on_hand": q.quantity,
        "reserved": q.reserved_quantity,
        "free": q.quantity - q.reserved_quantity} for q in quants[:25]]
    return {"product": product.display_name,
            "free_total": sum(r["free"] for r in rows), "rows": rows}


def _r_reorder(env, args, ctx, spec, H):
    product = _record(env, H, "product.product",
                      args.get("product_id"), args)
    rules = env["stock.warehouse.orderpoint"].search(
        [("product_id", "=", product.id)], limit=10)
    return {"product": product.display_name, "rules": [{
        "location": r.location_id.display_name,
        "min": r.product_min_qty, "max": r.product_max_qty,
        "to_order": max(0.0, r.product_max_qty - r.product_min_qty)}
        for r in rules]}


def _r_mo_availability(env, args, ctx, spec, H):
    record = _record(env, H, "mrp.production", args.get("res_id"), args)
    return {"order": record.display_name, "state": record.state,
            "reservation": getattr(record, "reservation_state", "") or "",
            "product": record.product_id.display_name,
            "qty": record.product_qty}


def _r_invoice_payments(env, args, ctx, spec, H):
    record = _record(env, H, "account.move", args.get("res_id"), args)
    return {"move": record.display_name, "state": record.state,
            "amount_total": record.amount_total,
            "residual": record.amount_residual,
            "payment_state": record.payment_state}


def _r_vendor_profile(env, args, ctx, spec, H):
    record = _record(env, H, "res.partner", args.get("res_id"), args)
    missing = []
    if not (record.vat or "").strip():
        missing.append("VAT number")
    if not (record.email or "").strip():
        missing.append("email")
    if not record.bank_ids:
        missing.append("bank account")
    if not record.property_supplier_payment_term_id:
        missing.append("purchase payment terms")
    if not (record.street or "").strip():
        missing.append("street address")
    return {"partner": record.display_name,
            "supplier_rank": record.supplier_rank,
            "missing": missing,
            "complete": not missing}


_READS = {
    "check_order_delivery": _r_order_delivery,
    "check_po_receipt": _r_po_receipt,
    "check_availability": _r_availability,
    "check_reorder": _r_reorder,
    "check_mo_availability": _r_mo_availability,
    "check_invoice_payments": _r_invoice_payments,
    "validate_vendor_profile": _r_vendor_profile,
}


# ------------------------------------------------------------------
# approve — re-validates, then applies inside a savepoint
# ------------------------------------------------------------------
def run_confirm_biz(env, card, agent=None):
    """Apply an approved business card. Returns {model, res_id, ...}."""
    from odoo.addons.nova_ai.models.nova_ai_tools import (
        _gone_or_denied as _gone)
    H = _helpers()
    payload = card.get("payload") or {}
    spec = specs().get(payload.get("biz") or card.get("kind") or "")
    if not spec:
        raise AccessError(env._("That card is unknown."))
    # Agent scope re-check at approve time (yesterday's card, today's scope).
    try:
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            _scoped_env)
        env = _scoped_env(env, agent)
    except Exception:
        pass
    op = payload.get("op") or spec.get("op")
    model = payload.get("model") or spec.get("model") or ""
    # Installed + allowed, before anything browses: a module removed
    # since queue time denies cleanly instead of KeyErroring.
    if model:
        _need_model(env, H, model)
    # Approve-time scope recheck applies to every op, email included: a
    # yesterday-scoped card never applies outside today's subset.
    if op == "custom:lead_meeting":
        for gated in ("calendar.event", "crm.lead"):
            if not H["allowed"](env, gated):
                raise H["denied"](gated, env)
    elif model:
        if not H["allowed"](env, model):
            raise H["denied"](model, env)
    try:
        with env.cr.savepoint():
            return _apply_biz(env, H, spec, op, model, payload)
    except AccessError:
        raise
    except Exception as err:
        raise AccessError(env._("Cannot apply %(tool)s: %(err)s") % {
            "tool": spec["name"],
            "err": str(err).split("\n")[0][:200]})


def _state_now(record, field="state"):
    try:
        value = record[field]
        return str(getattr(value, "id", value) or "")
    except Exception:
        return ""


def _apply_biz(env, H, spec, op, model, payload):
    if op == "create":
        values = dict(payload.get("values") or {})
        missing = H["missing"](env, model, values)
        if missing:
            raise AccessError(env._(
                "Cannot create %(model)s: still needs %(fields)s.") % {
                    "model": model, "fields": ", ".join(missing)})
        record = env[model].create(values)
        return {"model": model, "res_id": record.id,
                "display_name": record.display_name}
    if op == "write":
        record = env[model].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(H["gone"](env, model,
                                        payload.get("res_id")))
        expect = payload.get("expect_state") or []
        if expect and _state_now(record) not in [
                str(s) for s in expect]:
            raise AccessError(env._(
                "%(record)s is now %(state)s — the card expected "
                "%(allow)s.") % {
                    "record": record.display_name,
                    "state": _state_now(record),
                    "allow": "/".join(str(s) for s in expect)})
        values = dict(payload.get("values") or {})
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            _move_frozen_refusal)
        frozen = _move_frozen_refusal(record, "edit")
        if frozen:
            raise AccessError(env._("Cannot edit %(name)s: %(reason)s.")
                              % {"name": record.display_name,
                                 "reason": frozen})
        clean = {}
        for name, value in values.items():
            if isinstance(value, list) and value and all(
                    isinstance(cmd, (list, tuple)) for cmd in value):
                # m2m command triples (6, 0, ids): record rules still
                # apply at write; _check_settable would refuse m2m,
                # so commands travel verbatim after an existence look.
                try:
                    ftype = env[model]._fields[name].type
                except KeyError:
                    raise AccessError(env._(
                        "No field %(field)s on %(model)s.") % {
                            "field": name, "model": model})
                if ftype != "many2many":
                    raise AccessError(env._(
                        "Field %(field)s cannot be set by the agent.") % {
                            "field": name})
                clean[name] = value
                continue
            field = H["check_settable"](env, model, name)
            coerced, _display = H["coerce_field"](
                env, field, value, record)
            clean[name] = coerced
        record.write(clean)
        return {"model": model, "res_id": record.id,
                "display_name": record.display_name}
    if op == "method":
        records = env[model].browse(
            payload.get("res_ids") or []).exists()
        if not records:
            raise AccessError(H["gone"](env, model, 0))
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            _move_frozen_refusal, _side_effect_allowed)
        if not _side_effect_allowed(env, model, payload.get("method")):
            raise AccessError(env._("That method is not enabled."))
        expect = payload.get("expect_state") or []
        for record in records:
            if expect and _state_now(record) not in [
                    str(s) for s in expect]:
                raise AccessError(env._(
                    "%(record)s is now %(state)s.") % {
                        "record": record.display_name,
                        "state": _state_now(record)})
            frozen = _move_frozen_refusal(record, "edit")
            # Only cancelling escapes the frozen-period fence (it
            # books nothing); resetting to draft on a locked period
            # stays refused, as reset_move_to_draft promises.
            if frozen and payload.get("method") != "button_cancel":
                raise AccessError(env._(
                    "Cannot change %(name)s: %(reason)s.") % {
                        "name": record.display_name, "reason": frozen})
        method = payload.get("method")
        margs = dict(payload.get("method_args") or {})
        for record in records:
            target = getattr(record, method, None)
            if not callable(target):
                raise AccessError(env._(
                    "Method %(method)s is not available.") % {
                        "method": method})
            target(**margs) if margs else target()
        first = records[0]
        return {"model": model, "res_id": first.id,
                "display_name": first.display_name}
    def _expect(record, what):
        allow = payload.get("expect_state") or []
        if allow and _state_now(record) not in [str(s) for s in allow]:
            raise AccessError(env._(
                "%(record)s is now %(state)s — the card expected "
                "%(allow)s, so %(what)s was skipped.") % {
                    "record": record.display_name,
                    "state": _state_now(record),
                    "allow": "/".join(str(s) for s in allow),
                    "what": what})

    if op == "copy":
        record = env[model].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(H["gone"](env, model,
                                        payload.get("res_id")))
        _expect(record, "the copy")
        from odoo.addons.nova_ai.models.nova_ai_tools import (
            _default_state_value)
        default_state = _default_state_value(env, model)
        twin = record.copy(
            {"state": default_state} if default_state else None)
        return {"model": model, "res_id": twin.id,
                "display_name": twin.display_name}
    if op == "note":
        record = env[model].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(H["gone"](env, model,
                                        payload.get("res_id")))
        _expect(record, "the note")
        record.message_post(body=payload.get("body") or "",
                            message_type="comment")
        return {"model": model, "res_id": record.id,
                "display_name": record.display_name}
    if op == "activity":
        record = env[model].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(H["gone"](env, model,
                                        payload.get("res_id")))
        _expect(record, "the activity")
        atype = env["mail.activity.type"].search([], limit=1)
        if not atype:
            raise AccessError(env._("No activity type configured."))
        user_id = payload.get("user_id") or env.user.id
        record.activity_schedule(
            False, summary=payload.get("summary") or "Follow up",
            date_deadline=payload.get("date") or False,
            user_id=int(user_id), note=payload.get("note") or "",
            activity_type_id=atype.id)
        return {"model": model, "res_id": record.id,
                "display_name": record.display_name}
    if op == "email":
        to = (payload.get("to") or "").strip()
        if payload.get("model") and payload.get("res_id"):
            # Re-resolve: the record may be gone and the address stale.
            target = env[payload["model"]].browse(
                payload["res_id"]).exists()
            if not target:
                raise AccessError(H["gone"](env, payload["model"],
                                            payload["res_id"]))
            if not to and "email" in target._fields:
                try:
                    to = (target.email or "").strip()
                except Exception:
                    to = ""
        if not to or not (payload.get("subject") or "").strip():
            raise AccessError(env._(
                "That email lost its recipient or subject."))
        mail = env["mail.mail"].create({
            "subject": payload.get("subject") or "",
            "body_html": payload.get("body") or "",
            "to": to,
        })
        mail.send()
        return {"model": "mail.mail", "res_id": mail.id,
                "display_name": payload.get("subject") or ""}
    if op and op.startswith("custom:"):
        return _apply_custom(env, H, spec, op, payload)
    raise AccessError(env._("That card cannot be applied."))


def _apply_custom(env, H, spec, op, payload):
    name = op.split(":", 1)[1]
    model = payload.get("model") or spec.get("model") or ""
    if name == "order_discount":
        record = env["sale.order"].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(H["gone"](env, "sale.order",
                                        payload.get("res_id")))
        if record.state not in ("draft", "sent"):
            raise AccessError(env._("That order is %s.") % record.state)
        pct = float(payload.get("discount") or 0)
        record.order_line.filtered(
            lambda line: not line.display_type).write(
                {"discount": pct})
        return {"model": "sale.order", "res_id": record.id,
                "display_name": record.display_name}
    if name == "bill_po":
        order = env["purchase.order"].browse(
            payload.get("res_id")).exists()
        if not order or order.state not in ("purchase", "done"):
            raise AccessError(env._("That order cannot be billed now."))
        lines = []
        for line in order.order_line:
            to_bill = (line.product_qty or 0) - (line.qty_invoiced or 0)
            if to_bill <= 0:
                continue
            lines.append((0, 0, {
                "product_id": line.product_id.id,
                "name": line.name or line.product_id.display_name,
                "quantity": to_bill,
                "price_unit": line.price_unit,
                "product_uom_id": (line.product_uom.id
                                   if line.product_uom else False),
                "purchase_line_id": line.id,
            }))
        if not lines:
            raise AccessError(env._("Nothing left to bill on %s.") % (
                order.display_name))
        bill = env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": order.partner_id.id,
            "invoice_origin": order.name,
            "ref": order.partner_ref or "",
            "invoice_date": payload.get("date") or False,
            "invoice_line_ids": lines,
        })
        return {"model": "account.move", "res_id": bill.id,
                "display_name": bill.display_name}
    if name == "transfer":
        ptype = payload.get("picking_type") or "internal"
        for loc_key in ("location_id", "location_dest_id"):
            if not env["stock.location"].browse(
                    payload.get(loc_key)).exists():
                raise AccessError(H["gone"](
                    env, "stock.location", payload.get(loc_key)))
        for item in (payload.get("lines") or []):
            if not env["product.product"].browse(
                    item.get("product_id")).exists() or not float(
                        item.get("product_uom_qty")
                        or item.get("quantity") or 0) > 0:
                raise AccessError(env._(
                    "A transfer line is no longer valid."))
        ptype_id = env["stock.picking.type"].search(
            [("code", "=", ptype)], limit=1)
        vals = {
            "picking_type_id": ptype_id.id if ptype_id else False,
            "location_id": int(payload.get("location_id") or 0) or False,
                    "location_dest_id": int(payload.get(
                        "location_dest_id") or 0) or False,
                "scheduled_date": payload.get("scheduled_date") or False,
                "origin": payload.get("origin") or "",
                "move_ids": [(0, 0, {
                    "name": env["product.product"].browse(
                        item.get("product_id") or 0).exists(
                        ).display_name if item.get("product_id") else "/",
                    "product_id": item.get("product_id") or False,
                "product_uom_qty": item.get("product_uom_qty")
                or item.get("quantity") or 0,
                "location_id": int(payload.get("location_id") or 0)
                or False,
                "location_dest_id": int(payload.get(
                    "location_dest_id") or 0) or False,
            }) for item in (payload.get("lines") or [])],
        }
        if payload.get("partner_id"):
            vals["partner_id"] = int(payload["partner_id"])
        picking = env["stock.picking"].create(vals)
        return {"model": "stock.picking", "res_id": picking.id,
                "display_name": picking.display_name}
    if name == "scrap":
        move = env["stock.move"].browse(
            payload.get("res_id")).exists()
        if not move:
            raise AccessError(H["gone"](env, "stock.move",
                                        payload.get("res_id")))
        allow = payload.get("expect_state") or []
        if allow and _state_now(move) not in [str(s) for s in allow]:
            raise AccessError(env._(
                "%(record)s is now %(state)s — the card expected "
                "%(allow)s.") % {
                    "record": move.display_name,
                    "state": _state_now(move),
                    "allow": "/".join(str(s) for s in allow)})
        if payload.get("quantity") is not None:
            move.write({"quantity": payload["quantity"]})
        try:
            move.action_scrap()
        except Exception as err:
            raise AccessError(env._("Cannot scrap %(move)s: %(err)s") % {
                "move": move.display_name,
                "err": str(err).split("\n")[0][:160]})
        return {"model": "stock.move", "res_id": move.id,
                "display_name": move.display_name}
    if name == "adjust":
        if not env["product.product"].browse(
                payload.get("product_id")).exists():
            raise AccessError(H["gone"](
                env, "product.product", payload.get("product_id")))
        if not env["stock.location"].browse(
                payload.get("location_id")).exists():
            raise AccessError(H["gone"](
                env, "stock.location", payload.get("location_id")))
        quant = env["stock.quant"].search([
            ("product_id", "=", payload.get("product_id")),
            ("location_id", "=", payload.get("location_id"))], limit=1)
        if not quant:
            quant = env["stock.quant"].create({
                "product_id": payload.get("product_id"),
                "location_id": payload.get("location_id"),
                "inventory_quantity": payload.get("quantity")})
        else:
            quant.write(
                {"inventory_quantity": payload.get("quantity")})
        quant.action_apply_inventory()
        return {"model": "stock.quant", "res_id": quant.id,
                "display_name": quant.product_id.display_name}
    if name == "bom":
        if not env["product.template"].browse(
                payload.get("product_tmpl_id")).exists():
            raise AccessError(H["gone"](
                env, "product.template", payload.get("product_tmpl_id")))
        for item in (payload.get("lines") or []):
            if not env["product.product"].browse(
                    item.get("product_id")).exists():
                raise AccessError(env._("A BOM line names a product "
                                        "that no longer exists."))
        bom = env["mrp.bom"].create({
            "product_tmpl_id": payload.get("product_tmpl_id"),
            "product_qty": payload.get("product_qty") or 1,
            "type": payload.get("type") or "normal",
            "bom_line_ids": [(0, 0, item) for item in (
                payload.get("lines") or [])],
        })
        return {"model": "mrp.bom", "res_id": bom.id,
                "display_name": bom.display_name}
    if name == "bom_tree":
        bom_id, _made = _bom_tree_create(
            env, H, payload.get("tree") or {})
        bom = env["mrp.bom"].browse(bom_id)
        return {"model": "mrp.bom", "res_id": bom.id,
                "display_name": bom.display_name}
    if name == "mo":
        if not env["product.product"].browse(
                payload.get("product_id")).exists():
            raise AccessError(H["gone"](
                env, "product.product", payload.get("product_id")))
        vals = {"product_id": payload.get("product_id"),
                "product_qty": payload.get("product_qty")}
        if payload.get("bom_id"):
            if not env["mrp.bom"].browse(
                    payload["bom_id"]).exists():
                raise AccessError(H["gone"](
                    env, "mrp.bom", payload["bom_id"]))
            vals["bom_id"] = payload["bom_id"]
        if payload.get("date_start"):
            vals["date_start"] = payload["date_start"]
        mo = env["mrp.production"].create(vals)
        return {"model": "mrp.production", "res_id": mo.id,
                "display_name": mo.display_name}
    if name == "unbuild":
        vals = {"product_id": payload.get("product_id"),
                "product_qty": payload.get("product_qty"),
                "location_id": payload.get("location_id")}
        if payload.get("location_dest_id"):
            vals["location_dest_id"] = payload["location_dest_id"]
        order = env["mrp.unbuild"].create(vals)
        order.action_validate()
        return {"model": "mrp.unbuild", "res_id": order.id,
                "display_name": order.display_name}
    if name == "misc":
        lines = payload.get("lines") or []
        debit = sum(float(item.get("debit") or 0) for item in lines)
        credit = sum(float(item.get("credit") or 0) for item in lines)
        if abs(debit - credit) > 0.005:
            raise AccessError(env._(
                "Out of balance at approve: debit %(dr)s vs credit "
                "%(cr)s.") % {"dr": debit, "cr": credit})
        move = env["account.move"].create({
            "journal_id": payload.get("journal_id"),
            "date": payload.get("date") or False,
            "ref": payload.get("ref") or "",
            "line_ids": [(0, 0, item) for item in lines],
        })
        return {"model": "account.move", "res_id": move.id,
                "display_name": move.display_name}
    if name == "leave":
        # v20 leaves land in "confirm" straight from create (default
        # state); there is no action_confirm to call.
        date_from = (payload.get("date_from") or "")[:16] or False
        date_to = (payload.get("date_to") or "")[:16] or False
        if not date_from or not date_to:
            raise AccessError(env._(
                "A leave needs a start and an end "
                "(YYYY-MM-DD HH:MM)."))
        leave = env["hr.leave"].create({
            "employee_id": payload.get("employee_id"),
            "holiday_status_id": payload.get("holiday_status_id"),
            "request_date_from": str(date_from)[:10],
            "request_date_to": str(date_to)[:10],
            "date_from": date_from,
            "date_to": date_to,
            "name": payload.get("notes") or "",
        })
        return {"model": "hr.leave", "res_id": leave.id,
                "display_name": leave.display_name}
    if name in ("vendor_full", "customer_full"):
        values = dict(payload.get("values") or {})
        partner = env["res.partner"].create(values)
        bank = (payload.get("bank_acc") or "").strip()
        if bank:
            # Same-account guard as the standalone bank tools: a
            # queued card must not mint a twin.
            if not env["res.partner.bank"].search([
                    ("partner_id", "=", partner.id),
                    ("acc_number", "in", [bank])], limit=1):
                env["res.partner.bank"].create({
                    "partner_id": partner.id, "acc_number": bank})
        return {"model": "res.partner", "res_id": partner.id,
                "display_name": partner.display_name}
    if name == "channel":
        channel = env["discuss.channel"].browse(
            payload.get("channel_id")).exists()
        if not channel:
            raise AccessError(H["gone"](env, "discuss.channel",
                                        payload.get("channel_id")))
        channel.message_post(body=payload.get("body") or "",
                             message_type="comment")
        return {"model": "discuss.channel", "res_id": channel.id,
                "display_name": channel.display_name}
    if name == "notify":
        record = env[payload.get("model")].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(env._("Record not found."))
        record.message_post(
            body=payload.get("body") or "", message_type="comment",
            subtype_xmlid="mail.mt_comment")
        return {"model": payload.get("model"), "res_id": record.id,
                "display_name": record.display_name}
    if name in ("close_task", "move_task_stage"):
        # Stage membership is rechecked: a stage moved to another
        # project (or a task moved elsewhere) between queue and
        # approve refuses instead of misfiling.
        record = env["project.task"].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(env._("Record not found."))
        stage = env["project.task.type"].browse(
            payload.get("stage_id")).exists()
        if not stage:
            raise AccessError(env._("That stage no longer exists."))
        if record.project_id and stage.project_ids and \
                record.project_id not in stage.project_ids:
            raise AccessError(env._(
                "%(stage)s does not belong to %(project)s.") % {
                    "stage": stage.display_name,
                    "project": record.project_id.display_name})
        record.write({"stage_id": stage.id})
        return {"model": "project.task", "res_id": record.id,
                "display_name": record.display_name}
    if name == "subscribe":
        record = env["project.task"].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(env._("Record not found."))
        record.message_subscribe(
            partner_ids=payload.get("partner_ids") or [])
        return {"model": "project.task", "res_id": record.id,
                "display_name": record.display_name}
    if name == "attendees":
        record = env["calendar.event"].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(env._("Record not found."))
        record.write({"partner_ids": [(4, pid) for pid in (
            payload.get("partner_ids") or [])]})
        return {"model": "calendar.event", "res_id": record.id,
                "display_name": record.display_name}
    if name == "lead_meeting":
        lead = env["crm.lead"].browse(
            payload.get("lead_id")).exists()
        event = env["calendar.event"].create({
            "name": payload.get("name"),
            "start": payload.get("start") or False,
            "stop": payload.get("stop") or False,
            "description": payload.get("description") or "",
            "partner_ids": [(6, 0, payload.get("partner_ids") or [])],
            "opportunity_id": lead.id if lead else False,
        })
        lead = env["crm.lead"].browse(
            payload.get("lead_id")).exists()
        if lead:
            lead.message_post(
                body=env._("Meeting scheduled: %s") % event.display_name)
        return {"model": "calendar.event", "res_id": event.id,
                "display_name": event.display_name}
    if name == "partner_terms":
        record = env["res.partner"].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(H["gone"](env, "res.partner",
                                        payload.get("res_id")))
        record.write(dict(payload.get("values") or {}))
        return {"model": "res.partner", "res_id": record.id,
                "display_name": record.display_name}
    if name == "bank_account":
        record = env["res.partner"].browse(
            payload.get("partner_id")).exists()
        if not record:
            raise AccessError(H["gone"](env, "res.partner",
                                        payload.get("partner_id")))
        dupe = env["res.partner.bank"].search([
            ("partner_id", "=", record.id),
            ("acc_number", "=", payload.get("acc_number") or "")],
            limit=1)
        if dupe:
            raise AccessError(env._(
                "%(partner)s already has that account.") % {
                    "partner": record.display_name})
        vals = {"partner_id": record.id,
                "acc_number": payload.get("acc_number")}
        if payload.get("bank_id"):
            vals["bank_id"] = payload["bank_id"]
        bank = env["res.partner.bank"].create(vals)
        return {"model": "res.partner.bank", "res_id": bank.id,
                "display_name": payload.get("acc_number") or ""}
    if name == "lead_call":
        record = env["crm.lead"].browse(
            payload.get("res_id")).exists()
        if not record:
            raise AccessError(env._("Record not found."))
        record.message_post(body=payload.get("body") or "",
                            message_type="comment")
        if payload.get("followup"):
            atype = env["mail.activity.type"].search([], limit=1)
            if atype:
                record.activity_schedule(
                    False, summary=payload["followup"][:80],
                    date_deadline=payload.get("date_deadline") or False,
                    activity_type_id=atype.id)
        return {"model": "crm.lead", "res_id": record.id,
                "display_name": record.display_name}
    raise AccessError(env._("That card cannot be applied."))


# ------------------------------------------------------------------
# registry + tool rows + grants
# ------------------------------------------------------------------
def _handler_for(code):
    def _handle(env, args, ctx):
        return run_biz(env, args or {}, ctx or {}, specs()[code])
    _handle.__name__ = "tool_" + code
    return _handle


def registry():
    """{code: handler} for all business tools."""
    return {code: _handler_for(code) for code in specs()}


def ensure_tool_rows(env):
    """Upsert nova.ai.tool rows for every spec (never overwrites)."""
    Tool = env["nova.ai.tool"]
    made = 0
    for code, spec in specs().items():
        if Tool.search([("code", "=", code)], limit=1):
            continue
        import json as _json
        Tool.sudo().create({
            "name": spec["name"],
            "code": code,
            "description": spec["desc"],
            "tier_min": spec.get("tier", "t2"),
            "write_kind": ("none" if spec.get("op") == "read"
                           else "confirm"),
            "schema": _json.dumps(schema_for(spec)),
            "example": _example_for_spec(spec),
            "active": True,
        })
        made += 1
    # Backfill: rows created before examples existed get a generated
    # one (never overwrites a hand-written example).
    for tool in Tool.search([("example", "=", False)]):
        spec = specs().get(tool.code)
        if spec:
            tool.sudo().write({"example": _example_for_spec(spec)})
    return made


def _example_for_spec(spec):
    """A runnable-shaped example from the spec's required params.

    Placeholder values by kind (weak models copy examples, and the
    queue-time validator corrects what is still missing). Hand-written
    examples always win: this only fills the gap for generated tools.
    """
    out = {}
    for param in spec.get("params") or []:
        if not param.get("req"):
            continue
        name = param.get("name")
        base = (param.get("kind") or "str").split(":")[0]
        if base == "int":
            out[name] = 0
        elif base == "float":
            out[name] = 0.0
        elif base == "bool":
            out[name] = False
        elif base == "date":
            out[name] = "2026-01-01"
        elif base == "datetime":
            out[name] = "2026-01-01 00:00:00"
        elif base in ("ids", "lines", "movelines", "tree", "m2m"):
            out[name] = []
        else:
            out[name] = ""
    return out


def grant_to_agents(env):
    """Grant new tools to agents whose tier qualifies (missing only)."""
    order = {"t1": 1, "t2": 2, "t3": 3}
    granted = 0
    tools = env["nova.ai.tool"].search(
        [("code", "in", list(specs()))])
    by_code = {t.code: t for t in tools}
    for agent in env["nova.ai.agent"].search([]):
        have = order.get(agent.tier or "t1", 1)
        for code, spec in specs().items():
            need = order.get(spec.get("tier", "t2"), 2)
            if have < need:
                continue
            tool = by_code.get(code)
            if not tool or tool in agent.tool_ids:
                continue
            agent.sudo().write({"tool_ids": [(4, tool.id)]})
            granted += 1
    return granted
