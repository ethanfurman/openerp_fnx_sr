# -*- coding: utf-8 -*-

from itertools import groupby
from openerp import netsvc
from openerp import tools
from openerp.osv import fields, osv
from openerp.tools import float_compare, DEFAULT_SERVER_DATETIME_FORMAT
from openerp.tools.translate import _
from fnx import Date, DateTime, Time, float
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
    _order = 'appointment_date desc, appointment_time asc'
    _rec_name = 'local_source_document'

    def _calc_duration(self, cr, uid, ids, field, _arg, context=None):
        if not ids:
            return {}
        result = {}
        for id in ids:
            record = self.browse(cr, uid, id, context=context)
            result[id] = False
            if record.check_in and record.check_out:
                check_in = DateTime(record.check_in)
                check_out = DateTime(record.check_out)
                result[id] = float(check_out - check_in)
        return result

    def _current_user_is_manager(self, cr, uid, ids, field, _arg, context=None):
        res_users = self.pool.get('res.users')
        result= {}
        for id in ids:
            result[id] = res_users.has_group(cr, uid, 'fnx_sr.group_fnx_sr_manager')
        return result

    def _res_partner_warehouse_comment(self, cr, uid, ids, field, _arg, context=None):
        if not ids:
            return {}
        result = {}
        res_partner = self.pool.get('res.partner')
        for id in ids:
            record = self.browse(cr, uid, id, context=context)
            partner = res_partner.browse(cr, uid, record.partner_id)
            result[id] = partner.warehouse_comment
        return result

    _columns = {

        'direction': fields.selection([('incoming', 'Receiving'), ('outgoing', 'Sending')], "Type of shipment", required=True),
        'local_contact_ids': fields.many2many('res.users', 'users_shipping_rel', 'user_id', 'ship_id', string='Local employee', ondelete='restrict'),
        'job_title': fields.selection([('sales', 'Sales Rep:'), ('purchasing', 'Purchaser:')], 'Job Title'),
        'preposition': fields.selection([('sales', 'to '), ('purchasing', 'from ')], 'Type of order'),
        'local_source_document': fields.char('Our document', size=32),
        'partner_source_document_type': fields.selection([('sales', 'Purchase Order:'), ('purchasing', 'Sales Order:')], 'Type of order'),
        'partner_source_document': fields.char('Their document', size=32),
        'partner_id': fields.many2one('res.partner', 'Partner', required=True, ondelete='restrict'),

        'weight': fields.float('Weight'),
        'cartons': fields.integer('# of cartons'),
        'pallets': fields.integer('# of pallets'),
        'state': fields.selection([
            ('draft', 'Order Placed'),
            ('scheduled', 'Scheduled'),
            ('appt', 'Ready, needs Appt'),
            ('ready', 'Ready'),
            ('checked_in', 'Loading/Unloading'),
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

        'carrier_id': fields.many2one('res.partner', 'Shipper'),
        'appointment_date': fields.date('Appointment date', help="Date when driver should arrive."),
        'appointment_time': fields.float('Appointment time', help="Time when driver should arrive."),
        'duration': fields.function(_calc_duration, type='float', string='Duration (in hours)'),
        'appt_scheduled_by_id': fields.many2one('res.users', 'Scheduled by', help="Falcon employee that scheduled appointment."),
        'appt_confirmed': fields.boolean('Appointment confirmed'),
        'appt_confirmed_on': fields.datetime('Confirmed on', help="When the appointment was confirmed with the carrier"),
        'check_in': fields.datetime('Driver checked in at',),
        'check_out': fields.datetime('Driver checked out at'),
        'is_manager': fields.function(_current_user_is_manager, type='boolean'),
        'warehouse_comment': fields.function(_res_partner_warehouse_comment, type='char')
        }


    def create(self, cr, uid, values, context=None):
        values['state'] = 'draft'
        return super(fnx_sr_shipping, self).create(cr, uid, values, context=context)

    def sr_draft(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        override = context.get('manager_override')
        values = {'state':'draft'}
        if override:
            values['appointment_time'] = 0.0
            values['appt_confirmed'] = False
            values['appt_confirmed_on'] = False
            values['appt_scheduled_by_id'] = False
            values['check_in'] = False
            values['check_out'] = False
        self.write(cr, uid, ids, {'state':'draft'}, context=context)

    def sr_schedule(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        override = context.get('manager_override')
        current = self.browse(cr, uid, ids, context=context)[0]
        if current.appointment_date and current.appointment_time:
            values = {
                    'state': 'scheduled',
                    'appt_scheduled_by_id': current.appt_scheduled_by_id or uid,
                    'appt_confirmed': True,
                    'appt_confirmed_on': current.appt_confirmed_on or DateTime.now(),
                    },
            if override:
                values['check_in'] = False
                values['check_out'] = False
            self.write(cr, uid, ids, values, context=context)
            return True
        return False

    def sr_appointment(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        override = context.get('manager_override')
        values = {'state':'appt'}
        if override:
            values['appointment_time'] = 0.0
            values['appt_confirmed'] = False
            values['appt_confirmed_on'] = False
            values['appt_scheduled_by_id'] = False
            values['check_in'] = False
            values['check_out'] = False
        self.write(cr, uid, ids, values, context=context)

    def sr_ready(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        override = context.get('manager_override')
        values = {'state':'ready'}
        if override:
            values['check_in'] = False
            values['check_out'] = False
        self.write(cr, uid, ids, values, context=context)
        return True

    def sr_checkin(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        override = context.get('manager_override')
        values = {
                'state':'checked_in',
                'check_in': DateTime.now(),
                }
        if override:
            values['check_out'] = False
        self.write(cr, uid, ids, values, context=context)
        return True

    def sr_complete(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        override = context.get('manager_override')
        values = {
                'state':'complete',
                'check_out': DateTime.now(),
                }
        if override:
            current = self.browse(cr, uid, ids, context=context)[0]
            values['check_out'] = current.check_out
        self.write(cr, uid, ids, values, context=context)
        return True

    def sr_cancel(self, cr, uid, ids, context=None):
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
