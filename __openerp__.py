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
            'fnx',
        ],
    'js': [
        ],
    'data': [
            'security/security.xml',
            'security/ir.model.access.csv',
            'res_partner_view.xml',
            'ship_receive_view.xml',
            #'ship_receive_workflow.xml',
        ],
    'test': [],
    'installable': True,
    'active': False,
}
