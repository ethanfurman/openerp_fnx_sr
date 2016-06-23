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
            #'static/src/js/refresh.js',
        ],
    'data': [
            'security/fnx_sr_security.xaml',
            'security/ir.model.access.csv',
            'shipping_data.xaml',
            'shipping_view.xaml',
            'res_partner_view.xaml',
        ],
    'test': [],
    'installable': True,
    'active': False,
}
