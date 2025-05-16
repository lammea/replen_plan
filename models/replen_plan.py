from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

class ReplenPlanLine(models.Model):
    _name = 'replen.plan.line'
    _description = 'Ligne de prévision'

    plan_id = fields.Many2one('replen.plan', string='Plan', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Produit', required=True)
    date = fields.Date('Date', required=True)
    historic_qty = fields.Float('Historique des ventes', readonly=True)
    forecast_qty = fields.Float('Prévision')

class ReplenPlan(models.Model):
    _name = 'replen.plan'
    _description = 'Plan de réapprovisionnement'
    _order = 'create_date desc'

    name = fields.Char('Référence', required=True, copy=False, readonly=True, 
                      default=lambda self: _('Nouveau'))
    
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('forecast', 'Prévisions'),
        ('plan', 'Plan de réapprovisionnement'),
        ('done', 'Validé')
    ], string='État', default='draft', required=True, tracking=True)

    period_type = fields.Selection([
        ('monthly', 'Mensuelle'),
        ('quarterly', 'Trimestrielle'),
        ('biannual', 'Semestrielle'),
        ('annual', 'Annuelle')
    ], string='Type de période', required=True)

    show_sub_period = fields.Boolean(compute='_compute_show_sub_period')
    show_products = fields.Boolean(compute='_compute_show_products')

    sub_period_monthly = fields.Selection([
        ('01', 'Janvier'), ('02', 'Février'), ('03', 'Mars'),
        ('04', 'Avril'), ('05', 'Mai'), ('06', 'Juin'),
        ('07', 'Juillet'), ('08', 'Août'), ('09', 'Septembre'),
        ('10', 'Octobre'), ('11', 'Novembre'), ('12', 'Décembre')
    ], string='Mois')

    sub_period_quarterly = fields.Selection([
        ('Q1', '1er Trimestre'), ('Q2', '2ème Trimestre'),
        ('Q3', '3ème Trimestre'), ('Q4', '4ème Trimestre')
    ], string='Trimestre')

    sub_period_biannual = fields.Selection([
        ('S1', '1er Semestre'), ('S2', '2ème Semestre')
    ], string='Semestre')

    sub_period_annual = fields.Selection([
        ('Y0', 'Année en cours'), ('Y1', 'Année N+1'),
        ('Y2', 'Année N+2'), ('Y3', 'Année N+3'),
        ('Y4', 'Année N+4'), ('Y5', 'Année N+5')
    ], string='Année')

    sub_period = fields.Char(compute='_compute_sub_period', store=True)
    
    date_start = fields.Date('Date de début', compute='_compute_dates', store=True)
    date_end = fields.Date('Date de fin', compute='_compute_dates', store=True)
    
    product_ids = fields.Many2many(
        'product.product', 
        'replen_plan_product_rel',
        'plan_id',
        'product_id',
        string='Produits',
        domain="[('bom_ids', '!=', False), ('sale_ok', '=', True), ('type', '=', 'product')]",
        copy=True
    )

    product_count = fields.Integer(
        string='Nombre de produits',
        compute='_compute_product_count'
    )

    line_ids = fields.One2many('replen.plan.line', 'plan_id', string='Lignes de prévision')

    @api.depends('product_ids')
    def _compute_product_count(self):
        for plan in self:
            plan.product_count = len(plan.product_ids)

    @api.depends('period_type')
    def _compute_show_sub_period(self):
        for plan in self:
            plan.show_sub_period = bool(plan.period_type)

    @api.depends('sub_period')
    def _compute_show_products(self):
        for plan in self:
            plan.show_products = bool(plan.sub_period)

    @api.depends('period_type', 'sub_period_monthly', 'sub_period_quarterly', 
                'sub_period_biannual', 'sub_period_annual')
    def _compute_sub_period(self):
        for plan in self:
            if plan.period_type == 'monthly':
                plan.sub_period = plan.sub_period_monthly
            elif plan.period_type == 'quarterly':
                plan.sub_period = plan.sub_period_quarterly
            elif plan.period_type == 'biannual':
                plan.sub_period = plan.sub_period_biannual
            elif plan.period_type == 'annual':
                plan.sub_period = plan.sub_period_annual
            else:
                plan.sub_period = False

    @api.onchange('period_type')
    def _onchange_period_type(self):
        self.sub_period_monthly = False
        self.sub_period_quarterly = False
        self.sub_period_biannual = False
        self.sub_period_annual = False
        self.product_ids = [(5, 0, 0)]

    @api.onchange('sub_period_monthly', 'sub_period_quarterly', 
                 'sub_period_biannual', 'sub_period_annual')
    def _onchange_sub_period(self):
        # Récupérer les produits éligibles
        if any([self.sub_period_monthly, self.sub_period_quarterly,
                self.sub_period_biannual, self.sub_period_annual]):
            domain = [
                ('bom_ids', '!=', False),  # Produits avec nomenclature
                ('sale_ok', '=', True),    # Produits pouvant être vendus
                ('type', '=', 'product'),  # Produits stockables uniquement
                ('active', '=', True)      # Produits actifs uniquement
            ]
            products = self.env['product.product'].search(domain)
            self.product_ids = [(6, 0, products.ids)]
        else:
            self.product_ids = [(5, 0, 0)]

        # Gestion du message de changement d'année
        if not self.period_type or not self.sub_period:
            return
            
        today = date.today()
        current_year = today.year
        shifted_to_next_year = False
        
        if self.period_type == 'monthly' and self.sub_period_monthly:
            month = int(self.sub_period_monthly)
            if today.month > month:
                shifted_to_next_year = True
                
        elif self.period_type == 'quarterly' and self.sub_period_quarterly:
            quarters = {'Q1': 1, 'Q2': 4, 'Q3': 7, 'Q4': 10}
            month = quarters[self.sub_period_quarterly]
            if today.month > month:
                shifted_to_next_year = True
                
        elif self.period_type == 'biannual' and self.sub_period_biannual:
            semesters = {'S1': 1, 'S2': 7}
            month = semesters[self.sub_period_biannual]
            if today.month > month:
                shifted_to_next_year = True
        
        if shifted_to_next_year:
            period_names = {
                'monthly': {
                    '01': 'Janvier', '02': 'Février', '03': 'Mars',
                    '04': 'Avril', '05': 'Mai', '06': 'Juin',
                    '07': 'Juillet', '08': 'Août', '09': 'Septembre',
                    '10': 'Octobre', '11': 'Novembre', '12': 'Décembre'
                },
                'quarterly': {
                    'Q1': 'le 1er trimestre',
                    'Q2': 'le 2ème trimestre',
                    'Q3': 'le 3ème trimestre',
                    'Q4': 'le 4ème trimestre'
                },
                'biannual': {
                    'S1': 'le 1er semestre',
                    'S2': 'le 2ème semestre'
                }
            }
            
            period_value = ''
            if self.period_type == 'monthly':
                period_value = period_names['monthly'][self.sub_period_monthly]
            elif self.period_type == 'quarterly':
                period_value = period_names['quarterly'][self.sub_period_quarterly]
            elif self.period_type == 'biannual':
                period_value = period_names['biannual'][self.sub_period_biannual]
                
            message = _("La période sélectionnée ({}) étant antérieure à la date actuelle, "
                       "elle a été automatiquement décalée à l'année {}").format(
                           period_value, current_year + 1)
            return {
                'warning': {
                    'title': _('Changement d\'année'),
                    'message': message
                }
            }

    @api.model
    def create(self, vals):
        if vals.get('name', _('Nouveau')) == _('Nouveau'):
            vals['name'] = self.env['ir.sequence'].next_by_code('replen.plan') or _('Nouveau')
        return super(ReplenPlan, self).create(vals)

    @api.depends('period_type', 'sub_period')
    def _compute_dates(self):
        for plan in self:
            if not all([plan.period_type, plan.sub_period]):
                continue
                
            today = date.today()
            current_year = today.year
            
            if plan.period_type == 'monthly':
                month = int(plan.sub_period)
                if today.month > month:
                    current_year += 1
                start_date = date(current_year, month, 1)
                end_date = (start_date + relativedelta(months=1, days=-1))
                
            elif plan.period_type == 'quarterly':
                quarters = {'Q1': 1, 'Q2': 4, 'Q3': 7, 'Q4': 10}
                month = quarters[plan.sub_period]
                if today.month > month:
                    current_year += 1
                start_date = date(current_year, month, 1)
                end_date = (start_date + relativedelta(months=3, days=-1))
                
            elif plan.period_type == 'biannual':
                semesters = {'S1': 1, 'S2': 7}
                month = semesters[plan.sub_period]
                if today.month > month:
                    current_year += 1
                start_date = date(current_year, month, 1)
                end_date = (start_date + relativedelta(months=6, days=-1))
                
            else:  # annual
                year_offset = int(plan.sub_period[1])
                start_date = date(today.year + year_offset, 1, 1)
                end_date = date(today.year + year_offset, 12, 31)
            
            plan.date_start = start_date
            plan.date_end = end_date
        
    def _get_months_in_period(self):
        """Retourne la liste des mois de la période"""
        self.ensure_one()
        months = []
        start_date = self.date_start
        end_date = self.date_end
        current_date = start_date

        while current_date <= end_date:
            months.append(current_date)
            current_date += relativedelta(months=1)
        return months

    def action_to_forecast(self):
        self.ensure_one()
        if not self.product_ids:
            raise ValidationError(_("Veuillez conserver au moins un produit dans la liste."))
            
        # Vérification des champs obligatoires
        if not self.period_type or not self.sub_period:
            raise ValidationError(_("Veuillez sélectionner une période et une sous-période."))

        # Création des lignes de prévision
        months = self._get_months_in_period()
        lines_to_create = []
        
        for product in self.product_ids:
            for month_date in months:
                # TODO: Calculer l'historique des ventes
                historic_qty = 0.0
                
                lines_to_create.append({
                    'plan_id': self.id,
                    'product_id': product.id,
                    'date': month_date,
                    'historic_qty': historic_qty,
                    'forecast_qty': 0.0,
                })
        
        # Suppression des anciennes lignes si elles existent
        self.line_ids.unlink()
        
        # Création des nouvelles lignes
        self.env['replen.plan.line'].create(lines_to_create)
            
        # Passage à l'état 'forecast'
        self.write({
            'state': 'forecast',
        })
        
        # Retourner une action pour ouvrir la vue de saisie des prévisions
        return {
            'name': _('Saisie des prévisions - {}').format(self.sub_period),
            'type': 'ir.actions.act_window',
            'res_model': 'replen.plan',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('replen_plan.replen_plan_forecast_form').id,
            'target': 'current',
        } 