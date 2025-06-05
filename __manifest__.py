{
    'name': 'Plan de réapprovisionnement',
    'version': '15.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Gestion des plans de réapprovisionnement',
    'description': """
        Module de gestion des plans de réapprovisionnement permettant de :
        - Planifier les besoins en composants
        - Gérer les prévisions
        - Générer les demandes de prix
        - Suivre l'état d'avancement des plans
    """,
    'author': 'Votre Société',
    'website': 'https://www.votresociete.com',
    'depends': ['base', 'stock', 'purchase', 'mrp', 'sale', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'reports/replen_plan_report.xml',
        'views/menu_views.xml',
        'views/replen_plan_views.xml',
        'views/replen_plan_confirm_views.xml',
        'views/replen_plan_tracking_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'license': 'LGPL-3',
    'assets': {
        'web.assets_backend': [
            'replen_plan/static/src/js/replen_plan_list.js',
        ],
    },
}