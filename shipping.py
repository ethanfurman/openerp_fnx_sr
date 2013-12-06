# -*- coding: utf-8 -*-

from itertools import groupby
from openerp import netsvc
from openerp import tools
from openerp.osv import fields, osv
from openerp.tools import float_compare, DEFAULT_SERVER_DATETIME_FORMAT, detect_server_timezone
from openerp.tools.translate import _
from fnx import Date, DateTime, Time, float, get_user_timezone
from pytz import timezone
import logging
import openerp.addons.decimal_precision as dp
import time

_logger = logging.getLogger(__name__)


DIRECTION = {
    'incoming' : 'purchase',
    'outgoing' : 'sale',
    }

utc = timezone('UTC')

class fnx_sr_shipping(osv.Model):
    _name = 'fnx.sr.shipping'
    _description = 'shipping & receiving'
    _inherit = ['mail.thread']
    _order = 'state desc, appointment_date desc, appointment_time asc'
    _rec_name = 'name'
    _mail_flat_thread = False

    def _document_name_get(self, cr, uid, ids, _field, _arg, context=None):
        result = {}
        for record in self.browse(cr, uid, ids, context=context):
            result[record.id] = {'incoming':'PO ', 'outgoing':'Inv '}[record.direction] + record.local_source_document
        return result

    def _calc_appointment(self, cr, uid, ids, _field=None, _arg=None, context=None):
        if context is None:
            context = {}
        result = {}
        user_tz = get_user_timezone(self, cr, uid)[uid]
        if user_tz:
            user_tz = timezone(user_tz)
        for id in ids:
            result[id] = False
            record = self.browse(cr, uid, id, context=context)
            date = record.appointment_date
            time = record.appointment_time or 0.0
            if date:
                if not user_tz:
                    continue
                dt = DateTime.combine(Date(date), Time.fromfloat(time)).datetime()
                dt = user_tz.localize(dt)
                result[id] = dt
        return result

    def _calc_duration(self, cr, uid, ids, _field=None, _arg=None, context=None):
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

    def _generate_order_by(self, order_spec, query):
        "correctly orders state field if state is in query"
        order_by = super(fnx_sr_shipping, self)._generate_order_by(order_spec, query)
        if order_spec and 'state ' in order_spec:
            state_column = self._columns['state']
            state_order = 'CASE '
            for i, state in enumerate(state_column.selection):
                state_order += "WHEN %s.state='%s' THEN %i " % (self._table, state[0], i)
            state_order += 'END '
            order_by = order_by.replace('"%s"."state" ' % self._table, state_order)
        return order_by

    def _res_partner_warehouse_comment(self, cr, uid, ids, field, _arg, context=None):
        if not ids:
            return {}
        result = {}
        res_partner = self.pool.get('res.partner')
        for id in ids:
            record = self.browse(cr, uid, id, context=context)
            result[id] = record.partner_id.warehouse_comment
        return result

    _columns = {

        'name': fields.function(_document_name_get, type='char', string='Document', store=True),
        'direction': fields.selection([('incoming', 'Receiving'), ('outgoing', 'Shipping')], "Type of shipment", required=True),
        'local_contact_id': fields.many2one('res.users', string='Local employee', ondelete='restrict'),
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

        'carrier_id': fields.many2one('res.partner', 'Shipper', domain=[('is_carrier','=',True)]),
        'appointment_date': fields.date('Appointment date', help="Date when driver should arrive."),
        'appointment_time': fields.float('Appointment time', help="Time when driver should arrive."),
        'appointment': fields.function(_calc_appointment, type='datetime', string='Appointment',
                store={'fnx.sr.shipping': (_calc_appointment, ['appointment_date', 'appointment_time'], 10)}),
        'duration': fields.function(_calc_duration, type='float', string='Duration (in hours)',
                store={'fnx.sr.shipping': (_calc_duration, ['check_in', 'check_out'], 10)}),
        'appt_scheduled_by_id': fields.many2one('res.users', 'Scheduled by', help="Falcon employee that scheduled appointment."),
        'appt_confirmed': fields.boolean('Appointment confirmed'),
        'appt_confirmed_on': fields.datetime('Confirmed on', help="When the appointment was confirmed with the carrier"),
        'check_in': fields.datetime('Driver checked in at',),
        'check_out': fields.datetime('Driver checked out at'),
        'is_manager': fields.function(_current_user_is_manager, type='boolean'),
        'warehouse_comment': fields.function(_res_partner_warehouse_comment, type='char')
        }


    def create(self, cr, uid, values, context=None):
        if context == None:
            context = {}
        context['mail_create_nolog'] = True
        context['mail_create_nosubscribe'] = True
        res_users = self.pool.get('res.users')
        real_id = values.pop('real_id', None)
        real_name = None
        direction = DIRECTION[values['direction']].title()
        body = '%s order created' % direction
        follower_ids = values.pop('local_contact_ids', [])
        if real_id:
            values['local_contact_id'] = real_id #res_users.browse(cr, uid, real_id, context=context)
            follower_ids.append(real_id)
            real_name = res_users.browse(cr, uid, real_id, context=context).partner_id.name
            body = 'Order received from %s %s' % ({'Purchase':'Purchaser', 'Sale':'Sales Rep'}[direction], real_name)
        new_id = super(fnx_sr_shipping, self).create(cr, uid, values, context=context)
        self.message_post(cr, uid, new_id, body=body, context=context)
        if follower_ids:
            self.message_subscribe_users(cr, uid, [new_id], user_ids=follower_ids, context=context)
        return new_id

    def write(self, cr, uid, id, values, context=None):
        if context is None:
            context = {}
        state = None
        if not context.pop('from_workflow', False):
            state = values.pop('state', None)
        result = super(fnx_sr_shipping, self).write(cr, uid, id, values, context=context)
        if 'appointment_time' in values:
            self.sr_schedule(cr, uid, id, context=context)
        if state is not None:
            wf = self.WORKFLOW[state]
            wf(self, cr, uid, id, context=context)
        return result

    def sr_draft(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        context['from_workflow'] = True
        override = context.get('manager_override')
        values = {'state':'draft'}
        if override:
            values['appointment_time'] = 0.0
            values['appt_confirmed'] = False
            values['appt_confirmed_on'] = False
            values['appt_scheduled_by_id'] = False
            values['check_in'] = False
            values['check_out'] = False
        if self.write(cr, uid, ids, {'state':'draft'}, context=context):
            if override:
                context['mail_create_nosubscribe'] = True
                self.message_post(cr, uid, ids, body="Reset to draft", context=context)
            return True
        return False

    def sr_schedule(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        context['from_workflow'] = True
        user_tz = get_user_timezone(self, cr, uid)[uid]
        override = context.get('manager_override')
        current = self.browse(cr, uid, ids, context=context)[0]
        if current.appointment_date and current.appointment_time:
            values = {
                    'appt_scheduled_by_id': uid,
                    'appt_confirmed': True,
                    'appt_confirmed_on': DateTime.now(),
                    }
            if override:
                values['state'] = 'scheduled'
            elif current.state == 'draft':
                values['state'] = 'scheduled'
            elif current.state == 'appt':
                values['state'] = 'ready'
            dt = utc.localize(DateTime(current.appointment).datetime())
            if user_tz:
                dt = dt.astimezone(timezone(user_tz))
            body = 'Scheduled for %s' % (dt.strftime('%Y-%m-%d %H:%M %Z'), )
            if override:
                values['check_in'] = False
                values['check_out'] = False
                values['appt_confirmed_on'] = current.appt_confirmed_on
                body = 'Reset to scheduled.'
            if self.write(cr, uid, ids, values, context=context):
                context['mail_create_nosubscribe'] = True
                self.message_post(cr, uid, ids, body=body, context=context)
                return True
        return False

    def sr_appointment(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        context['from_workflow'] = True
        override = context.get('manager_override')
        values = {'state':'appt'}
        body = 'Order pulled.'
        if override:
            values['appointment_time'] = 0.0
            values['appt_confirmed'] = False
            values['appt_confirmed_on'] = False
            values['appt_scheduled_by_id'] = False
            values['check_in'] = False
            values['check_out'] = False
            body = 'Appointment cancelled.'
        if self.write(cr, uid, ids, values, context=context):
            context['mail_create_nosubscribe'] = True
            self.message_post(cr, uid, ids, body=body, context=context)
            return True
        return False

    def sr_ready(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        context['from_workflow'] = True
        override = context.get('manager_override')
        values = {'state':'ready'}
        body = 'Order pulled.'
        current = self.browse(cr, uid, ids, context=context)[0]
        if not (current.appointment_date and current.appointment_time):
            return False
        if override:
            values['check_in'] = False
            values['check_out'] = False
            body = 'Reset to Ready.'
        if self.write(cr, uid, ids, values, context=context):
            context['mail_create_nosubscribe'] = True
            self.message_post(cr, uid, ids, body=body, context=context)
            return True
        return False

    def sr_checkin(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        context['from_workflow'] = True
        override = context.get('manager_override')
        current = self.browse(cr, uid, ids, context=context)[0]
        values = {
                'state':'checked_in',
                'check_in': current.check_in or DateTime.now(),
                }
        body = 'Driver checked in at %s' % values['check_in']
        if override:
            values['check_out'] = False
            body = 'Reset to Driver checked in.'
        if self.write(cr, uid, ids, values, context=context):
            context['mail_create_nosubscribe'] = True
            self.message_post(cr, uid, ids, body=body, context=context)
            return True
        return False

    def sr_complete(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        context['from_workflow'] = True
        override = context.get('manager_override')
        order_update = context.get('order_update')
        values = {'state':'complete'}
        if not order_update:
            values['check_out'] = DateTime.now()
            body = 'Driver checked out at %s' % values['check_out']
        current = self.browse(cr, uid, ids, context=context)[0]
        if override:
            values['check_out'] = current.check_out or False
            body = 'Reset to Complete.'
        if self.write(cr, uid, ids, values, context=context):
            context['mail_create_nosubscribe'] = True
            followers = self._get_followers(cr, uid, ids, None, None, context=context)[ids[0]]['message_follower_ids']
            if not order_update:
                self.message_post(cr, uid, ids, body=body, context=context)
            self.message_post(cr, uid, ids, body='%s complete.' % current.name, subtype='mt_comment', partner_ids=followers, context=context)
            return True
        return False

    def sr_cancel(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        context['from_workflow'] = True
        if self.write(cr, uid, ids, {'state':'cancelled'}, context=context):
            context['mail_create_nosubscribe'] = True
            self.message_post(cr, uid, ids, body='Order cancelled.', context=context)
            return True
        return False

    WORKFLOW = {
        'draft': sr_draft,
        'scheduled': sr_schedule,
        'appt': sr_appointment,
        'ready': sr_ready,
        'checked_in': sr_checkin,
        'complete': sr_complete,
        'cancelled': sr_cancel,
        }
fnx_sr_shipping()

# shipment status --> Draft, Scheduled (confirmed with carrier), Completed

# order status --> Draft, Confirmed, Ready, Completed

# appt

# get order --> Draft
# spoken with carrier --> Confirmed (good date)
# invoice printed --> pallets
# pallet count == has been pulled
