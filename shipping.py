# -*- coding: utf-8 -*-
# imports
from collections import OrderedDict
from dbf.data_types import Date, DateTime, Time, RelativeDay
from openerplib.dates import str_to_datetime, local_datetime
from openerp import SUPERUSER_ID
from openerp.osv import fields, osv
from openerp.exceptions import ERPError
from openerp.tools import DEFAULT_SERVER_DATE_FORMAT, DEFAULT_SERVER_DATETIME_FORMAT
from openerp.tools.misc import OrderBy
from textwrap import dedent
import pytz
from VSS.utils import float, hrtd
import logging
import re

# set up

_logger = logging.getLogger(__name__)
UTC = pytz.timezone('UTC')

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
    # _order = 'appointment_date desc, appointment_time asc, state desc'
    _order = OrderBy(dedent("""\
            CASE WHEN state = 'draft' THEN 1 END,
            CASE WHEN state = 'ready' THEN 2 END,
            CASE WHEN state = 'loading' THEN 3 END,
            CASE WHEN state = 'transit' THEN 4 END,
            CASE WHEN state = 'partial' THEN 5 END,
            CASE WHEN state = 'complete' THEN 6 END,
            CASE WHEN state = 'cancelled' THEN 7 END,
            COALESCE(appointment_date, ship_date) DESC
            """))
    _rec_name = 'name'
    _mail_flat_thread = False

    _track = OrderedDict()
    _track['appointment'] = {
            'fnx_sr.mt_ship_rec_event_scheduled': lambda s, c, u, r, ctx: 'appointment_time' in r and r['appointment_time'],
            }
    _track['pallets'] = {
            'fnx_sr.mt_ship_rec_event_picked': lambda s, c, u, r, ctx: 'pallets' in r and r['pallets'],
            }
    state = OrderedDict()
    state['fnx_sr.mt_ship_rec_draft'] = lambda s, c, u, r, ctx: r['state'] == 'draft'
    state['fnx_sr.mt_ship_rec_ready'] = lambda s, c, u, r, ctx: r['state'] == 'ready'
    state['fnx_sr.mt_ship_rec_loading_unloading'] = lambda s, c, u, r, ctx: r['state'] == 'loading'
    state['fnx_sr.mt_ship_rec_partial_receipt'] = lambda s, c, u, r, ctx: r['state'] == 'partial'
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

    def _calc_appt(self, cr, uid, ids, field_names, _arg, context=None):
        res = {}
        if not ids or not field_names:
            return res
        ctx = context or {}
        tz_name = ctx.get('tz', False)
        if not tz_name:
            try:
                tz_name = self.pool.get(
                        'ir.config_parameter'
                        ).read(
                            cr, uid,
                            ids=[('key','=','database.time_zone')]
                            )[0]['value']
            except IndexError:
                _logger.warning('missing system parameter: database.time_zone')
                tz_name = 'UTC'
        try:
            tz = pytz.timezone(tz_name)
        except Exception:
            _logger.warning("unknown timezone:  %r" % tz_name)
            tz = UTC
        if isinstance(ids, (int, long)):
            ids = [ids]
        data = self.read(cr, uid, ids, fields=['appointment_date', 'appointment_time'], context=ctx)
        for datum in data:
            if not datum['appointment_date']:
                dt = False
            else:
                # date/time are assumed to be in local time
                try:
                    date = Date.strptime(datum['appointment_date'], DEFAULT_SERVER_DATE_FORMAT)
                except ValueError:
                    raise ERPError('Invalid Time', 'Unable to parse date from %r' % datum['appointment_date'])
                try:
                    time = datum['appointment_time'] or '0:00'
                    time = Time.strptime(time, '%H:%M')
                except ValueError:
                    raise ERPError('Invalid Time', 'Unable to parse time from %r' % datum['appointment_time'])
                dt = DateTime.combine(date, time, tzinfo=tz)
            if 'appointment' in field_names:
                res[datum['id']] = {'appointment': dt}
        return res

    def _calc_duration(self, cr, uid, ids, _field=None, _arg=None, context=None):
        result = {}
        for id in ids:
            record = self.browse(cr, uid, id, context=context)
            result[id] = False
            if record.check_in and record.check_out:
                check_in = DateTime(record.check_in)
                check_out = DateTime(record.check_out)
                result[id] = value = float(check_out - check_in)
                if value < 0:
                    raise ERPError('Invalid Time', 'The check-out time is before the check-in time.')
        return result

    def _calc_state(self, cr, uid, ids, field_name, args, context=None):
        res = {}
        for record in self.browse(cr, SUPERUSER_ID, ids, context=context):
            # calculate the current state based on the data changes
            state = 'draft'
            old_state = record.state
            reopen = context.get('reopen', False)
            if old_state == 'cancelled' and not reopen:
                raise ERPError('Invalid Operation', 'This order has been cancelled.')
            # appt -> scheduled
            if record.appointment and record.pallets:
                state = 'ready'
            # checkin -> loading
            if record.check_in:
                state = 'loading'
            # -> transit (not implemented)
            # checkout -> (partial | complete)
            elif record.partial_complete:
                state = 'partial'
            if (record.check_out or old_state == 'complete') and not reopen:
                state = 'complete'
            # -> cancelled (doesn't happen here)
            res[record.id] = state
        return res

    _columns = {

        'state': fields.function(
            _calc_state,
            fnct_inv=True,
            type='selection',
            selection=(
                ('draft', 'Order Placed'),
                ('ready', 'Ready'),
                ('loading', 'Loading/Unloading'),
                ('transit', 'En Route'),
                ('partial', 'Partially Complete'),
                ('complete', 'Complete'),
                ('cancelled', 'Cancelled'),
                ),
            string='Status',
            sort_order='definition',
            help=(
                "Draft     --> Initial entry of order.\n"
                "Ready     --> Order has been pulled and palletized and/or carrier appointment has been confirmed.\n"
                "Loading/Unloading  --> Order is being transferred to/from the delivery truck.\n"
                "En Route  --> Order is travelling to destination.\n"
                "Partially Complete --> Some, not all, of order is done.\n"
                "Complete  --> Order is done (shipped or received).\n"
                "Cancelled --> Order was cancelled.",
                ),
            store={
                'fnx.sr.shipping': (
                    lambda s, c, u, ids, ctx={}: ids,
                    ['appointment_date', 'appointment_time', 'appt_confirmed', 'pallets', 'check_in', 'check_out', ],
                    99,
                    ),
                },
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
        'partner_number': fields.char('Customer #', size=6),
        'ship_to_code': fields.char('Ship To code', size=4),
        #
        'weight': fields.float('Weight'),
        'cartons': fields.integer('# of cartons'),
        'pallets': fields.integer('# of pallets'),
        'comment': fields.text('Comments', help="Comment or instructions for this order only."),
        #
        'carrier_id': fields.many2one('res.partner', 'Shipper', domain=[('is_carrier','=',True)]),
        'order_date': fields.date('Date order placed'),
        'wanted_date': fields.date('Date order wanted'),
        'ship_date': fields.date('Estimated ship date'),
        'appointment_date': fields.date('Appointment date', help="Date when driver should arrive."),
        'appointment_time': fields.char('Appointment time', size=5, help='Time when driver should arrive.'),
        'appointment': fields.function(
            _calc_appt,
            type='datetime',
            string='Appointment',
            track_visibility='change_only',
            store={
                'fnx.sr.shipping': (
                    lambda s, c, u, ids, ctx={}: ids,
                    ['appointment_date', 'appointment_time'],
                    10,
                    ),
                },
            multi='calc_appointment',
            ),
        'appt_scheduled_by_id': fields.many2one('res.users', string='Scheduled by', help="Falcon employee that scheduled appointment."),
        'duration': fields.function(_calc_duration, type='float', digits=(16,2), string='Duration (in hours)',
                store={'fnx.sr.shipping': (lambda s, c, u, ids, ctx={}: ids, ['check_in', 'check_out'], 30)}),
        'appt_confirmed': fields.boolean('Appointment confirmed'),
        'appt_confirmed_on': fields.datetime('Confirmed on', help="When the appointment was confirmed with the carrier"),
        'check_in': fields.datetime('Driver checked in at',),
        'check_out': fields.datetime('Driver checked out at'),
        'container': fields.char('Container ID', size=20),
        'partial_complete': fields.boolean('Partially complete', oldname='partial_receipt'),
        'shipments': fields.text('Shipments'),
        'carrier_tracking_no': fields.char('Tracking #', size=64),
        'carrier_appt_delivery_date': fields.date('Scheduled Delivery Date'),
        'carrier_actual_delivery_date': fields.date('Actual Delivery Date'),
        'carrier_bill_of_lading': fields.char('Bill of Lading', size=6),
        'create_date': fields.date('Date order imported'),
        }

    _sql_constraints = [ ('lsd_unique', 'unique(local_source_document)', 'Already have that source document in the system') ]


    def create(self, cr, uid, values, context=None):
        if 'appointment_date' in values or 'appointment_time' in values:
            values['appt_scheduled_by_id'] = uid
        res_users = self.pool.get('res.users')
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
        return super(fnx_sr_shipping, self).create(cr, uid, values, context=context)

    def write(self, cr, uid, ids, values, context=None):
        context = (context or {}).copy()
        if 'appointment_date' in values or 'appointment_time' in values:
            values['appt_scheduled_by_id'] = uid
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
        # don't (easily) reset pallets
        if values.get('pallets') == 0 and not context.get('fnxsr_pallet_reset'):
            values.pop('pallets')
        return super(fnx_sr_shipping, self).write(cr, uid, ids, values, context=context)

    def onchange_appt_time(self, cr, uid, ids, time, context=None):
        if isinstance(ids, (int, long)):
            ids = [ids]
        time = normalize_time(time)
        res = {}
        res['value'] = {'appointment_time': time}
        return res

    def sr_checkin(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        if isinstance(ids, (int, long)):
            ids = [ids]
        ctx['mail_create_nosubscribe'] = True
        values = {
                'check_in': DateTime.now(),
                }
        return self.write(cr, uid, ids, values, context=ctx)

    def sr_uncheckin(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        if isinstance(ids, (int, long)):
            ids = [ids]
        ctx['mail_create_nosubscribe'] = True
        ctx['message_force'] = 'Ticket un-checked-in: reset to'
        values = {
                'check_in': False,
                }
        return self.write(cr, uid, ids, values, context=ctx)

    def sr_checkout_partial(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        if isinstance(ids, (int, long)):
            ids = [ids]
        check_in = str_to_datetime(ctx['fnxsr_checkin'])
        check_out = local_datetime()
        shipments = ctx.get('fnxsr_shipments')
        if shipments:
            shipments = [shipments]
        else:
            shipments = []
        shipments.append(
                '%s - %s:  %s'
                % (
                    check_in,
                    check_out,
                    hrtd(check_out-check_in),
                ))
        values = {
                'check_in': False,
                'check_out': False,
                'partial_complete': True,
                'shipments': '\n'.join(shipments),
                }
        ctx['mail_create_nosubscribe'] = True
        if ctx.get('override', True):
            ctx['message_force'] = 'Manager override:'
        return self.write(cr, uid, ids, values, context=ctx)
    button_partial = sr_checkout_partial

    def sr_checkout_full(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        if isinstance(ids, (int, long)):
            ids = [ids]
        check_in = DateTime.strptime(ctx['fnxsr_checkin'], DEFAULT_SERVER_DATETIME_FORMAT)
        check_out = DateTime.now()
        shipments = ctx.get('fnxsr_shipments')
        if shipments:
            shipments = [shipments]
        else:
            shipments = []
        shipments.append('%s - %s:  %s' % (check_in, check_out, hrtd(check_out-check_in)))
        values = {
                'check_out':  check_out,
                'partial_complete': False,
                'shipments': '\n'.join(shipments),
                }
        ctx['mail_create_nosubscribe'] = True
        if ctx.get('override', True):
            ctx['message_force'] = 'Manager override:'
        return self.write(cr, uid, ids, values, context=ctx)
    button_complete = sr_checkout_full

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
        ctx['reopen'] = True
        ctx['mail_create_nosubscribe'] = True
        ctx['message_force'] = 'Manager override: reset to '
        return self.write(cr, uid, ids, {'check_out': False}, context=ctx)

    def button_reset(self, cr, uid, ids, context=None):
        ctx = (context or {}).copy()
        ctx['fnxsr_pallet_reset'] = True
        values = {'pallets': 0}
        return self.write(cr, uid, ids, values, context=ctx)

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


def normalize_time(time):
    'converts time to 24 hh:mm format'
    if not time or not time.strip():
        return '0:00'
    time = time + ' '
    m = re.search(r'^\s*(\d+)[:. ](\d\d)?\s*(.*)\s*$', time.lower())
    if not m:
        raise ERPError('Invalid Time', 'Unable to parse %r [no match]' % time)
    hour, minute, meridian = m.groups()
    hour = int(hour)
    minute = int(minute or 0)
    meridian = meridian.replace('.', '')
    if meridian not in ('', 'a', 'am', 'p', 'pm'):
        raise ERPError('Invalid Time', 'Unable to parse %r [bad meridian]' % time)
    if meridian.startswith('a') and hour == 12:
        hour -= 12
    elif meridian.startswith(('a','p')) and hour > 12:
        raise ERPError('Invalid Time', 'Unable to parse %r [bad meridian]' % time)
    elif meridian.startswith('p') and hour < 12:
        hour += 12
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ERPError('Invalid Time', '%r is not a valid time [invalid hour or minute]' % time)
    return '%d:%02d' % (hour, minute)

