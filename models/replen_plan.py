from odoo import models, fields, api, tools, _
from odoo.exceptions import ValidationError, UserError
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
import base64

class ReplenPlanLine(models.Model):
    _name = 'replen.plan.line'
    _description = 'Ligne de prévision'

    plan_id = fields.Many2one('replen.plan', string='Plan', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Produit', required=True)
    date = fields.Date('Mois', required=True)
    date_display = fields.Char('Période', compute='_compute_date_display', store=True)
    historic_qty = fields.Float('Historique des ventes (par mois)', readonly=True)
    forecast_qty = fields.Float('Prévisions (par mois)')

    @api.depends('date')
    def _compute_date_display(self):
        months_fr = {
            1: 'janvier', 2: 'février', 3: 'mars', 4: 'avril',
            5: 'mai', 6: 'juin', 7: 'juillet', 8: 'août',
            9: 'septembre', 10: 'octobre', 11: 'novembre', 12: 'décembre'
        }
        for line in self:
            if line.date:
                month = months_fr[line.date.month]
                year = line.date.year
                line.date_display = f"{month} {year}"
            else:
                line.date_display = ""

class ReplenPlanSupplierLine(models.Model):
    _name = 'replen.plan.supplier.line'
    _description = 'Ligne de fournisseur pour réapprovisionnement'

    component_id = fields.Many2one('replen.plan.component', string='Composant', required=True, ondelete='cascade')
    supplier_id = fields.Many2one('res.partner', string='Fournisseur', required=True)
    price = fields.Float('Prix unitaire', digits='Product Price')
    total_price = fields.Float('Prix total', compute='_compute_total_price', store=True)
    delivery_lead_time = fields.Integer('Délai de livraison (jours)')
    quantity = fields.Float('Quantité', related='component_id.quantity_to_supply', store=True)

    @api.depends('price', 'quantity')
    def _compute_total_price(self):
        for line in self:
            line.total_price = line.price * line.quantity

class ReplenPlanComponent(models.Model):
    _name = 'replen.plan.component'
    _description = 'Ligne de réapprovisionnement des composants'

    plan_id = fields.Many2one('replen.plan', string='Plan', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Composant', required=True)
    forecast_consumption = fields.Float('Consommation prévisionnelle', digits='Product Unit of Measure')
    current_stock = fields.Float('Stock actuel', digits='Product Unit of Measure')
    safety_stock = fields.Float('Stock de sécurité', digits='Product Unit of Measure')
    stock_state = fields.Selection([
        ('available', 'Disponible'),
        ('warning', 'À surveiller'),
        ('urgent', 'Urgence')
    ], string='État', compute='_compute_stock_state', store=True)
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
    supplier_line_ids = fields.One2many('replen.plan.supplier.line', 'component_id', string='Lignes fournisseurs')

    @api.depends('current_stock', 'forecast_consumption', 'safety_stock')
    def _compute_stock_state(self):
        for record in self:
            difference = record.current_stock - record.forecast_consumption
            if difference > record.safety_stock:
                record.stock_state = 'available'
            elif difference == record.safety_stock:
                record.stock_state = 'warning'
            else:
                record.stock_state = 'urgent'

    @api.depends('forecast_consumption', 'current_stock', 'safety_stock')
    def _compute_quantity_to_supply(self):
        for line in self:
            calculated_qty = line.forecast_consumption - line.current_stock + line.safety_stock
            line.suggested_quantity = calculated_qty
            if not line.quantity_to_supply:
                line.quantity_to_supply = calculated_qty

    def action_reset_quantity_to_supply(self):
        """Réinitialise la quantité à réapprovisionner à la valeur suggérée"""
        for record in self:
            record.quantity_to_supply = record.suggested_quantity

    @api.model
    def create(self, vals):
        res = super(ReplenPlanComponent, self).create(vals)
        if res.product_id:
            # Créer une ligne pour chaque fournisseur
            supplier_lines = []
            for seller in res.product_id.seller_ids:
                supplier_lines.append({
                    'component_id': res.id,
                    'supplier_id': seller.name.id,
                    'price': seller.price,
                    'delivery_lead_time': seller.delay,
                })
            if supplier_lines:
                self.env['replen.plan.supplier.line'].create(supplier_lines)
        return res

class ReplenPlanComponentSupplierDisplay(models.Model):
    _name = 'replen.plan.component.supplier.display'
    _description = 'Affichage des composants et fournisseurs'
    _auto = False
    _order = 'product_id, supplier_id'

    plan_id = fields.Many2one('replen.plan', string='Plan', readonly=True)
    product_id = fields.Many2one('product.product', string='Composant', readonly=True)
    supplier_id = fields.Many2one('res.partner', string='Fournisseur', readonly=True)
    quantity_to_supply = fields.Float('Quantité à réapprovisionner', readonly=True)
    price = fields.Float('Prix unitaire', readonly=True)
    total_price = fields.Float('Prix total', readonly=True)
    delivery_lead_time = fields.Integer('Délai de livraison (jours)', readonly=True)
    expected_delivery_date = fields.Date('Date de réception prévue', compute='_compute_expected_delivery_date', store=False)
    is_late_delivery = fields.Boolean('Livraison hors période', compute='_compute_expected_delivery_date', store=False)

    @api.depends('delivery_lead_time', 'plan_id.date_start', 'plan_id.date_end')
    def _compute_expected_delivery_date(self):
        today = fields.Date.today()
        for record in self:
            # Calculer la date de livraison prévue
            record.expected_delivery_date = today + relativedelta(days=record.delivery_lead_time or 0)
            
            # Vérifier si la livraison est hors période
            record.is_late_delivery = False
            if record.expected_delivery_date and record.plan_id.date_end:
                record.is_late_delivery = record.expected_delivery_date > record.plan_id.date_end

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        
        # First drop the trigger if it exists
        self.env.cr.execute("""
            DROP TRIGGER IF EXISTS replen_plan_component_supplier_display_update_trigger 
            ON replen_plan_component_supplier_display;
        """)
        
        # Create the view
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW %s AS (
                SELECT 
                    ROW_NUMBER() OVER () AS id,
                    c.plan_id,
                    c.product_id,
                    sl.supplier_id,
                    c.quantity_to_supply,
                    sl.price,
                    sl.total_price,
                    sl.delivery_lead_time
                FROM replen_plan_component c
                JOIN replen_plan_supplier_line sl ON sl.component_id = c.id
            )
        """ % (self._table,))
        
        # Create the INSTEAD OF UPDATE trigger function
        self.env.cr.execute("""
            CREATE OR REPLACE FUNCTION update_replen_plan_component_supplier_display()
            RETURNS TRIGGER AS $$
            BEGIN
                -- Update the supplier line
                UPDATE replen_plan_supplier_line sl
                SET price = NEW.price,
                    total_price = NEW.total_price,
                    delivery_lead_time = NEW.delivery_lead_time
                FROM replen_plan_component c
                WHERE c.id = sl.component_id
                    AND c.plan_id = NEW.plan_id
                    AND c.product_id = NEW.product_id
                    AND sl.supplier_id = NEW.supplier_id;
                
                -- Update the component
                UPDATE replen_plan_component
                SET quantity_to_supply = NEW.quantity_to_supply
                WHERE plan_id = NEW.plan_id
                    AND product_id = NEW.product_id;
                
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        
        # Create the trigger (without OR REPLACE)
        self.env.cr.execute("""
            CREATE TRIGGER replen_plan_component_supplier_display_update_trigger
            INSTEAD OF UPDATE ON replen_plan_component_supplier_display
            FOR EACH ROW
            EXECUTE FUNCTION update_replen_plan_component_supplier_display();
        """)

    def unlink(self):
        """Supprime les lignes fournisseur correspondantes"""
        if not self:
            return True

        # Stocker les informations nécessaires avant la suppression
        to_unlink_data = []
        for record in self:
            to_unlink_data.append({
                'plan_id': record.plan_id.id,
                'product_id': record.product_id.id,
                'supplier_id': record.supplier_id.id
            })

        # Supprimer les lignes fournisseur pour chaque enregistrement
        for data in to_unlink_data:
            # Rechercher le composant correspondant
            component = self.env['replen.plan.component'].search([
                ('plan_id', '=', data['plan_id']),
                ('product_id', '=', data['product_id'])
            ], limit=1)
            
            if component:
                # Supprimer la ligne fournisseur correspondante
                supplier_lines = component.supplier_line_ids.filtered(
                    lambda l: l.supplier_id.id == data['supplier_id']
                )
                if supplier_lines:
                    supplier_lines.unlink()
        
        return True

class ReplenPlan(models.Model):
    _name = 'replen.plan'
    _description = 'Plan de réapprovisionnement'
    _order = 'create_date desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char('Référence', required=True, copy=False, readonly=True, 
                      default=lambda self: _('Nouveau'))
    
    currency_id = fields.Many2one('res.currency', string='Devise',
                                 default=lambda self: self.env.company.currency_id.id,
                                 required=True, readonly=True)
    
    state = fields.Selection([
        ('draft', 'Paramétrage initial'),
        ('forecast', 'Planification prévisionnelle'),
        ('plan', 'Planification du réapprovisionnement'),
        ('report', 'Rapport de réapprovisionnement'),
        ('done', 'Validé')
    ], string='État', default='draft', required=True)

    validation_date = fields.Datetime('Date de validation', readonly=True, copy=False)
    period = fields.Char(string='Période', compute='_compute_period', store=True)

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
        ('01', 'janvier'), ('02', 'février'), ('03', 'mars'),
        ('04', 'avril'), ('05', 'mai'), ('06', 'juin'),
        ('07', 'juillet'), ('08', 'août'), ('09', 'septembre'),
        ('10', 'octobre'), ('11', 'novembre'), ('12', 'décembre')
    ], string='Mois')

    sub_period_quarterly = fields.Selection([
        ('Q1', '1er trimestre'), ('Q2', '2ème trimestre'),
        ('Q3', '3ème trimestre'), ('Q4', '4ème trimestre')
    ], string='Trimestre')

    sub_period_biannual = fields.Selection([
        ('S1', '1er semestre'), ('S2', '2ème semestre')
    ], string='Semestre')

    @api.model
    def _get_year_selection(self):
        current_year = fields.Date.today().year
        return [(str(year), str(year)) for year in range(current_year, current_year + 6)]

    sub_period_annual = fields.Selection(
        selection='_get_year_selection',
        string='Année'
    )

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
    component_supplier_ids = fields.One2many('replen.plan.component.supplier.display', 'plan_id', string='Composants et fournisseurs')
    has_empty_forecasts = fields.Boolean(compute='_compute_has_empty_forecasts')

    product_count = fields.Integer(
        string='Nombre de produits',
        compute='_compute_product_count'
    )

    has_late_deliveries = fields.Boolean(
        string='A des livraisons tardives',
        compute='_compute_has_late_deliveries',
        store=False
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
                    '01': 'janvier', '02': 'février', '03': 'mars',
                    '04': 'avril', '05': 'mai', '06': 'juin',
                    '07': 'juillet', '08': 'août', '09': 'septembre',
                    '10': 'octobre', '11': 'novembre', '12': 'décembre'
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
        
        result.message_post(body="Plan de réapprovisionnement créé.", subtype_xmlid="mail.mt_note")
        
        return result

    @api.depends('period_type', 'sub_period')
    def _compute_dates(self):
        for plan in self:
            if not all([plan.period_type, plan.sub_period]):
                continue
                
            today = date.today()
            
            if plan.period_type == 'monthly':
                month = int(plan.sub_period)
                if today.month > month:
                    current_year = today.year + 1
                else:
                    current_year = today.year
                start_date = date(current_year, month, 1)
                end_date = (start_date + relativedelta(months=1, days=-1))
                
            elif plan.period_type == 'quarterly':
                quarters = {'Q1': 1, 'Q2': 4, 'Q3': 7, 'Q4': 10}
                month = quarters[plan.sub_period]
                if today.month > month:
                    current_year = today.year + 1
                else:
                    current_year = today.year
                start_date = date(current_year, month, 1)
                end_date = (start_date + relativedelta(months=3, days=-1))
                
            elif plan.period_type == 'biannual':
                semesters = {'S1': 1, 'S2': 7}
                month = semesters[plan.sub_period]
                if today.month > month:
                    current_year = today.year + 1
                else:
                    current_year = today.year
                start_date = date(current_year, month, 1)
                end_date = (start_date + relativedelta(months=6, days=-1))
                
            else:  # annual
                year = int(plan.sub_period)
                start_date = date(year, 1, 1)
                end_date = date(year, 12, 31)
            
            plan.date_start = start_date
            plan.date_end = end_date

    def _get_months_in_period(self):
        """Retourne la liste des mois de la période, en commençant au mois en cours si la période sélectionnée inclut le présent."""
        self.ensure_one()
        months = []
        start_date = self.date_start
        end_date = self.date_end
        today = date.today()

        # Si la période sélectionnée inclut l'année en cours, on commence au mois actuel
        if self.period_type == 'annual' and self.sub_period == 'Y0':
            if start_date.year == today.year:
                current_date = date(today.year, today.month, 1)
            else:
                current_date = start_date
        # Pour les autres périodes, on adapte aussi si la sous-période est "en cours"
        elif self.period_type == 'monthly' and start_date.year == today.year and int(self.sub_period) == today.month:
            current_date = date(today.year, today.month, 1)
        elif self.period_type == 'quarterly' and start_date.year == today.year:
            # Si le trimestre sélectionné commence avant le mois actuel, on commence au mois actuel
            if start_date <= today <= end_date:
                current_date = date(today.year, today.month, 1)
            else:
                current_date = start_date
        elif self.period_type == 'biannual' and start_date.year == today.year:
            if start_date <= today <= end_date:
                current_date = date(today.year, today.month, 1)
            else:
                current_date = start_date
        else:
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
            component._compute_quantity_to_supply()
            
        self.write({'state': 'report'})
        return self._return_form_action('report')

    def action_generate_rfq(self):
        self.ensure_one()

        # Grouper les composants par fournisseur
        supplier_products = {}
        rfq_count = 0
        components_with_rfq = []  # Liste pour suivre les composants avec demande de prix

        for component in self.component_ids:
            for supplier_line in component.supplier_line_ids:
                if supplier_line.supplier_id not in supplier_products:
                    supplier_products[supplier_line.supplier_id] = []
                    rfq_count += 1
                supplier_products[supplier_line.supplier_id].append({
                    'product_id': component.product_id.id,
                    'quantity': component.quantity_to_supply,
                    'price_unit': supplier_line.price,
                })
                if component not in components_with_rfq:
                    components_with_rfq.append(component)

        # Créer une demande de prix pour chaque fournisseur
        purchase_obj = self.env['purchase.order']
        purchase_orders = []  # Liste pour stocker les bons de commande créés

        for supplier, products in supplier_products.items():
            # Créer l'entête de la demande de prix
            po_vals = {
                'partner_id': supplier.id,
                'state': 'draft',
                'origin': f'Réappro {self.name}',
            }
            purchase_order = purchase_obj.create(po_vals)
            purchase_orders.append(purchase_order)

            # Ajouter les lignes de produits
            for product in products:
                self.env['purchase.order.line'].create({
                    'order_id': purchase_order.id,
                    'product_id': product['product_id'],
                    'product_qty': product['quantity'],
                    'price_unit': product['price_unit'],
                    'name': self.env['product.product'].browse(product['product_id']).name,
                    'date_planned': fields.Date.today(),
                    'product_uom': self.env['product.product'].browse(product['product_id']).uom_po_id.id,
                })

        # Créer le suivi du plan avec les composants qui ont des demandes de prix
        tracking = self.env['replen.plan.tracking'].create_from_replen_plan(self, components_with_rfq, purchase_orders)

        # Passage à l'état validé et mise à jour de la date de validation
        self.write({
            'state': 'done',
            'validation_date': fields.Datetime.now()
        })

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

    @api.depends('component_ids.forecast_consumption')
    def _compute_total_amount(self):
        for plan in self:
            plan.total_amount = sum(plan.component_ids.mapped('forecast_consumption'))

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
        old_states = {rec.id: rec.state for rec in self}
        result = super(ReplenPlan, self).write(vals)
        if 'state' in vals:
            for rec in self:
                old_state = old_states.get(rec.id)
                new_state = rec.state
                if old_state != new_state:
                    state_dict = dict(self.fields_get(allfields=['state'])['state']['selection'])
                    message = f"État : {state_dict.get(old_state, old_state)} → {state_dict.get(new_state, new_state)}"
                    rec.message_post(body=message, subtype_xmlid="mail.mt_note")
        return result

    def _get_product_domain(self):
        """Calcule le domaine pour les produits en tenant compte des variantes"""
        return [
            ('product_tmpl_id.bom_ids', '!=', False),  # Produits avec nomenclature au niveau du modèle
            ('sale_ok', '=', True),                    # Produits pouvant être vendus
            ('type', '=', 'product'),                  # Produits stockables uniquement
            ('active', '=', True)                      # Produits actifs uniquement
        ]

    @api.depends('period_type', 'sub_period', 'sub_period_monthly', 'sub_period_quarterly', 
                'sub_period_biannual', 'sub_period_annual')
    def _compute_period(self):
        months_fr = {
            '01': 'janvier', '02': 'février', '03': 'mars',
            '04': 'avril', '05': 'mai', '06': 'juin',
            '07': 'juillet', '08': 'août', '09': 'septembre',
            '10': 'octobre', '11': 'novembre', '12': 'décembre'
        }
        for plan in self:
            if not plan.period_type or not plan.sub_period:
                plan.period = False
                continue

            if plan.period_type == 'monthly':
                if plan.sub_period in months_fr:
                    year = fields.Date.today().year
                    if fields.Date.today().month > int(plan.sub_period):
                        year += 1
                    plan.period = f"{months_fr[plan.sub_period]} {year}"
                else:
                    plan.period = False
            elif plan.period_type == 'quarterly':
                quarters = {
                    'Q1': '1er trimestre',
                    'Q2': '2ème trimestre',
                    'Q3': '3ème trimestre',
                    'Q4': '4ème trimestre'
                }
                if plan.sub_period in quarters:
                    year = fields.Date.today().year
                    quarter_month = {'Q1': 1, 'Q2': 4, 'Q3': 7, 'Q4': 10}
                    if fields.Date.today().month > quarter_month[plan.sub_period]:
                        year += 1
                    plan.period = f"{quarters[plan.sub_period]} {year}"
                else:
                    plan.period = False
            elif plan.period_type == 'biannual':
                semesters = {
                    'S1': '1er semestre',
                    'S2': '2ème semestre'
                }
                if plan.sub_period in semesters:
                    year = fields.Date.today().year
                    semester_month = {'S1': 1, 'S2': 7}
                    if fields.Date.today().month > semester_month[plan.sub_period]:
                        year += 1
                    plan.period = f"{semesters[plan.sub_period]} {year}"
                else:
                    plan.period = False
            elif plan.period_type == 'annual':
                plan.period = f"Année {plan.sub_period}"
            else:
                plan.period = False

    @api.depends('component_supplier_ids', 'component_supplier_ids.expected_delivery_date', 'date_end')
    def _compute_has_late_deliveries(self):
        for plan in self:
            plan.has_late_deliveries = False
            for supplier in plan.component_supplier_ids:
                if supplier.is_late_delivery:
                    plan.has_late_deliveries = True
                    break