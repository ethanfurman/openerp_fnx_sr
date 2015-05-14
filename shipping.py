# -*- coding: utf-8 -*-

from itertools import groupby
from openerp import netsvc
from openerp import tools
from openerp.osv import fields, osv
from openerp.tools import float_compare, DEFAULT_SERVER_DATETIME_FORMAT, detect_server_timezone
from openerp.tools.translate import _
from fnx import Date, DateTime, Time, float, get_user_timezone, all_equal
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
    _inherits = {}
    _mirrors = {'partner_id': ['warehouse_comment']}
    _order = 'appointment_date desc, appointment_time asc, state desc'
    _rec_name = 'name'
    _mail_flat_thread = False

    def _document_name_get(self, cr, uid, ids, _field, _arg, context=None):
        result = {}
        for record in self.browse(cr, uid, ids, context=context):
            result[record.id] = {'incoming':'PO ', 'outgoing':'Inv '}.get(record.direction, '') + record.local_source_document
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

    _columns = {

        'name': fields.function(_document_name_get, type='char', string='Document', store=True),
        'direction': fields.selection([('incoming', 'Receiving'), ('outgoing', 'Shipping')], "Type of shipment", required=False),
        'local_contact_id': fields.many2one('res.partner', string='Local employee', ondelete='restrict'),
        'job_title': fields.selection([('sales', 'Sales Rep:'), ('purchasing', 'Purchaser:')], 'Job Title'),
        'preposition': fields.selection([('sales', 'to '), ('purchasing', 'from ')], 'Type of order'),
        'local_source_document': fields.char('Our document', size=32),
        'partner_source_document_type': fields.selection([('sales', 'Purchase Order:'), ('purchasing', 'Sales Order:')], 'Type of order'),
        'partner_source_document': fields.char('Their document', size=32),
        'partner_id': fields.many2one('res.partner', 'Partner', required=False, ondelete='restrict'),

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
        }

    _sql_constraints = [ ('lsd_unique', 'unique(local_source_document)', 'Already have that source document in the system') ]


    def create(self, cr, uid, values, context=None):
        if context == None:
            context = {}
        if 'state' in values:
            values['state'] = values['state'].lower()
        res_partner = self.pool.get('res.partner')
        res_users = self.pool.get('res.users')
        if 'carrier_id' not in values or not values['carrier_id']:
            values['carrier_id'] = res_partner.search(cr, uid, [('xml_id','=','99'),('module','=','F27')])[0]
        context['mail_create_nolog'] = True
        context['mail_create_nosubscribe'] = True
        partner_id = values.get('partner_id')
        partner_follower_ids = []
        user_follower_ids = []
        if partner_id is not None:
            partner = res_partner.browse(cr, uid, values['partner_id'])
            # partner_follower_ids dance is so we don't include Administrator
            partner_follower_ids = [p.id for p in partner.message_follower_ids]
            user_follower_ids = res_users.search(cr, uid, [('partner_id','in',partner_follower_ids),('id','!=',1)])
            user_follower_records = res_users.browse(cr, uid, user_follower_ids)
            partner_follower_ids = [u.partner_id.id for u in user_follower_records]
        real_id = values.pop('login_id', None)
        real_name = None
        direction = values.get('direction')
        if direction is None or partner_id is None:
            body = 'Order created.'
        else:
            direction = DIRECTION[direction].title()
            body = '%s order %s %s created.' % (
                    direction,
                    ('to', 'from')[direction=='sale'],
                    partner.name,
                    )
        follower_ids = values.pop('local_contact_ids', [])
        follower_ids.extend(user_follower_ids)
        if real_id:
            values['local_contact_id'] = real_id #res_users.browse(cr, uid, real_id, context=context)
            follower_ids.append(real_id)
            real_name = res_users.browse(cr, uid, real_id, context=context).partner_id.name
            if direction is None or partner_id is None:
                body = '%s submitted order.' % real_name
            else:
                body = '%s submitted %s order %s %s %s' % (
                        real_name,
                        direction,
                        local_source_document,
                        ('to', 'for')[direction=='sale'],
                        partner.name,
                        )
        if 'appointment_date' in values:
            try:
                appt = Date.fromymd(values['appointment_date'])
            except ValueError:
                appt = Date.fromymd(values['appointment_date'][:-2] + '01')
                appt = appt.replace(delta_month=1)
                values['appointment_date'] = appt.ymd()
        new_id = super(fnx_sr_shipping, self).create(cr, uid, values, context=context)
        if partner_follower_ids:
            self.message_post(cr, uid, new_id, body=body, partner_ids=partner_follower_ids, subtype='mt_comment', context=context)
        else:
            self.message_post(cr, uid, new_id, body=body, context=context)
        if follower_ids:
            self.message_subscribe_users(cr, uid, [new_id], user_ids=follower_ids, context=context)
        return new_id

    def write(self, cr, uid, id, values, context=None):
        if context is None:
            context = {}
        context['mail_create_nolog'] = True
        context['mail_create_nosubscribe'] = True
        if 'state' in values:
            values['state'] = values['state'].lower()
        state = None
        follower_ids = values.pop('local_contact_ids', [])
        login_id = values.pop('login_id', None)
        real_name = None
        if login_id:
            res_users = self.pool.get('res.users')
            partner = res_users.browse(cr, uid, login_id, context=context).partner_id
            values['local_contact_id'] = partner.id
            follower_ids.append(login_id)
        if not context.pop('from_workflow', False):
            state = values.pop('state', None)
        result = super(fnx_sr_shipping, self).write(cr, uid, id, values, context=context)
        if 'appointment_time' in values:
            self.sr_schedule(cr, uid, id, context=context)
        if state is not None:
            wf = self.WORKFLOW[state]
            wf(self, cr, uid, id, context=context)
        if follower_ids:
            self.message_subscribe_users(cr, uid, id, user_ids=follower_ids, context=context)
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
        if self.write(cr, uid, ids, values, context=context):
            if override:
                context['mail_create_nosubscribe'] = True
                self.message_post(cr, uid, ids, body="Reset to draft", context=context)
            return True
        return False

    def sr_schedule(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
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
                for id in ids:
                    self.message_post(cr, uid, id, body=body, context=context)
                return True
        return False

    def sr_appointment(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
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
            for id in ids:
                self.message_post(cr, uid, id, body=body, context=context)
            return True
        return False

    def sr_ready(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
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
            for id in ids:
                self.message_post(cr, uid, id, body=body, context=context)
            return True
        return False

    def sr_checkin(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        if len(ids) > 1:
            # check all have the same carrier
            records = self.browse(cr, uid, ids, context=context)
            carrier_ids = [r.carrier_id.id for r in records]
            if not all_equal(carrier_ids):
                raise osv.except_osv('Error', 'Not all carriers are the same, unable to process')
        context['from_workflow'] = True
        override = context.get('manager_override')
        current = self.browse(cr, uid, ids, context=context)[0]
        values = {
                'state':'checked_in',
                'check_in': current.check_in or DateTime.now(),
                }
        body = 'Driver checked in'
        if override:
            values['check_out'] = False
            body = 'Reset to Driver checked in.'
        if self.write(cr, uid, ids, values, context=context):
            context['mail_create_nosubscribe'] = True
            for id in ids:
                self.message_post(cr, uid, id, body=body, context=context)
            return True
        return False

    def sr_complete(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        if len(ids) > 1:
            # check all have the same carrier
            records = self.browse(cr, uid, ids, context=context)
            carrier_ids = [r.carrier_id.id for r in records]
            if not all_equal(carrier_ids):
                raise osv.except_osv('Error', 'Not all carriers are the same, unable to process')
        context['from_workflow'] = True
        override = context.get('manager_override')
        order_update = context.get('order_update')
        values = {'state':'complete'}
        if not order_update:
            values['check_out'] = DateTime.now()
            body = 'Driver checked out'
        for id in ids:
            current = self.browse(cr, uid, id, context=context)
            if override:
                values['check_out'] = current.check_out or False
                body = 'Reset to Complete.'
            if self.write(cr, uid, id, values, context=context):
                context['mail_create_nosubscribe'] = True
                followers = self._get_followers(cr, uid, [id], None, None, context=context)[id]['message_follower_ids']
                if not order_update:
                    self.message_post(cr, uid, id, body=body, context=context)
                if current.direction == 'incoming':
                    message = 'Complete:  received from %s.' % current.partner_id.name
                else:
                    message = 'Complete:  shipped to %s.' % current.partner_id.name
                self.message_post(cr, uid, id, body=message, subtype='mt_comment', partner_ids=followers, context=context)
        return True

    def sr_cancel(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        context['from_workflow'] = True
        if self.write(cr, uid, ids, {'state':'cancelled'}, context=context):
            context['mail_create_nosubscribe'] = True
            for id in ids:
                self.message_post(cr, uid, id, body='Order cancelled.', context=context)
            return True
        return False

    def search(self, cr, user, args=None, offset=0, limit=None, order=None, context=None, count=False):
        # 2013 08 12  (yyyy mm dd)
        new_args = []
        for arg in args:
            if not isinstance(arg, list) or arg[0] != 'date' or arg[2] not in ['THIS_WEEK', 'LAST_WEEK', 'THIS_MONTH', 'LAST_MONTH']:
                new_args.append(arg)
                continue
            today = Date.today()
            period = arg[2]
            if period == 'THIS_WEEK':
                start = today.replace(day=RelativeDay.LAST_MONDAY)
                stop = start.replace(delta_day=6)
            elif period == 'LAST_WEEK':
                start = today.replace(day=RelativeDay.LAST_MONDAY, delta_day=-7)
                stop = start.replace(delta_day=6)
            elif period == 'THIS_MONTH':
                start = today.replace(day=1)
                stop = start.replace(delta_month=1, delta_day=-1)
            elif period == 'LAST_MONTH':
                start = today.replace(day=1, delta_month=-1)
                stop = start.replace(delta_month=1, delta_day=-1)
            else:
                raise ValueError("forgot to update something! (period is %r)" % (arg[2],))
            op = arg[1]
            if arg[1] in ('=', 'in'):
                op = '&'
                first = '>='
                last = '<='
            elif arg[1] in ('!=', 'not in'):
                op = '|'
                first = '<'
                last = '>'
            if op != arg[1]:
                new_args.append(op)
                new_args.append(['date', first, start.strftime('%Y-%m-%d')])
                new_args.append(['date', last, stop.strftime('%Y-%m-%d')])
            elif '<' in op:
                new_args.append(['date', op, start.strftime('%Y-%m-%d')])
            elif '>' in op:
                new_args.append(['date', op, last.strftime('%Y-%m-%d')])
            else:
                raise ValueError('unable to process domain: %r' % arg)
        return super(fnx_sr_shipping, self).search(cr, user, args=new_args, offset=offset, limit=limit, order=order, context=context, count=count)

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

class fnx_sr_shipping_schedule_appt(osv.osv_memory):
    _name = 'fnx.sr.shipping.schedule_appt'
    _description = 'schedule an appt for order pickup/delivery'

    def get_carrier(self, cr, uid, context=None):
        if context is None:
            return False
        order_ids = context.get('active_ids')
        if order_ids is None:
            return False
        sr = self.pool.get('fnx.sr.shipping')
        records = sr.browse(cr, uid, order_ids, context=context)
        shipper = False
        for rec in records:
            if not rec.carrier_id:
                continue
            elif not rec.carrier_id.name.replace('_',''):
                continue
            shipper = rec.carrier_id.id
            break
        return shipper

    _columns = {
        'appointment_date' : fields.date('Date'),
        'appointment_time' : fields.float('Time'),
        'carrier_id': fields.many2one('res.partner', 'Shipper', domain=[('is_carrier','=',True)]),
        }

    _defaults = {
        'carrier_id': get_carrier,
        }

    def set_appt(self, cr, uid, ids, context=None):
        if context is None:
            return False
        if len(ids) > 1:
            raise ValueError("Can only handle one id at a time")
        order_ids = context.get('active_ids')
        if order_ids is None:
            return False
        sr = self.pool.get('fnx.sr.shipping')
        record = self.browse(cr, uid, ids[0], context=context)
        values = {}
        values['appointment_date'] = record.appointment_date
        values['appointment_time'] = record.appointment_time
        values['carrier_id'] = record.carrier_id.id
        return sr.write(cr, uid, order_ids, values, context=context)
fnx_sr_shipping_schedule_appt()

class fnx_sr_shipping_checkin(osv.osv_memory):
    _name = 'fnx.sr.shipping.checkin'
    _description = 'shipping & receiving driver checkin'

    def more_checkin(self, cr, uid, ids, context=None):
        if context is None:
            return False
        order_ids = context['active_ids']
        sr = self.pool.get('fnx.sr.shipping')
        return sr.sr_checkin(cr, uid, order_ids, context=context)
fnx_sr_shipping_checkin()

class fnx_sr_shipping_checkout(osv.osv_memory):
    _name = 'fnx.sr.shipping.checkout'
    _description = 'shipping & receiving driver checkout'

    def more_checkout(self, cr, uid, ids, context=None):
        if context is None:
            return False
        order_ids = context['active_ids']
        sr = self.pool.get('fnx.sr.shipping')
        return sr.sr_complete(cr, uid, order_ids, context=context)
fnx_sr_shipping_checkout()


# get order --> Draft
# spoken with carrier --> Confirmed (good date)
# invoice printed --> pallets
# pallet count == has been pulled
