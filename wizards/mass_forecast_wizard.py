from odoo import models, fields, api, _

class ReplenPlanMassForecastWizard(models.TransientModel):
    _name = 'replen.plan.mass.forecast.wizard'
    _description = 'Assistant de saisie de masse des prévisions'

    plan_id = fields.Many2one('replen.plan', string='Plan', required=True)
    forecast_qty = fields.Float(string='Quantité prévisionnelle', required=True)
    select_all = fields.Boolean('Tout sélectionner', default=False)
    line_ids = fields.Many2many('replen.plan.line', string='Lignes à modifier')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self._context.get('active_id'):
            plan = self.env['replen.plan'].browse(self._context['active_id'])
            res.update({
                'plan_id': plan.id,
                'line_ids': [(6, 0, plan.line_ids.ids)],
            })
        return res

    @api.onchange('select_all')
    def _onchange_select_all(self):
        if self.line_ids:
            self.line_ids.write({'selected': self.select_all})

    def action_apply(self):
        self.ensure_one()
        selected_lines = self.line_ids.filtered(lambda l: l.selected)
        if selected_lines:
            selected_lines.write({'forecast_qty': self.forecast_qty})
        return {'type': 'ir.actions.act_window_close'} 