# -*- coding: utf-8 -*-

from collections import OrderedDict
from itertools import groupby
from openerp import netsvc, tools, SUPERUSER_ID
from openerp.osv import fields, osv
from openerp.osv.osv import except_osv as ERPError
from openerp.tools import float_compare, DEFAULT_SERVER_DATETIME_FORMAT, detect_server_timezone
from openerp.tools.translate import _
from fnx import Date, DateTime, Time, float, all_equal
from fnx.oe import get_user_timezone, Proposed
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
                store={'fnx.sr.shipping': (_calc_duration, ['check_in', 'check_out'], 10)}),
        'appt_scheduled_by_id': fields.many2one('res.users', 'Scheduled by', help="Falcon employee that scheduled appointment."),
        'appt_confirmed': fields.boolean('Appointment confirmed'),
        'appt_confirmed_on': fields.datetime('Confirmed on', help="When the appointment was confirmed with the carrier"),
        'check_in': fields.datetime('Driver checked in at',),
        'check_out': fields.datetime('Driver checked out at'),
        }

    _sql_constraints = [ ('lsd_unique', 'unique(local_source_document)', 'Already have that source document in the system') ]


    def create(self, cr, uid, values, context=None):
        print '!!! fnx_sr_shipping.create'
        ctx = (context or {}).copy()
        if 'state' in values:
            values['state'] = values['state'].lower()
        res_partner = self.pool.get('res.partner')
        res_users = self.pool.get('res.users')
        if 'carrier_id' not in values or not values['carrier_id']:
            values['carrier_id'] = res_partner.search(cr, uid, [('xml_id','=','99'),('module','=','F27')])[0]
        # ctx['mail_create_nolog'] = True
        # ctx['mail_create_nosubscribe'] = True
        partner_id = values.get('partner_id')
        # partner_follower_ids = []
        # user_follower_ids = []
        if partner_id is not None:
            partner = res_partner.browse(cr, uid, values['partner_id'])
        #     # partner_follower_ids dance is so we don't include Administrator
        #     partner_follower_ids = [p.id for p in partner.message_follower_ids]
        #     user_follower_ids = res_users.search(cr, uid, [('partner_id','in',partner_follower_ids),('id','!=',1)])
        #     user_follower_records = res_users.browse(cr, uid, user_follower_ids)
        #     partner_follower_ids = [u.partner_id.id for u in user_follower_records]
        real_id = values.pop('login_id', None)
        real_name = None
        direction = values.get('direction')
        if direction is None or partner_id is None:
            # body = 'Order created.'
            pass
        else:
            direction = DIRECTION[direction].title()
            # body = '%s order %s %s created.' % (
            #         direction,
            #         ('to', 'from')[direction=='sale'],
            #         partner.name,
            #         )
        follower_user_ids = values.pop('message_follower_user_ids', [])
        # follower_ids.extend(user_follower_ids)
        if real_id:
            values['local_contact_id'] = real_id #res_users.browse(cr, uid, real_id, context=context)
            follower_user_ids.append(real_id)
            real_name = res_users.browse(cr, uid, real_id, context=ctx).partner_id.name
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
        values['message_follower_user_ids'] = follower_user_ids
        if 'appointment_date' in values:
            try:
                appt = Date.fromymd(values['appointment_date'])
            except ValueError:
                appt = Date.fromymd(values['appointment_date'][:-2] + '01')
                appt = appt.replace(delta_month=1)
                values['appointment_date'] = appt.ymd()
        return super(fnx_sr_shipping, self).create(cr, uid, values, context=ctx)
        # if partner_follower_ids:
        #     self.message_post(cr, uid, new_id, body=body, partner_ids=partner_follower_ids, subtype='mt_comment', context=context)
        # else:
        # self.message_post(cr, uid, new_id, body=body, subtype='fnx_sr.mt_ship_rec_draft', context=context)
        # if follower_ids:
        #     self.message_subscribe_users(cr, uid, [new_id], user_ids=follower_ids, context=context)
        # return new_id

    def write(self, cr, uid, ids, values, context=None):
        print '###\n##\n#\n#'
        print '# shipping.write:', ids, values
        print '#'
        context = (context or {}).copy()
        if isinstance(ids, (int, long)):
            ids = [ids]
        follower_user_ids = values.pop('message_follower_user_ids', [])
        login_id = values.pop('login_id', None)
        if login_id:
            res_users = self.pool.get('res.users')
            partner = res_users.browse(cr, uid, login_id, context=context).partner_id
            values['local_contact_id'] = partner.id
            follower_user_ids.append(login_id)
        if follower_user_ids:
            values['follower_user_ids'] = follower_user_ids
        if ids and ('state' not in values or values['state'] == 'uncancel'):
            print '# checking records\n#'
            for record in self.browse(cr, SUPERUSER_ID, ids, context=context):
                # calculate the current state based on the data changes
                # 
                vals = values.copy()
                state = 'draft'
                old_state = record.state
                uncancel = values.get('state') == 'uncancel'
                print '# old state:', old_state
                if old_state == 'cancelled' and not uncancel:
                    raise ERPError('Invalid Operation', 'This order has been cancelled.')
                if uncancel:
                    del vals['state']
                proposed = Proposed(self, cr, values, record)
                # appt -> scheduled
                if proposed.appointment and proposed.pallets:
                    state = 'ready'
                # checkin -> loading
                if proposed.check_in:
                    state = 'loading'
                    print '# new state:', state
                # -> transit (not implemented)
                # checkout -> complete
                if proposed.check_out:
                    state = 'complete'
                    print '# new state:', state
                # -> cancelled (doesn't happen here)
                # 
                if not super(fnx_sr_shipping, self).write(cr, uid, [record.id], vals, context=context):
                    return False
                if state != old_state:
                    if not super(fnx_sr_shipping, self).write(cr, uid, [record.id], {'state': state}, context=context):
                        return False
            print '#\n##\n###'
            return True
        print '#\n##\n###'
        return super(fnx_sr_shipping, self).write(cr, uid, ids, values, context=context)

    def onchange_appointment(self, cr, uid, ids, appt_date, appt_time, context=None):
        value = {'appointment': False}
        res = {'value': value}
        # XXX: change this hardcoded time zone after all users verified to not be in UTC
        # and res.users no longer has UTC as the default time zone
        # user_tz = timezone(get_user_timezone(self, cr, uid)[uid])
        user_tz = timezone('America/Los_Angeles')
        utc = timezone('UTC')

        if not user_tz:
            return {
                    'warning': {
                        'title': 'Missing Time Zone',
                        'message': 'You must have your time zone set.\n'
                                   'Check your preferences (click on your name in the upper-right corner)',
                        },
                    }
        if appt_date and appt_time:
            dt = DateTime.combine(Date(appt_date), Time.fromfloat(appt_time)).datetime()
            dt = user_tz.normalize(user_tz.localize(dt)).astimezone(utc)
            value['appointment'] = dt.strftime(DEFAULT_SERVER_DATETIME_FORMAT)
        return res

    def button_complete(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        values = {}
        if isinstance(ids, (int, long)):
            ids = [ids]
        if len(ids) > 1:
            # check all have the same carrier
            records = self.browse(cr, uid, ids, context=ctx)
            carrier_ids = [r.carrier_id.id for r in records]
            if not all_equal(carrier_ids):
                raise osv.except_osv('Error', 'Not all carriers are the same, unable to process')
        values['check_out'] = DateTime.now()
        # values['state'] = 'complete'
        override = context.get('override', True)
        for id in ids:
            current = self.browse(cr, uid, id, context=ctx)
            if override:
                values['check_out'] = current.check_out or values['check_out']
                ctx['mail_create_nosubscribe'] = True
                ctx['message_force'] = 'Manager override:'
            self.write(cr, uid, id, values, context=ctx)
        return True

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
        return self.write(cr, uid, ids, {'state': 'uncancel', 'check_out': False}, context=ctx)

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
        values['carrier_id'] = record.carrier_id.id
        # update records in active_model
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
