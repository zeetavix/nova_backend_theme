# Part of Nova. See LICENSE file for full copyright and licensing details.
"""Scheduled agents.

A schedule runs an agent on a cron with a fixed prompt — the morning
digest, the Friday pipeline review, the stale-quotation sweep. Runs
execute as the configured user (default: whoever created the schedule)
so record rules apply exactly as if that user had asked. Results land
in a session the user can open from the drawer, and optionally as a
chatter note on a pinned record.
"""

from odoo import _, api, fields, models

# Schedules running right now, by schedule id. The cron watchdog
# reloads the process when cron0 exceeds its time budget, and one
# slow model turn is already longer than that — so run_due() never
# blocks on a turn (see below), and this set stops two workers (or
# two ticks) from running the same schedule twice.
_RUNNING = set()
_RUNNING_LOCK = None


def _running_lock():
    global _RUNNING_LOCK
    if _RUNNING_LOCK is None:
        import threading
        _RUNNING_LOCK = threading.Lock()
    return _RUNNING_LOCK


class NovaAiSchedule(models.Model):
    _name = "nova.ai.schedule"
    _description = "Nova AI Schedule"
    _order = "name"

    name = fields.Char(required=True)
    agent_id = fields.Many2one(
        "nova.ai.agent", required=True, ondelete="cascade")
    prompt = fields.Text(required=True)
    run_as_user_id = fields.Many2one(
        "res.users", default=lambda self: self.env.user,
        help="Reads run with this user's access rights.")
    context_model = fields.Char()
    context_res_id = fields.Integer()
    post_note = fields.Boolean(
        help="Also post the reply as an internal note on the context.")
    mail_results = fields.Boolean(
        help="Also email the reply to the run-as user.")
    active = fields.Boolean(default=True)
    last_run = fields.Datetime(readonly=True)
    last_ok = fields.Boolean(readonly=True)
    last_error = fields.Text(readonly=True)
    last_reply = fields.Text(
        readonly=True,
        help="Reply of the previous successful run, fed back as the "
             "prior brief so the next run reports only deltas.")

    @api.model
    def run_due(self):
        for schedule in self.search([("active", "=", True)]):
            schedule._run_async()
        return True

    def _run_async(self):
        """Start one schedule in a daemon thread and return at once.

        The cron watchdog reloads the whole process when cron0 runs
        past its time budget; a single model turn on a slow endpoint
        already exceeds it. The turn therefore runs beside cron0, on
        its own cursor, and overlapping ticks are skipped instead of
        piling up.
        """
        self.ensure_one()
        lock = _running_lock()
        try:
            with lock:
                if self.id in _RUNNING:
                    return False
                _RUNNING.add(self.id)
        except Exception:
            return False
        import threading
        try:
            uid = self.env.uid
            dbname = self._cr.dbname
        except Exception:
            with _running_lock():
                _RUNNING.discard(self.id)
            return False
        thread = threading.Thread(
            target=self._run_async_done, args=(dbname, uid, self.id),
            name="nova-schedule-%d" % self.id, daemon=True)
        thread.start()
        return True

    @api.model
    def _run_async_done(self, dbname, uid, schedule_id):
        try:
            from odoo.modules.registry import Registry as _Registry
            with _Registry(dbname).cursor() as cr:
                env = api.Environment(cr, uid, {})
                schedule = env["nova.ai.schedule"].browse(
                    schedule_id).exists()
                if schedule:
                    schedule._run_one()
        except Exception as err:
            try:
                import logging
                logging.getLogger(__name__).exception(
                    "Scheduled agent %d failed: %s", schedule_id, err)
            except Exception:
                pass
        finally:
            try:
                with _running_lock():
                    _RUNNING.discard(schedule_id)
            except Exception:
                pass

    def _run_one(self):
        self.ensure_one()
        user = self.run_as_user_id or self.env.user
        agent = self.agent_id.with_user(user)
        # Kill-switch first: paused is paused, whatever the agent
        # state — an admin stopping Nova must see "paused", not a
        # downstream readiness error.
        from odoo.addons.nova_ai.controllers.nova_ai import _killed
        if _killed(self.env):
            self.write({"last_run": fields.Datetime.now(),
                        "last_ok": False,
                        "last_error": self.env._("Nova is paused.")})
            return
        if not agent or not agent.active or not agent.model_id:
            self.write({"last_run": fields.Datetime.now(),
                        "last_ok": False,
                        "last_error": self.env._("Agent not ready.")})
            return
        session = self.env["nova.ai.session"].with_user(user).create({
            "agent_id": agent.id,
            "context_model": self.context_model or "",
            "context_res_id": self.context_res_id or 0,
        })
        from odoo.addons.nova_ai.controllers.nova_ai import (
            run_chat_turn, _render_vars)
        prompt = _render_vars(
            self.env(user=user), self.prompt or "",
            self.context_model or "", self.context_res_id or 0)
        if self.last_reply:
            # Digest continuity (T-276): the model sees the prior
            # brief and reports only what changed since it, instead
            # of re-listing everything every run.
            prompt = "%s\n\nPrior brief from the previous run — report " \
                "only deltas vs this, and say 'no change since the " \
                "prior brief' when nothing moved:\n%s" % (
                    prompt, self.last_reply[:2000])
        try:
            result = run_chat_turn(
                self.env(user=user), agent, session, prompt,
                {"model": self.context_model or "",
                 "res_id": self.context_res_id or 0})
        except Exception as err:
            session.write({"active": False})
            try:
                session.set_progress("")
            except Exception:
                pass
            self.write({"last_run": fields.Datetime.now(),
                        "last_ok": False,
                        "last_error": str(err)[:500]})
            return
        reply = (result or {}).get("reply") or ""
        if self.post_note and self.context_model and self.context_res_id:
            try:
                record = self.env[self.context_model].with_user(user).browse(
                    self.context_res_id).exists()
                if record and hasattr(record, "message_post"):
                    record.message_post(
                        body=reply, message_type="comment",
                        subtype_xmlid="mail.mt_note")
            except Exception:
                pass
        delivery = ""
        if self.mail_results:
            delivery = self._mail_reply(user, reply)
        self.write({"last_run": fields.Datetime.now(), "last_ok": True,
                    "last_error": delivery, "last_reply": reply[:8000]})

    def _mail_reply(self, user, reply):
        """Queue the reply by email. Returns '' or a delivery note."""
        self.ensure_one()
        address = user.email or (user.partner_id.email if user.partner_id else "")
        if not address:
            return self.env._("Email skipped: the run-as user has no email address.")
        try:
            mail = self.env["mail.mail"].with_user(user).create({
                "subject": self.env._("Nova: %s", self.name),
                "body_html": reply or self.env._("(empty reply)"),
                "email_to": address,
            })
            mail.send()
        except Exception as err:
            return self.env._("Email queued but not sent: %s", str(err)[:200])
        return ""
