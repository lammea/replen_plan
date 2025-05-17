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
    total_price = fields.Float(
        'Prix total',
        compute='_compute_supplier_info',
        digits='Product Price',
        store=True,
        help="Prix total basé sur la quantité à réapprovisionner"
    )
    delivery_lead_time = fields.Integer(
        'Délai de livraison (jours)',
        compute='_compute_supplier_info',
        store=True,
        help="Délai de livraison du fournisseur sélectionné"
    )

    def dummy_button(self):
        """Méthode factice pour le bouton de sélection de fournisseur"""
        return True

    @api.depends('supplier_id', 'product_id', 'quantity_to_supply')
    def _compute_supplier_info(self):
        for line in self:
            if not (line.supplier_id and line.product_id and line.quantity_to_supply):
                line.total_price = 0.0
                line.delivery_lead_time = 0
                continue

            # Rechercher l'info fournisseur
            supplier_info = self.env['product.supplierinfo'].search([
                ('name', '=', line.supplier_id.id),
                ('product_tmpl_id', '=', line.product_id.product_tmpl_id.id)
            ], limit=1)

            if supplier_info:
                # Calculer le prix total
                line.total_price = supplier_info.price * line.quantity_to_supply
                line.delivery_lead_time = supplier_info.delay
            else:
                line.total_price = 0.0
                line.delivery_lead_time = 0

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

    total_amount = fields.Float(
        string='Montant total',
        compute='_compute_total_amount',
        store=True,
        help="Montant total du plan de réapprovisionnement"
    )

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
        domain=lambda self: self._get_product_domain(),
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
        products = self.env['product.product'].search(self._get_product_domain())
        self.product_ids = [(6, 0, products.ids)]

    @api.onchange('sub_period_monthly', 'sub_period_quarterly', 
                 'sub_period_biannual', 'sub_period_annual')
    def _onchange_sub_period(self):
        # Réinitialisation des produits avec tous les produits éligibles
        if any([self.sub_period_monthly, self.sub_period_quarterly,
                self.sub_period_biannual, self.sub_period_annual]):
            products = self.env['product.product'].search(self._get_product_domain())
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
        """Surcharge de la méthode de création pour gérer la séquence et l'état initial"""
        if vals.get('name', _('Nouveau')) == _('Nouveau'):
            vals['name'] = self.env['ir.sequence'].next_by_code('replen.plan') or _('Nouveau')
        
        # S'assurer que l'état initial est 'draft'
        vals['state'] = 'draft'
        
        # Création de l'enregistrement
        result = super(ReplenPlan, self).create(vals)
        
        return result

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

    def _return_form_action(self, state):
        """Helper pour retourner l'action appropriée avec le bon contexte"""
        action_mapping = {
            'draft': 'replen_plan.action_replen_plan_draft',
            'forecast': 'replen_plan.action_replen_plan_forecast',
            'plan': 'replen_plan.action_replen_plan_supply',
            'report': 'replen_plan.action_replen_plan_report',
            'done': 'replen_plan.action_replen_plan_validated'
        }
        
        action = self.env.ref(action_mapping[state]).read()[0]
        action.update({
            'res_id': self.id,
            'target': 'current',
            'context': {
                'form_view_initial_mode': 'readonly' if state == 'done' else 'edit',
                'state_view': state
            }
        })
        return action

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
        self.write({'state': 'forecast'})
        
        return self._return_form_action('forecast')

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
        """Récupère récursivement tous les composants d'un produit, y compris les sous-assemblages"""
        if component_needs is None:
            component_needs = {}
        if level > 10:  # Protection contre les boucles infinies
            return component_needs

        # Récupérer la nomenclature spécifique à la variante
        boms = self.env['mrp.bom']._bom_find(product)
        if not boms or not boms.get(product):
            return component_needs
            
        bom = boms[product]
        if not bom or not bom.bom_line_ids:
            return component_needs

        # Pour chaque composant dans la nomenclature
        for bom_line in bom.bom_line_ids:
            component = bom_line.product_id
            qty_needed = bom_line.product_qty * qty

            # Si le composant a une nomenclature (sous-assemblage ou kit)
            sub_boms = self.env['mrp.bom']._bom_find(component)
            sub_bom = sub_boms.get(component) if sub_boms else False
            
            if sub_bom and sub_bom.bom_line_ids:
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
        return self._return_form_action('plan')

    def action_back_to_draft(self):
        self.ensure_one()
        self.write({'state': 'draft'})
        return self._return_form_action('draft')

    def action_back_to_forecast(self):
        self.ensure_one()
        self.write({'state': 'forecast'})
        return self._return_form_action('forecast')

    def action_back_to_plan(self):
        self.ensure_one()
        self.write({'state': 'plan'})
        return self._return_form_action('plan')

    def action_to_report(self):
        self.ensure_one()
        
        # Force le recalcul des fournisseurs disponibles
        for component in self.component_ids:
            component._compute_available_suppliers()
            
        self.write({'state': 'report'})
        return self._return_form_action('report')

    def action_generate_rfq(self):
        self.ensure_one()
        
        # Vérifier que tous les composants ont un fournisseur sélectionné
        if any(not comp.supplier_id for comp in self.component_ids):
            raise UserError(_("Veuillez sélectionner un fournisseur pour tous les composants."))

        # Grouper les composants par fournisseur
        supplier_products = {}
        rfq_count = 0
        for component in self.component_ids:
            if component.supplier_id not in supplier_products:
                supplier_products[component.supplier_id] = []
                rfq_count += 1
            supplier_products[component.supplier_id].append({
                'product_id': component.product_id.id,
                'quantity': component.quantity_to_supply,
            })

        # Créer une demande de prix pour chaque fournisseur
        purchase_obj = self.env['purchase.order']
        for supplier, products in supplier_products.items():
            # Créer l'entête de la demande de prix
            po_vals = {
                'partner_id': supplier.id,
                'state': 'draft',
                'origin': f'Réappro {self.name}',
            }
            purchase_order = purchase_obj.create(po_vals)

            # Ajouter les lignes de produits
            for product in products:
                self.env['purchase.order.line'].create({
                    'order_id': purchase_order.id,
                    'product_id': product['product_id'],
                    'product_qty': product['quantity'],
                    'name': self.env['product.product'].browse(product['product_id']).name,
                    'date_planned': fields.Date.today(),
                    'product_uom': self.env['product.product'].browse(product['product_id']).uom_po_id.id,
                })

        # Passage à l'état validé
        self.write({'state': 'done'})

        # Message de notification avec redirection
        message = _('{} demande(s) de prix ont été générée(s) avec succès.').format(rfq_count)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Succès'),
                'message': message,
                'sticky': False,
                'type': 'success',
                'next': self._return_form_action('done'),
            },
        }

    @api.model
    def get_formview_id(self, access_uid=None):
        """Retourne l'ID de la vue form appropriée en fonction de l'état du plan"""
        view_mapping = {
            'draft': 'replen_plan.replen_plan_view_form',
            'forecast': 'replen_plan.replen_plan_forecast_form',
            'plan': 'replen_plan.replen_plan_supply_form',
            'report': 'replen_plan.replen_plan_report_form',
            'done': 'replen_plan.replen_plan_validated_form'
        }
        
        # Pour un nouvel enregistrement ou état par défaut
        if not self._context.get('state_view'):
            return self.env.ref('replen_plan.replen_plan_view_form').id
            
        # Pour un état spécifique
        state = self._context.get('state_view')
        if state in view_mapping:
            return self.env.ref(view_mapping[state]).id
            
        return self.env.ref('replen_plan.replen_plan_view_form').id

    def open_form(self):
        """Ouvre la vue appropriée en fonction de l'état du plan"""
        self.ensure_one()
        
        # Récupérer l'état demandé du contexte ou utiliser l'état actuel
        requested_state = self.env.context.get('state_view', self.state)
        
        action_mapping = {
            'draft': 'replen_plan.action_replen_plan_draft',
            'forecast': 'replen_plan.action_replen_plan_forecast',
            'plan': 'replen_plan.action_replen_plan_supply',
            'report': 'replen_plan.action_replen_plan_report',
            'done': 'replen_plan.action_replen_plan_validated'
        }
        
        # Si l'état demandé est antérieur à l'état actuel, utiliser l'état actuel
        state_sequence = ['draft', 'forecast', 'plan', 'report', 'done']
        current_index = state_sequence.index(self.state)
        requested_index = state_sequence.index(requested_state)
        
        # Utiliser l'état actuel si l'état demandé est antérieur
        final_state = self.state if requested_index < current_index else requested_state
        
        action = self.env.ref(action_mapping[final_state]).read()[0]
        action.update({
            'res_id': self.id,
            'target': 'current',
            'context': {
                'form_view_initial_mode': 'readonly' if final_state == 'done' else 'edit',
                'state_view': final_state
            }
        })
        return action

    def action_open_plan(self):
        """Méthode appelée lors du clic sur un plan dans la vue liste"""
        return self.open_form()

    @api.depends('component_ids.total_price')
    def _compute_total_amount(self):
        for plan in self:
            plan.total_amount = sum(plan.component_ids.mapped('total_price'))

    def _show_welcome_message(self):
        """Affiche un message de bienvenue en fonction de l'état du plan"""
        state_messages = {
            'draft': _("Vous pouvez continuer le paramétrage de votre plan."),
            'forecast': _("Vous pouvez continuer la saisie de vos prévisions."),
            'plan': _("Vous pouvez continuer l'ajustement de votre plan de réapprovisionnement."),
            'report': _("Vous pouvez continuer la sélection des fournisseurs."),
            'done': _("Le plan est validé et en lecture seule.")
        }
        
        if self.state in state_messages:
            message = _("Bienvenue dans votre plan de réapprovisionnement. ") + state_messages[self.state]
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _("Reprise de l'activité"),
                    'message': message,
                    'type': 'info',
                    'sticky': False,
                }
            }

    def write(self, vals):
        """Surcharge de la méthode d'écriture pour gérer les messages de bienvenue"""
        result = super(ReplenPlan, self).write(vals)
        if 'state' in vals:
            return self._show_welcome_message()
        return result

    def _get_product_domain(self):
        """Calcule le domaine pour les produits en tenant compte des variantes"""
        return [
            ('product_tmpl_id.bom_ids', '!=', False),  # Produits avec nomenclature au niveau du modèle
            ('sale_ok', '=', True),                    # Produits pouvant être vendus
            ('type', '=', 'product'),                  # Produits stockables uniquement
            ('active', '=', True)                      # Produits actifs uniquement
        ]