from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import base64

class ReplenPlanLine(models.Model):
    _name = 'replen.plan.line'
    _description = 'Ligne de prévision'

    plan_id = fields.Many2one('replen.plan', string='Plan', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Produit', required=True)
    date = fields.Date('Date', required=True)
    historic_qty = fields.Float('Historique des ventes', readonly=True)
    forecast_qty = fields.Float('Prévision')

class ReplenPlanComponent(models.Model):
    _name = 'replen.plan.component'
    _description = 'Ligne de réapprovisionnement des composants'

    plan_id = fields.Many2one('replen.plan', string='Plan', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Composant', required=True)
    forecast_consumption = fields.Float('Consommation prévisionnelle', digits='Product Unit of Measure')
    current_stock = fields.Float('Stock actuel', digits='Product Unit of Measure')
    safety_stock = fields.Float('Stock de sécurité', digits='Product Unit of Measure')
    quantity_to_supply = fields.Float(
        'Quantité à réapprovisionner',
        compute='_compute_quantity_to_supply',
        digits='Product Unit of Measure',
        store=True,
        readonly=False,
        help="Quantité calculée automatiquement mais modifiable si nécessaire"
    )
    suggested_quantity = fields.Float(
        'Quantité suggérée',
        compute='_compute_quantity_to_supply',
        digits='Product Unit of Measure',
        store=True,
        readonly=True,
        help="Quantité calculée automatiquement selon la formule standard"
    )
    available_supplier_ids = fields.Many2many(
        'res.partner',
        string='Fournisseurs disponibles',
        compute='_compute_available_suppliers',
        store=True
    )
    supplier_id = fields.Many2one(
        'res.partner',
        string='Fournisseur',
        domain="[('id', 'in', available_supplier_ids)]",
        help="Sélectionner un fournisseur pour ce composant"
    )

    def dummy_button(self):
        """Méthode factice pour le bouton de sélection de fournisseur"""
        return True

    @api.depends('product_id')
    def _compute_available_suppliers(self):
        for line in self:
            if line.product_id:
                suppliers = line.product_id.seller_ids.mapped('name')
                line.available_supplier_ids = [(6, 0, suppliers.ids)]
                # Si un seul fournisseur, le sélectionner automatiquement
                if len(suppliers) == 1:
                    line.supplier_id = suppliers[0]
            else:
                line.available_supplier_ids = [(6, 0, [])]
                line.supplier_id = False

    @api.depends('forecast_consumption', 'current_stock', 'safety_stock')
    def _compute_quantity_to_supply(self):
        for line in self:
            calculated_qty = line.forecast_consumption - line.current_stock + line.safety_stock
            line.suggested_quantity = calculated_qty
            if not line.quantity_to_supply:
                line.quantity_to_supply = calculated_qty

class ReplenPlan(models.Model):
    _name = 'replen.plan'
    _description = 'Plan de réapprovisionnement'
    _order = 'create_date desc'

    name = fields.Char('Référence', required=True, copy=False, readonly=True, 
                      default=lambda self: _('Nouveau'))
    
    state = fields.Selection([
        ('draft', 'Paramétrage initial'),
        ('forecast', 'Planification prévisionnelle'),
        ('plan', 'Planification du réapprovisionnement'),
        ('report', 'Rapport de réapprovisionnement'),
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

    line_ids = fields.One2many('replen.plan.line', 'plan_id', string='Lignes de prévision')
    component_ids = fields.One2many('replen.plan.component', 'plan_id', string='Composants')
    has_empty_forecasts = fields.Boolean(compute='_compute_has_empty_forecasts')

    product_count = fields.Integer(
        string='Nombre de produits',
        compute='_compute_product_count'
    )

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
        # Réinitialisation des sous-périodes
        self.sub_period_monthly = False
        self.sub_period_quarterly = False
        self.sub_period_biannual = False
        self.sub_period_annual = False
        
        # Réinitialisation des produits avec tous les produits éligibles
        domain = [
            ('bom_ids', '!=', False),  # Tous les produits ayant une nomenclature
            ('sale_ok', '=', True),    # Produits pouvant être vendus
            ('type', '=', 'product'),  # Produits stockables uniquement
            ('active', '=', True)      # Produits actifs uniquement
        ]
        products = self.env['product.product'].search(domain)
        self.product_ids = [(6, 0, products.ids)]

    @api.onchange('sub_period_monthly', 'sub_period_quarterly', 
                 'sub_period_biannual', 'sub_period_annual')
    def _onchange_sub_period(self):
        # Réinitialisation des produits avec tous les produits éligibles
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

    def _get_historic_sales(self, product_id, date):
        """Calcule les ventes historiques pour un produit à une date donnée"""
        self.ensure_one()
        
        # Calculer la même période l'année précédente
        start_date = date.replace(year=date.year - 1)
        end_date = (start_date + relativedelta(months=1, days=-1))
        
        # Rechercher les mouvements de stock sortants (livraisons)
        domain = [
            ('product_id', '=', product_id),
            ('state', '=', 'done'),
            ('date', '>=', start_date),
            ('date', '<=', end_date),
            ('location_dest_id.usage', '=', 'customer'),  # Livraisons aux clients
        ]
        
        moves = self.env['stock.move'].search(domain)
        total_qty = sum(moves.mapped('product_uom_qty'))
        
        return total_qty

    @api.depends('line_ids.forecast_qty')
    def _compute_has_empty_forecasts(self):
        for plan in self:
            plan.has_empty_forecasts = any(line.forecast_qty <= 0 for line in plan.line_ids)

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
                # Calculer l'historique des ventes pour ce produit et ce mois
                historic_qty = self._get_historic_sales(product.id, month_date)
                
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

    def action_generate_plan(self):
        self.ensure_one()

        # Vérification des prévisions vides
        if self.has_empty_forecasts:
            message = _("Attention : Certaines prévisions sont vides ou nulles. "
                       "Voulez-vous continuer quand même ?")
            return {
                'type': 'ir.actions.act_window',
                'name': _('Confirmation'),
                'res_model': 'replen.plan.confirm.wizard',
                'view_mode': 'form',
                'target': 'new',
                'context': {'default_plan_id': self.id}
            }
        else:
            return self._generate_plan()

    def _get_bom_components(self, product, qty, level=0, component_needs=None):
        """Récupère récursivement tous les composants d'un produit, y compris les sous-assemblages
        Args:
            product: product.product - Le produit dont on veut récupérer les composants
            qty: float - La quantité nécessaire du produit
            level: int - Niveau de récursion pour éviter les boucles infinies
            component_needs: dict - Dictionnaire pour accumuler les besoins en composants
        Returns:
            dict: Dictionnaire des besoins en composants
        """
        if component_needs is None:
            component_needs = {}
        if level > 10:  # Protection contre les boucles infinies
            return component_needs

        # Récupérer la nomenclature
        bom = self.env['mrp.bom']._bom_find(product)[product]
        if not bom:
            return component_needs

        # Pour chaque composant dans la nomenclature
        for bom_line in bom.bom_line_ids:
            component = bom_line.product_id
            qty_needed = bom_line.product_qty * qty

            # Si le composant a une nomenclature (sous-assemblage ou kit)
            sub_bom = self.env['mrp.bom']._bom_find(component)[component]
            if sub_bom:
                # Récursion pour obtenir les composants du sous-assemblage
                self._get_bom_components(component, qty_needed, level + 1, component_needs)
            else:
                # C'est un composant final
                if component.id in component_needs:
                    component_needs[component.id]['qty'] += qty_needed
                else:
                    # Récupérer le stock actuel
                    current_stock = component.qty_available

                    # Récupérer le stock de sécurité depuis les règles de stock
                    orderpoint = self.env['stock.warehouse.orderpoint'].search([
                        ('product_id', '=', component.id),
                        ('location_id.usage', '=', 'internal')
                    ], limit=1)
                    safety_stock = orderpoint.product_min_qty if orderpoint else 0.0

                    component_needs[component.id] = {
                        'product': component,
                        'qty': qty_needed,
                        'current_stock': current_stock,
                        'safety_stock': safety_stock
                    }

        return component_needs

    def _generate_plan(self):
        self.ensure_one()

        # Supprimer les anciennes lignes de composants
        self.component_ids.unlink()

        # Dictionnaire pour accumuler les besoins par composant
        component_needs = {}

        # Pour chaque produit fini et ses prévisions
        for line in self.line_ids:
            product = line.product_id
            forecast_qty = line.forecast_qty

            # Récupérer tous les composants de manière récursive
            component_needs = self._get_bom_components(
                product, 
                forecast_qty, 
                component_needs=component_needs
            )

        # Créer les lignes de composants
        component_lines = []
        for comp_id, data in component_needs.items():
            component_lines.append({
                'plan_id': self.id,
                'product_id': comp_id,
                'forecast_consumption': data['qty'],
                'current_stock': data['current_stock'],
                'safety_stock': data['safety_stock'],
            })

        if component_lines:
            self.env['replen.plan.component'].create(component_lines)

        # Passage à l'état 'plan'
        self.write({'state': 'plan'})

        # Retourner une action pour ouvrir la vue du plan de réapprovisionnement
        return {
            'name': _('Plan de réapprovisionnement - {}').format(self.sub_period),
            'type': 'ir.actions.act_window',
            'res_model': 'replen.plan',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('replen_plan.replen_plan_supply_form').id,
            'target': 'current',
        }

    def action_back_to_draft(self):
        self.ensure_one()
        self.write({'state': 'draft'})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'replen.plan',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('replen_plan.replen_plan_view_form').id,
            'target': 'current',
            'context': {'keep_products': True}
        }

    def action_back_to_forecast(self):
        self.ensure_one()
        self.write({'state': 'forecast'})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'replen.plan',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('replen_plan.replen_plan_forecast_form').id,
            'target': 'current',
        }

    def action_back_to_plan(self):
        """Retour à l'étape de plan depuis l'état validé"""
        self.ensure_one()
        self.write({'state': 'plan'})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'replen.plan',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('replen_plan.replen_plan_supply_form').id,
            'target': 'current',
        }

    def action_to_report(self):
        """Passage à l'étape de rapport de réapprovisionnement"""
        self.ensure_one()
        
        # Force le recalcul des fournisseurs disponibles
        for component in self.component_ids:
            component._compute_available_suppliers()
            
        self.write({'state': 'report'})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'replen.plan',
            'res_id': self.id,
            'view_mode': 'form',
            'view_id': self.env.ref('replen_plan.replen_plan_report_form').id,
            'target': 'current',
        }

    def action_validate(self):
        """Validation finale du plan de réapprovisionnement"""
        self.ensure_one()
        # Vérifier que tous les composants ont un fournisseur sélectionné
        if any(not comp.supplier_id for comp in self.component_ids):
            raise UserError(_("Veuillez sélectionner un fournisseur pour tous les composants."))
        self.write({'state': 'done'})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'replen.plan',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }