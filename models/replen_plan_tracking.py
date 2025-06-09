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
        ('in_progress', 'En cours'),
        ('done', 'Fin du réapprovisionnement')
    ], string='État', default='in_progress', tracking=True)
    component_line_ids = fields.One2many('replen.plan.tracking.line', 'tracking_id', string='Composants')
    component_count = fields.Integer(string='Nombre de composants', compute='_compute_component_count', store=True)
    total_amount = fields.Monetary(string='Montant total', compute='_compute_total_amount', store=True, currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Devise', default=lambda self: self.env.company.currency_id.id)
    progress_percentage = fields.Float(string='Avancement (%)', compute='_compute_progress_percentage', store=True)

    @api.depends('component_line_ids')
    def _compute_component_count(self):
        for record in self:
            record.component_count = len(record.component_line_ids)

    @api.depends('component_line_ids.total_price')
    def _compute_total_amount(self):
        for record in self:
            record.total_amount = sum(record.component_line_ids.mapped('total_price'))

    @api.depends('component_line_ids', 'component_line_ids.state', 'component_line_ids.quantity_received', 'component_line_ids.quantity_to_supply')
    def _compute_progress_percentage(self):
        for record in self:
            if not record.component_line_ids:
                record.progress_percentage = 0
                continue

            total_lines = len(record.component_line_ids)
            done_lines = len(record.component_line_ids.filtered(lambda l: l.state == 'done'))
            
            _logger.info(f"Calcul du pourcentage pour {record.name}:")
            _logger.info(f"Lignes terminées: {done_lines}")
            _logger.info(f"Total des lignes: {total_lines}")
            
            if total_lines > 0:
                percentage = (done_lines / total_lines) * 100
                _logger.info(f"Pourcentage calculé: {percentage}%")
                record.progress_percentage = percentage
            else:
                record.progress_percentage = 0

    def action_view_details(self):
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
            'state': 'in_progress',  # État initial : En cours
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

    @api.depends('component_line_ids.state')
    def check_completion(self):
        self.ensure_one()
        all_completed = all(line.state in ['done', 'rejected'] for line in self.component_line_ids)
        if all_completed:
            self.state = 'done'
        else:
            self.state = 'in_progress'

class ReplenPlanTrackingLine(models.Model):
    _name = 'replen.plan.tracking.line'
    _description = 'Ligne de suivi des composants'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    tracking_id = fields.Many2one('replen.plan.tracking', string='Suivi', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Composant', required=True)
    display_name = fields.Char(string='Nom affiché', compute='_compute_display_name', store=True)
    quantity_to_supply = fields.Float(string='Quantité à réapprovisionner', digits='Product Unit of Measure', tracking=True)
    vendor_id = fields.Many2one('res.partner', string='Fournisseur', tracking=True)
    lead_time = fields.Integer(string='Délai (jours)', tracking=True)
    expected_date = fields.Date(string='Date de réception prévue', compute='_compute_expected_date', store=True, readonly=False, tracking=True)
    total_price = fields.Float(string='Prix total', digits='Product Price', tracking=True)
    
    quantity_received = fields.Float(string='Quantité reçue', digits='Product Unit of Measure', tracking=True)
    quantity_pending = fields.Float(string='Quantité en attente', compute='_compute_quantity_pending', store=True, digits='Product Unit of Measure')
    purchase_order_line_ids = fields.Many2many('purchase.order.line', string='Lignes de commande')
    state = fields.Selection([
        ('waiting', 'En attente'),
        ('partial', 'En cours'),
        ('done', 'Terminé'),
        ('late', 'En retard'),
        ('rejected', 'Rejeté')
    ], string='État', compute='_compute_state', store=True, tracking=True)

    @api.depends('quantity_to_supply', 'quantity_received', 'expected_date', 'purchase_order_line_ids', 'purchase_order_line_ids.order_id.state')
    def _compute_state(self):
        today = fields.Date.today()
        for line in self:
            old_state = line.state
            if not line.purchase_order_line_ids:
                line.state = 'rejected'
            elif all(pol.order_id.state in ['purchase', 'done'] for pol in line.purchase_order_line_ids):
                if line.quantity_received > 0:
                    if line.quantity_received < line.quantity_to_supply:
                        line.state = 'partial'
                    else:
                        line.state = 'done'
                else:
                    if line.expected_date and line.expected_date < today:
                        line.state = 'late'
                    else:
                        line.state = 'waiting'
            else:
                line.state = 'waiting'

            # Enregistrer le changement d'état dans le chatter
            if old_state != line.state:
                line.tracking_id.message_post(
                    body=f"Le composant <b>{line.product_id.name}</b> est passé de l'état <b>{dict(line._fields['state'].selection).get(old_state, 'Nouveau')}</b> à <b>{dict(line._fields['state'].selection).get(line.state)}</b>",
                    message_type='notification',
                    subtype_xmlid='mail.mt_note'
                )
            
            # Appeler check_completion sur le tracking parent
            if line.tracking_id:
                line.tracking_id.check_completion()

    @api.depends('lead_time', 'tracking_id.validation_date')
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

    def update_from_purchase_order_line(self, purchase_order_line):
        """Met à jour les valeurs de la ligne de suivi en fonction des modifications de la ligne de demande de prix"""
        self.ensure_one()
        
        # Calculer la nouvelle quantité totale à partir de toutes les lignes de commande
        quantity_to_supply = sum(line.product_qty for line in self.purchase_order_line_ids)
        
        # Calculer le nouveau prix total
        total_price = sum(line.price_unit * line.product_qty for line in self.purchase_order_line_ids)
        
        # Si la date planifiée a été modifiée dans la demande de prix, on met à jour notre date
        if purchase_order_line.date_planned:
            self.expected_date = fields.Date.to_date(purchase_order_line.date_planned)
        
        # Mettre à jour les valeurs
        self.write({
            'quantity_to_supply': quantity_to_supply,
            'total_price': total_price,
        })

    def reset_tracking_line(self):
        """Réinitialise les valeurs de la ligne de suivi après suppression de la ligne de commande"""
        self.ensure_one()
        self.write({
            'vendor_id': False,
            'lead_time': 0,
            'total_price': 0,
            'quantity_to_supply': 0,
            'quantity_received': 0,
            'expected_date': False,
        })

    @api.depends('quantity_to_supply', 'quantity_received')
    def _compute_quantity_pending(self):
        for line in self:
            line.quantity_pending = line.quantity_to_supply - line.quantity_received

    def write(self, vals):
        # Pour chaque ligne, enregistrer les anciennes valeurs avant modification
        tracked_fields = {
            'quantity_to_supply': ('Quantité à réapprovisionner', 'Product Unit of Measure'),
            'quantity_received': ('Quantité reçue', 'Product Unit of Measure'),
            'quantity_pending': ('Quantité en attente', 'Product Unit of Measure'),
            'total_price': ('Prix total', 'currency'),
            'lead_time': ('Délai', 'jours'),
            'expected_date': ('Date de réception prévue', 'date'),
        }

        for line in self:
            changes = []
            for field, (label, unit_type) in tracked_fields.items():
                if field in vals and vals[field] != getattr(line, field):
                    old_value = getattr(line, field)
                    new_value = vals[field]
                    
                    # Formatage spécial selon le type de champ
                    if unit_type == 'Product Unit of Measure':
                        old_str = f"{old_value} {line.product_id.uom_id.name}"
                        new_str = f"{new_value} {line.product_id.uom_id.name}"
                    elif unit_type == 'currency':
                        currency = self.env.company.currency_id
                        old_str = f"{currency.symbol} {old_value}"
                        new_str = f"{currency.symbol} {new_value}"
                    elif unit_type == 'date':
                        old_str = old_value.strftime('%d/%m/%Y') if old_value else 'Non défini'
                        new_str = fields.Date.from_string(new_value).strftime('%d/%m/%Y') if new_value else 'Non défini'
                    elif unit_type == 'jours':
                        old_str = f"{old_value} jours"
                        new_str = f"{new_value} jours"
                    else:
                        old_str = str(old_value)
                        new_str = str(new_value)
                    
                    changes.append(f"- {label} : {old_str} → {new_str}")

            # Suivre les modifications des demandes de prix
            if 'purchase_order_line_ids' in vals:
                old_po_lines = line.purchase_order_line_ids
                res = super(ReplenPlanTrackingLine, self).write(vals)
                new_po_lines = line.purchase_order_line_ids
                
                # Identifier les nouvelles lignes ajoutées
                added_lines = new_po_lines - old_po_lines
                for po_line in added_lines:
                    line.tracking_id.message_post(
                        body=f"""Nouvelle demande de prix pour le composant <b>{line.product_id.name}</b>:<br/>
                              - Commande: {po_line.order_id.name}<br/>
                              - Quantité: {po_line.product_qty} {po_line.product_uom.name}<br/>
                              - Prix unitaire: {po_line.price_unit} {po_line.order_id.currency_id.symbol}""",
                        message_type='notification',
                        subtype_xmlid='mail.mt_note'
                    )
                
                # Identifier les lignes supprimées
                removed_lines = old_po_lines - new_po_lines
                for po_line in removed_lines:
                    line.tracking_id.message_post(
                        body=f"Suppression de la demande de prix {po_line.order_id.name} pour le composant <b>{line.product_id.name}</b>",
                        message_type='notification',
                        subtype_xmlid='mail.mt_note'
                    )
            else:
                res = super(ReplenPlanTrackingLine, self).write(vals)

            # Si des changements ont été détectés, les enregistrer dans le chatter
            if changes:
                line.tracking_id.message_post(
                    body=f"""Modification des valeurs pour le composant <b>{line.product_id.name}</b>:<br/>
                          {'<br/>'.join(changes)}""",
                    message_type='notification',
                    subtype_xmlid='mail.mt_note'
                )

        return res

    @api.depends('tracking_id.name', 'product_id.name')
    def _compute_display_name(self):
        for record in self:
            if record.tracking_id and record.product_id:
                record.display_name = f"{record.tracking_id.name} - {record.product_id.name}"
            else:
                record.display_name = "Nouveau"

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def button_confirm(self):
        res = super(PurchaseOrder, self).button_confirm()
        for order in self:
            tracking_lines = self.env['replen.plan.tracking.line'].search([
                ('purchase_order_line_ids', 'in', order.order_line.ids)
            ])
            for tracking_line in tracking_lines:
                # Forcer le recalcul de l'état
                tracking_line._compute_state()
                # Mettre à jour les valeurs
                tracking_line.update_from_purchase_order_line(order.order_line.filtered(
                    lambda l: l.id in tracking_line.purchase_order_line_ids.ids
                )[0])
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

class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    def unlink(self):
        """Surcharge de la méthode unlink pour mettre à jour les lignes de suivi lors de la suppression"""
        # Récupérer les lignes de suivi avant la suppression
        tracking_lines = self.env['replen.plan.tracking.line'].search([
            ('purchase_order_line_ids', 'in', self.ids)
        ])
        
        # Supprimer la ligne de commande
        res = super(PurchaseOrderLine, self).unlink()
        
        # Mettre à jour les lignes de suivi
        for tracking_line in tracking_lines:
            remaining_lines = tracking_line.purchase_order_line_ids.filtered(lambda l: l.id not in self.ids)
            if not remaining_lines:
                # Si plus aucune ligne de commande, réinitialiser la ligne de suivi
                tracking_line.reset_tracking_line()
            else:
                # Recalculer avec les lignes restantes
                quantity_to_supply = sum(line.product_qty for line in remaining_lines)
                total_price = sum(line.price_unit * line.product_qty for line in remaining_lines)
                tracking_line.write({
                    'quantity_to_supply': quantity_to_supply,
                    'total_price': total_price,
                })
        
        return res

    def write(self, vals):
        """Surcharge de la méthode write pour mettre à jour les lignes de suivi"""
        res = super(PurchaseOrderLine, self).write(vals)
        
        # Si le prix, la quantité ou la date planifiée ont été modifiés
        if any(field in vals for field in ['price_unit', 'product_qty', 'date_planned']):
            tracking_lines = self.env['replen.plan.tracking.line'].search([
                ('purchase_order_line_ids', 'in', self.ids)
            ])
            for tracking_line in tracking_lines:
                tracking_line.update_from_purchase_order_line(self)
        
        return res 