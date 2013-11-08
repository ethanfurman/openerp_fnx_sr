import logging
from osv import osv, fields

class res_partner(osv.Model):
    "Inherits partner and makes the external_id visible and modifiable"
    _name = 'res.partner'
    _inherit = 'res.partner'

    _columns = {
        'is_carrier': fields.boolean('Carrier', help='This partner is used for shipping.'),
        'is_driver': fields.boolean('Driver', help='This partner is a driver for a carrier.'),
        'warehouse_comment': fields.text('Warehouse Notes'),
        }
res_partner()
