{
    'name': 'Planificateur de réapprovisionnement',
    'version': '15.0.1.0.0',
    'category': 'Inventory/Purchase',
    'summary': 'Génération automatique des demandes de prix basée sur les prévisions de vente',
    'description': """
        Module de planification de réapprovisionnement permettant de :
        - Créer des plans de réapprovisionnement
        - Saisir des prévisions de vente
        - Générer des plans de réapprovisionnement
        - Créer automatiquement des demandes de prix
    """,
    'author': 'Odoo',
    'depends': [
        'base',
        'stock',
        'purchase',
        'mrp',
        'sale',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'views/replen_plan_views.xml',
        'views/menu_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
} 