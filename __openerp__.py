{
    'name': 'Fnx Shipping & Receiving',
    'version': '0.1',
    'category': 'Generic Modules',
    'description': """\
            Phoenix Shipping & Receiving.
            """,
    'author': 'Emile van Sebille',
    'maintainer': 'Emile van Sebille',
    'website': 'www.openerp.com',
    'depends': [
            'base',
            'crm',
            'fis_integration',
            'fnx',
        ],
    'js': [
        ],
    'data': [
            'security/security.xml',
            'security/ir.model.access.csv',
            'shipping_view.xml',
            'res_partner_view.xml',
        ],
    'test': [],
    'installable': True,
    'active': False,
}
