from odoo import models, fields, api
from datetime import datetime, timedelta
import logging

_logger = logging.getLogger(__name__)

class ReplenPlanTracking(models.Model):
    _name = 'replen.plan.tracking'
    _description = 'Suivi des plans de réapprovisionnement'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Référence', required=True, readonly=True)
    replen_plan_id = fields.Many2one('replen.plan', string='Plan de réapprovisionnement', required=True)
    period = fields.Char(string='Période concernée', related='replen_plan_id.period', store=True)
    validation_date = fields.Datetime(string='Date de validation', related='replen_plan_id.validation_date', store=True)
    state = fields.Selection([
        ('validated', 'Validé'),
        ('in_progress', 'En cours'),
        ('done', 'Terminé')
    ], string='État', default='validated', tracking=True)
    component_line_ids = fields.One2many('replen.plan.tracking.line', 'tracking_id', string='Composants')

    def action_view_details(self):
        self.state = 'in_progress'
        return {
            'name': f'Suivi du plan {self.name}',
            'type': 'ir.actions.act_window',
            'res_model': 'replen.plan.tracking',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.model
    def create_from_replen_plan(self, replen_plan, components_with_rfq, purchase_orders):
        tracking = self.create({
            'replen_plan_id': replen_plan.id,
            'name': replen_plan.name,
        })
        
        # Créer un dictionnaire pour associer les produits aux lignes de commande par fournisseur
        product_vendor_po_lines = {}
        for po in purchase_orders:
            for line in po.order_line:
                key = (line.product_id.id, line.partner_id.id)
                if key not in product_vendor_po_lines:
                    product_vendor_po_lines[key] = []
                product_vendor_po_lines[key].append(line)

        # Créer les lignes de suivi pour chaque combinaison composant-fournisseur
        for component in components_with_rfq:
            # Trouver toutes les combinaisons produit-fournisseur pour ce composant
            for key, po_lines in product_vendor_po_lines.items():
                product_id, vendor_id = key
                if product_id == component.product_id.id:
                    # Récupérer le délai de livraison depuis les lignes fournisseur du composant
                    supplier_line = component.supplier_line_ids.filtered(
                        lambda l: l.supplier_id.id == vendor_id
                    )
                    
                    lead_time = supplier_line.delivery_lead_time if supplier_line else 0
                    first_po_line = po_lines[0]
                    
                    # Calculer la quantité totale commandée pour ce fournisseur
                    quantity_ordered = sum(line.product_qty for line in po_lines)
                    
                    # Debug logs
                    _logger.info(f"Calcul du prix total pour le produit {component.product_id.name}:")
                    _logger.info(f"Prix unitaire: {first_po_line.price_unit}")
                    _logger.info(f"Quantité commandée: {quantity_ordered}")
                    
                    # Calculer le prix total
                    total_price = float(first_po_line.price_unit or 0.0) * float(quantity_ordered or 0.0)
                    _logger.info(f"Prix total calculé: {total_price}")
                    
                    self.env['replen.plan.tracking.line'].create({
                        'tracking_id': tracking.id,
                        'product_id': component.product_id.id,
                        'vendor_id': vendor_id,
                        'lead_time': lead_time,
                        'total_price': total_price,
                        'quantity_to_supply': quantity_ordered,
                        'quantity_received': 0.0,
                        'purchase_order_line_ids': [(6, 0, [line.id for line in po_lines])],
                    })
        
        return tracking

    def check_completion(self):
        self.ensure_one()
        all_received = all(line.state == 'done' for line in self.component_line_ids)
        if all_received:
            self.state = 'done'

class ReplenPlanTrackingLine(models.Model):
    _name = 'replen.plan.tracking.line'
    _description = 'Ligne de suivi des composants'

    tracking_id = fields.Many2one('replen.plan.tracking', string='Suivi', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Composant', required=True)
    vendor_id = fields.Many2one('res.partner', string='Fournisseur')
    lead_time = fields.Integer(string='Délai (jours)')
    expected_date = fields.Date(string='Date de réception prévue', compute='_compute_expected_date', store=True)
    total_price = fields.Float(string='Prix total', digits='Product Price')
    quantity_to_supply = fields.Float(string='Quantité à réapprovisionner', digits='Product Unit of Measure')
    quantity_received = fields.Float(string='Quantité reçue', digits='Product Unit of Measure')
    purchase_order_line_ids = fields.Many2many('purchase.order.line', string='Lignes de commande')
    state = fields.Selection([
        ('waiting', 'En attente'),
        ('partial', 'En cours'),
        ('done', 'Terminé'),
        ('late', 'En retard')
    ], string='État', compute='_compute_state', store=True)

    @api.depends('quantity_to_supply', 'quantity_received', 'expected_date')
    def _compute_state(self):
        today = fields.Date.today()
        for line in self:
            if line.quantity_received == 0:
                if line.expected_date and line.expected_date < today:
                    line.state = 'late'
                else:
                    line.state = 'waiting'
            elif line.quantity_received < line.quantity_to_supply:
                if line.expected_date and line.expected_date < today:
                    line.state = 'late'
                else:
                    line.state = 'partial'
            else:
                line.state = 'done'

    @api.depends('lead_time')
    def _compute_expected_date(self):
        for line in self:
            if line.tracking_id.validation_date and line.lead_time:
                line.expected_date = fields.Date.to_date(line.tracking_id.validation_date) + timedelta(days=line.lead_time)
            else:
                line.expected_date = False

    @api.model
    def create_from_replen_plan(self, replen_plan, components_with_rfq, purchase_orders):
        tracking = self.create({
            'replen_plan_id': replen_plan.id,
            'name': replen_plan.name,
        })
        
        # Créer un dictionnaire pour associer les produits aux lignes de commande par fournisseur
        product_vendor_po_lines = {}
        for po in purchase_orders:
            for line in po.order_line:
                key = (line.product_id.id, line.partner_id.id)
                if key not in product_vendor_po_lines:
                    product_vendor_po_lines[key] = []
                product_vendor_po_lines[key].append(line)

        # Créer les lignes de suivi pour chaque combinaison composant-fournisseur
        for component in components_with_rfq:
            # Trouver toutes les combinaisons produit-fournisseur pour ce composant
            for key, po_lines in product_vendor_po_lines.items():
                product_id, vendor_id = key
                if product_id == component.product_id.id:
                    # Récupérer le délai de livraison depuis les lignes fournisseur du composant
                    supplier_line = component.supplier_line_ids.filtered(
                        lambda l: l.supplier_id.id == vendor_id
                    )
                    
                    lead_time = supplier_line.delivery_lead_time if supplier_line else 0
                    first_po_line = po_lines[0]
                    
                    # Calculer la quantité totale commandée pour ce fournisseur
                    quantity_ordered = sum(line.product_qty for line in po_lines)
                    
                    # Debug logs
                    _logger.info(f"Calcul du prix total pour le produit {component.product_id.name}:")
                    _logger.info(f"Prix unitaire: {first_po_line.price_unit}")
                    _logger.info(f"Quantité commandée: {quantity_ordered}")
                    
                    # Calculer le prix total
                    total_price = float(first_po_line.price_unit or 0.0) * float(quantity_ordered or 0.0)
                    _logger.info(f"Prix total calculé: {total_price}")
                    
                    self.env['replen.plan.tracking.line'].create({
                        'tracking_id': tracking.id,
                        'product_id': component.product_id.id,
                        'vendor_id': vendor_id,
                        'lead_time': lead_time,
                        'total_price': total_price,
                        'quantity_to_supply': quantity_ordered,
                        'quantity_received': 0.0,
                        'purchase_order_line_ids': [(6, 0, [line.id for line in po_lines])],
                    })
        
        return tracking

    def update_from_purchase_order(self, purchase_order_line):
        self.ensure_one()
        
        # Récupérer le délai de livraison depuis les lignes fournisseur du composant
        component = self.env['replen.plan.component'].search([
            ('plan_id', '=', self.tracking_id.replen_plan_id.id),
            ('product_id', '=', self.product_id.id)
        ], limit=1)
        
        supplier_line = False
        if component:
            supplier_line = component.supplier_line_ids.filtered(
                lambda l: l.supplier_id == purchase_order_line.partner_id
            )
        
        lead_time = supplier_line.delivery_lead_time if supplier_line else 0
        
        # Calculer le nouveau prix total
        total_price = purchase_order_line.price_unit * self.quantity_to_supply
        
        self.write({
            'vendor_id': purchase_order_line.partner_id.id,
            'lead_time': lead_time,
            'total_price': total_price,
            'purchase_order_line_ids': [(4, purchase_order_line.id)],
        })

    def update_received_quantity(self):
        for line in self:
            received_qty = sum(move.product_uom_qty
                             for pol in line.purchase_order_line_ids
                             for move in pol.move_ids
                             if move.state == 'done')
            line.quantity_received = received_qty
            line.tracking_id.check_completion()

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def button_confirm(self):
        res = super(PurchaseOrder, self).button_confirm()
        for order in self:
            for line in order.order_line:
                tracking_lines = self.env['replen.plan.tracking.line'].search([
                    ('product_id', '=', line.product_id.id),
                    ('state', 'in', ['waiting', 'partial', 'late'])
                ])
                for tracking_line in tracking_lines:
                    tracking_line.update_from_purchase_order(line)
        return res

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def _action_done(self):
        res = super(StockPicking, self)._action_done()
        for picking in self:
            if picking.picking_type_code == 'incoming':
                tracking_lines = self.env['replen.plan.tracking.line'].search([
                    ('purchase_order_line_ids.move_ids.picking_id', '=', picking.id)
                ])
                tracking_lines.update_received_quantity()
        return res 