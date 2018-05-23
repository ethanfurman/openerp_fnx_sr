# -*- coding: utf-8 -*-
# imports
from collections import OrderedDict
from dbf import Date, DateTime, RelativeDay
from openerp import SUPERUSER_ID
from openerp.osv import fields, osv
from openerp.exceptions import ERPError
from VSS.utils import float, all_equal
from fnx import construct_datetime
from fnx.oe import Proposed
import logging

# set up

_logger = logging.getLogger(__name__)


DIRECTION = {
    'incoming' : 'purchase',
    'outgoing' : 'sale',
    }

#tables

class fnx_sr_shipping(osv.Model):
    _name = 'fnx.sr.shipping'
    _description = 'shipping & receiving'
    _inherit = ['mail.thread']
    _inherits = {}
    _mirrors = {'partner_id': ['warehouse_comment']}
    _order = 'appointment_date desc, appointment_time asc, state desc'
    _rec_name = 'name'
    _mail_flat_thread = False

    _track = OrderedDict()
    _track['appointment'] = {
            'fnx_sr.mt_ship_rec_event_scheduled': lambda s, c, u, r, ctx: 'appointment' in r and r['appointment'],
            }
    _track['pallets'] = {
            'fnx_sr.mt_ship_rec_event_picked': lambda s, c, u, r, ctx: 'pallets' in r and r['pallets'],
            }
    state = OrderedDict()
    state['fnx_sr.mt_ship_rec_draft'] = lambda s, c, u, r, ctx: r['state'] == 'draft'
    state['fnx_sr.mt_ship_rec_ready'] = lambda s, c, u, r, ctx: r['state'] == 'ready'
    state['fnx_sr.mt_ship_rec_loading_unloading'] = lambda s, c, u, r, ctx: r['state'] == 'loading'
    state['fnx_sr.mt_ship_rec_en_route'] = lambda s, c, u, r, ctx: r['state'] == 'transit'
    state['fnx_sr.mt_ship_rec_complete'] = lambda s, c, u, r, ctx: r['state'] == 'complete'
    state['fnx_sr.mt_ship_rec_cancelled'] = lambda s, c, u, r, ctx: r['state'] == 'cancelled'
    _track['state'] = state
    del state

    # get order --> Draft
    # spoken with carrier --> Confirmed (good date)
    # invoice printed --> pallets
    # pallet count == has been pulled

    def _document_name_get(self, cr, uid, ids, _field, _arg, context=None):
        result = {}
        for record in self.browse(cr, uid, ids, context=context):
            result[record.id] = {'incoming':'PO ', 'outgoing':'Inv '}.get(record.direction, '') + record.local_source_document
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

    _columns = {

        'state': fields.selection([
            ('draft', 'Order Placed'),
            ('ready', 'Ready'),
            ('loading', 'Loading/Unloading'),
            ('transit', 'En Route'),
            ('complete', 'Complete'),
            ('cancelled', 'Cancelled'),
            ], 'Status',
            sort_order='definition',
            help="Draft     --> Initial entry of order.\n"
                 "Ready     --> Order has been pulled and palletized, carrier appointment has been confirmed.\n"
                 "Loading/Unloading --> Order is being transferred to/from the delivery truck.\n"
                 "En Route  --> Order is travelling to destination.\n"
                 "Complete  --> Order has been shipped.\n"
                 "Cancelled --> Order was cancelled.",
            ),
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
        'comment': fields.text('Comments', help="Comment or instructions for this order only."),

        'carrier_id': fields.many2one('res.partner', 'Shipper', domain=[('is_carrier','=',True)]),
        'appointment_date': fields.date('Appointment date', help="Date when driver should arrive."),
        'appointment_time': fields.float('Appointment time', help="Time when driver should arrive."),
        'appointment': fields.datetime('Appointment', track_visibility='change_only'),
        'duration': fields.function(_calc_duration, type='float', string='Duration (in hours)',
                store={'fnx.sr.shipping': (lambda s, c, u, ids, ctx={}: ids, ['check_in', 'check_out'], 30)}),
        'appt_scheduled_by_id': fields.many2one('res.users', 'Scheduled by', help="Falcon employee that scheduled appointment."),
        'appt_confirmed': fields.boolean('Appointment confirmed'),
        'appt_confirmed_on': fields.datetime('Confirmed on', help="When the appointment was confirmed with the carrier"),
        'check_in': fields.datetime('Driver checked in at',),
        'check_out': fields.datetime('Driver checked out at'),
        }

    _sql_constraints = [ ('lsd_unique', 'unique(local_source_document)', 'Already have that source document in the system') ]


    def create(self, cr, uid, values, context=None):
        if 'state' in values:
            values['state'] = values['state'].lower()
        res_partner = self.pool.get('res.partner')
        res_users = self.pool.get('res.users')
        if 'carrier_id' not in values or not values['carrier_id']:
            values['carrier_id'] = res_partner.search(cr, uid, [('xml_id','=','99'),('module','=','F27')], context=context)[0]
        partner_id = values.get('partner_id')
        real_id = values.pop('login_id', None)
        direction = values.get('direction')
        if direction is None or partner_id is None:
            pass
        else:
            direction = DIRECTION[direction].title()
        follower_ids = values.pop('message_follower_ids', [])
        if real_id:
            real_user = res_users.browse(cr, uid, real_id, context=context)
            follower_ids.append(real_user.partner_id.id)
        if follower_ids:
            values['message_follower_ids'] = follower_ids
        if 'appointment_date' in values:
            try:
                appt = Date.fromymd(values['appointment_date'])
            except ValueError:
                appt = Date.fromymd(values['appointment_date'][:-2] + '01')
                appt = appt.replace(delta_month=1)
                values['appointment_date'] = appt.ymd()
            values['appointment'] = construct_datetime(appt, 0, context)
        return super(fnx_sr_shipping, self).create(cr, uid, values, context=context)

    def write(self, cr, uid, ids, values, context=None):
        context = (context or {}).copy()
        if 'tz' not in context:
            context['tz'] = 'UTC'
        if isinstance(ids, (int, long)):
            ids = [ids]
        follower_ids = values.pop('message_follower_ids', [])
        login_id = values.pop('login_id', None)
        if login_id:
            res_users = self.pool.get('res.users')
            partner = res_users.browse(cr, uid, login_id, context=context).partner_id
            context['tz'] = partner.tz
            values['local_contact_id'] = partner.id
            follower_ids.append(partner.id)
        if follower_ids:
            values['message_follower_ids'] = follower_ids
        if 'appointment_date' in values and 'appointment' not in values:
            # only possible from update script
            try:
                appt = Date.fromymd(values['appointment_date'])
            except ValueError:
                appt = Date.fromymd(values['appointment_date'][:-2] + '01')
                appt = appt.replace(delta_month=1)
                values['appointment_date'] = appt.ymd()
            values['appointment'] = construct_datetime(appt, values.get('appointment_time', 0), context)
        if ids and ('state' not in values or values['state'] == 'reopen'):
            for record in self.browse(cr, SUPERUSER_ID, ids, context=context):
                # calculate the current state based on the data changes
                # 
                vals = values.copy()
                state = 'draft'
                old_state = record.state
                reopen = values.get('state') == 'reopen'
                if old_state == 'cancelled' and not reopen:
                    raise ERPError('Invalid Operation', 'This order has been cancelled.')
                if reopen:
                    del vals['state']
                proposed = Proposed(self, cr, values, record, context=context)
                # appt -> scheduled
                if proposed.appointment and proposed.pallets:
                    state = 'ready'
                # checkin -> loading
                if proposed.check_in:
                    state = 'loading'
                # -> transit (not implemented)
                # checkout -> complete
                if (proposed.check_out or old_state == 'complete') and not reopen:
                    state = 'complete'
                # -> cancelled (doesn't happen here)
                # 
                if not super(fnx_sr_shipping, self).write(cr, uid, [record.id], vals, context=context):
                    return False
                if state != old_state:
                    if not super(fnx_sr_shipping, self).write(cr, uid, [record.id], {'state': state}, context=context):
                        return False
            return True
        return super(fnx_sr_shipping, self).write(cr, uid, ids, values, context=context)

    def onchange_appointment(self, cr, uid, ids, appt_date, appt_time, context=None):
        return {'value': {
                    'appointment': construct_datetime(appt_date, appt_time, context),
                    },
                    }

    def sr_checkin(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        if isinstance(ids, (int, long)):
            ids = [ids]
        if len(ids) > 1:
            # check all have the same carrier
            records = self.browse(cr, uid, ids, context=ctx)
            carrier_ids = [r.carrier_id.id for r in records]
            if not all_equal(carrier_ids):
                raise osv.except_osv('Error', 'Not all carriers are the same, unable to process')
        ctx['mail_create_nosubscribe'] = True
        values = {
                'check_in': DateTime.now(),
                }
        return self.write(cr, uid, ids, values, context=ctx)

    def sr_uncheckin(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        if isinstance(ids, (int, long)):
            ids = [ids]
        if len(ids) > 1:
            # check all have the same carrier
            records = self.browse(cr, uid, ids, context=context)
            carrier_ids = [r.carrier_id.id for r in records]
            if not all_equal(carrier_ids):
                raise osv.except_osv('Error', 'Not all carriers are the same, unable to process')
        ctx['mail_create_nosubscribe'] = True
        ctx['message_force'] = 'Ticket un-checked-in: reset to'
        values = {
                'check_in': False,
                }
        return self.write(cr, uid, ids, values, context=ctx)

    def sr_checkout(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        if isinstance(ids, (int, long)):
            ids = [ids]
        if len(ids) > 1:
            # check all have the same carrier
            records = self.browse(cr, uid, ids, context=ctx)
            carrier_ids = [r.carrier_id.id for r in records]
            if not all_equal(carrier_ids):
                raise osv.except_osv('Error', 'Not all carriers are the same, unable to process')
        values = {'check_out':  DateTime.now()}
        ctx['mail_create_nosubscribe'] = True
        if ctx.get('override', True):
            ctx['message_force'] = 'Manager override:'
        return self.write(cr, uid, ids, values, context=ctx)
    button_complete = sr_checkout

    def button_cancel(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        if isinstance(ids, (int, long)):
            ids = [ids]
        ctx['mail_create_nosubscribe'] = True
        ctx['message_force'] = 'Manager override:'
        return self.write(cr, uid, ids, {'state':'cancelled'}, context=ctx)

    def button_reopen(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        if isinstance(ids, (int, long)):
            ids = [ids]
        ctx['mail_create_nosubscribe'] = True
        ctx['message_force'] = 'Manager override: reset to '
        return self.write(cr, uid, ids, {'state': 'reopen', 'check_out': False}, context=ctx)

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
        # context contains 'active_id', 'active_ids', and 'active_model'
        if context is None:
            return False
        if len(ids) > 1:
            # should never happen
            raise ValueError("Can only handle one id at a time")
        order_ids = context.get('active_ids')
        if order_ids is None:
            return False
        # get link to active_model
        sr = self.pool.get('fnx.sr.shipping')
        # get memory model record with data
        record = self.browse(cr, uid, ids[0], context=context)
        values = {}
        # save to values dict
        values['appointment_date'] = record.appointment_date
        values['appointment_time'] = record.appointment_time
        values['appointment'] = construct_datetime(record.appointment_date, record.appointment_time, context)
        values['carrier_id'] = record.carrier_id.id
        # update records in active_model
        return sr.write(cr, uid, order_ids, values, context=context)

class fnx_sr_shipping_checkin(osv.osv_memory):
    _name = 'fnx.sr.shipping.checkin'
    _description = 'shipping & receiving driver checkin'

    def more_checkin(self, cr, uid, ids, context=None):
        if context is None:
            return False
        order_ids = context['active_ids']
        sr = self.pool.get('fnx.sr.shipping')
        return sr.sr_checkin(cr, uid, order_ids, context=context)

class fnx_sr_shipping_checkout(osv.osv_memory):
    _name = 'fnx.sr.shipping.checkout'
    _description = 'shipping & receiving driver checkout'

    def more_checkout(self, cr, uid, ids, context=None):
        if context is None:
            return False
        order_ids = context['active_ids']
        sr = self.pool.get('fnx.sr.shipping')
        return sr.sr_checkout(cr, uid, order_ids, context=context)

