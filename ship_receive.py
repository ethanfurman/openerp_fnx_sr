# -*- coding: utf-8 -*-

from itertools import groupby
from openerp import netsvc
from openerp import tools
from openerp.osv import fields, osv
from openerp.tools import float_compare, DEFAULT_SERVER_DATETIME_FORMAT
from openerp.tools.translate import _
from VSS.dbf import Date, DateTime, Time
import logging
import openerp.addons.decimal_precision as dp

_logger = logging.getLogger(__name__)

"""
class fnx_sr_bay(osv.Model):
    _name = 'fnx.sr.bays'
    _description = 'Loading Bays'
    _columns = {
        'name' : fields.char('Unique name', size=64),
        'location' : fields.char('Location', size=128),
        'description': fields.text('Description'),
        }
    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'name must be unique!'),
        ]
fnx_sr_bay()

class fnx_sr_appointment(osv.Model):

    def _calc_time(self, cr, uid, ids, field_name, arg, context=None):
        pass

    def _check_overlap(self, cr, uid, ids, context=None):
        pass

    _name = 'fnx.sr.appointment'
    _description = 'Appointments for delivery / pickup'
    _columns = {
        #'departure': fields.function(_calc_time, type='datetime', string='Vehicle Departure Time')
        'loading_bay': fields.many2one('fnx.sr.bays', 'Loading Bay'),
        }
fnx_sr_appointment()
"""

class fnx_sr_shipping(osv.Model):
    _name = 'fnx.sr.shipping'
    _description = 'shipping & receiving entries'
    _inherits = {
        'res.partner': 'partner_id',
        }
    _order = 'appointment desc'
    _columns = {

        'direction': fields.selection([('incoming', 'Receiving'), ('outgoing', 'Sending')], "Type of shipment", required=True),
        'local_source_document': fields.char('Our PO or Sales #', size=32),
        'partner_source_document': fields.char('Their PO or Sales #', size=32),
        'partner_id': fields.many2one('res.partner', 'Vendor/Customer', required=True, ondelete='restrict'),

        'weight': fields.float('Weight'),
        'cartons': fields.integer('# of cartons'),
        'pallets': fields.integer('# of pallets'),
        'state': fields.selection([
            ('draft', 'Order Placed'),
            ('scheduled', 'Scheduled'),
            ('appt', 'Ready, needs Appt'),
            ('ready', 'Ready'),
            ('complete', 'Complete'),
            ('cancelled', 'Cancelled'),
            ], 'Status',
            help="Draft     --> Initial entry of order.\n"
                 "Scheduled --> Confirmed appt with carrier.\n"
                 "Ready, needs Appt --> Order is palletized, but appointment has not been confirmed.\n"
                 "Ready     --> Order has been pulled and palletized, carrier appointment has been confirmed.\n"
                 "Complete  --> Order has been shipped.\n",
            ),
        'comment': fields.text('Comments', help="Comment or instructions for this order only."),
        #'warehouse_comment': fields.text('Warehouse comment', help="Comment from FIS."),

        'carrier_id': fields.many2one('res.partner', 'Shipment Carrier'),
        'appointment': fields.datetime('Scheduled arrival time', help="Time when driver should arrive."),
        'actual': fields.datetime('Actual arrival time', help="Time that driver actually arrived."),
        'duration': fields.float('Duration (in hours)'),
        'appt_scheduled_by_id': fields.many2one('res.users', 'Scheduled by', help="Falcon employee that scheduled appointment."),
        'appt_confirmed': fields.boolean('Appointment confirmed'),
        'appt_confirmed_on': fields.datetime('Confirmed on', help="When the appointment was confirmed with the carrier"),
        #'appt_status': fields.selection([('draft', 'Tentative'), ('confirmed', 'Confirmed'), ('complete', 'Complete')], 'Appt Status',
        #    help="Tentative --> Ball-park date.\n"
        #         "Confirmed --> Definite date provided by carrier.\n"
        #         "Complete  --> Order shipped.\n"),

        }


    def create(self, cr, uid, values, context=None):
        values['state'] = 'draft'
        return super(fnx_sr_shipping, self).create(cr, uid, values, context=context)

    def sr_draft(self, cr, uid, ids, context=None):
        print "sr_draft"
        self.write(cr, uid, ids, {'state':'draft'}, context=context)

    def sr_schedule(self, cr, uid, ids, context=None):
        print "sr_schedule", uid
        current = self.browse(cr, uid, ids, context=context)[0]
        if current.appointment:
            self.write(cr, uid, ids,
                    {
                    'state': 'scheduled',
                    'appt_scheduled_by_id': uid,
                    'appt_confirmed': True,
                    'appt_confirmed_on': DateTime.now(),
                    },
                    context=context)
            return True
        return False

    def sr_appointment(self, cr, uid, ids, context=None):
        print "sr_appointment"
        self.write(cr, uid, ids, {'state':'appt'}, context=context)

    def sr_ready(self, cr, uid, ids, context=None):
        print "sr_ready"
        self.write(cr, uid, ids, {'state':'ready'}, context=context)
        return True

    def sr_complete(self, cr, uid, ids, context=None):
        print "sr_complete"
        self.write(cr, uid, ids, {'state':'complete'}, context=context)
        return True

    def sr_cancel(self, cr, uid, ids, context=None):
        print "sr_cancel"
        self.write(cr, uid, ids, {'state':'cancelled'}, context=context)
        return True

fnx_sr_shipping()

# shipment status --> Draft, Scheduled (confirmed with carrier), Completed

# order status --> Draft, Confirmed, Ready, Completed

# appt

# get order --> Draft
# spoken with carrier --> Confirmed (good date)
# invoice printed --> pallets
# pallet count == has been pulled
