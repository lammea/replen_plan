from odoo import models, fields, api, _

class ReplenPlanConfirmWizard(models.TransientModel):
    _name = 'replen.plan.confirm.wizard'
    _description = 'Assistant de confirmation du plan'

    plan_id = fields.Many2one('replen.plan', string='Plan', required=True)

    def action_confirm(self):
        return self.plan_id._generate_plan()

    def action_cancel(self):
        return {'type': 'ir.actions.act_window_close'}