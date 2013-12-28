import logging
from osv import osv, fields

_logger = logging.getLogger(__name__)

class res_partner(osv.Model):
    "Inherits partner and link to FnxSR"
    _name = 'res.partner'
    _inherit = 'res.partner'

    _columns = {
        'shipped': fields.one2many('fnx.sr.shipping', 'partner_id', 'Shipped Orders'),
        'carried': fields.one2many('fnx.sr.shipping', 'carrier_id', 'Shipped Orders'),
        }

res_partner()
