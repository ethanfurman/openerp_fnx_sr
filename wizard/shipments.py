import logging
from osv import osv, fields
from openerp.exceptions import ERPError
import re

_logger = logging.getLogger(__name__)

# constants

class TicketState(fields.SelectionEnum):
    _order_ = 'draft ready loading transit partial complete cancelled'
    draft = 'Order Placed'
    ready = 'Ready'
    loading = 'Loading/Unloading'
    transit = 'En Route'
    partial = 'Partially Complete'
    complete = 'Complete'
    cancelled = 'Cancelled'

#
# set appointment
#

class fnx_sr_shipping_set_appointment(osv.TransientModel):
    _name = 'fnx.sr.shipping.set_appointment'

    _columns = {
        'appointment_date': fields.date('Date'),
        'appointment_time': fields.char('Time', size=5),
        'carrier_id': fields.many2one('res.partner', string='Carrier', domain=[('module','=','F27')]),
        'possible_records': fields.one2many(
                'fnx.sr.shipping.set_appointment.sub', 'master_id',
                string='Possibles',
                ),
        }

    def default_get(self, cr, uid, fields=None, context=None):
        res = {}.fromkeys(fields)
        ctx = context or {}
        source_ids = ctx.get('active_ids', [])
        if not (source_ids and 'possible_records' in (fields or [])):
            return super(fnx_sr_shipping_set_appointment, self).default_get(cr, uid, fields=fields, context=context)
        fnx_sr_shipping = self.pool.get('fnx.sr.shipping')
        tickets = []
        for rec in fnx_sr_shipping.browse(cr, uid, source_ids, context=context):
            tickets.append(dict(
                    source_id=rec.id,
                    name=rec.name,
                    partner=rec.partner_id.name,
                    current_appt='%s %s' % (rec.appointment_date, rec.appointment_time),
                    current_carrier=rec.carrier_id.name,
                    disposition='set',
                    state=rec.state,
                    ))
        for f in ('appointment_date', 'appointment_time'):
            if f in fields:
                res[f] = False
        res['possible_records'] = tickets
        return res

    def create(self, cr, uid, values, context=None):
        ctx = context or {}
        source_ids = ctx.get('active_ids', [])
        if source_ids:
            fnx_sr_shipping = self.pool.get('fnx.sr.shipping')
            schedule_ids = [
                    t[2]['source_id']
                    for t in values['possible_records']
                    if t[0] == 0
                    ]
            fnx_sr_shipping.write(
                    cr, uid,
                    schedule_ids,
                    {
                        'appointment_date': values['appointment_date'],
                        'appointment_time': values['appointment_time'],
                        'carrier_id': values['carrier_id'],
                        },
                    context=context,
                    )
        return super(fnx_sr_shipping_set_appointment, self).create(cr, uid, values, context=context)

    def confirm(self, cr, uid, ids, context=None):
        return {'type': 'ir.actions.act_window_close'}

    def onchange_appt_time(self, cr, uid, ids, time, context=None):
        if isinstance(ids, (int, long)):
            ids = [ids]
        time = normalize_time(time)
        res = {}
        res['value'] = {'appointment_time': time}
        return res


class fnx_sr_shipping_set_appointment_sub(osv.TransientModel):
    _name = 'fnx.sr.shipping.set_appointment.sub'

    _columns = {
        'master_id': fields.many2one('fnx.sr.shipping.set_appointment', string='Master'),
        'source_id': fields.many2one('fnx.sr.shipping', string='Shipping Ticket'),
        'current_carrier': fields.char('Current Carrier', size=128),
        'name': fields.char('Document', size=64),
        'partner': fields.char('Partner', size=128),
        'current_appt': fields.char('Current Appointment', size=16),
        'disposition': fields.selection([
                    ('set', 'Set Appointment'),
                    ('ignore', 'Ignore'),
                    ],
                string='Action',
                sort_order='definition',
                ),
        'state': fields.selection(
                TicketState,
                string='Status',
                help=(
                    "Draft     --> Initial entry of order.\n"
                    "Ready     --> Order has been pulled and palletized and/or carrier appointment has been confirmed.\n"
                    "Loading/Unloading  --> Order is being transferred to/from the delivery truck.\n"
                    "En Route  --> Order is travelling to destination.\n"
                    "Partially Complete --> Some, not all, of order is done.\n"
                    "Complete  --> Order is done (shipped or received).\n"
                    "Cancelled --> Order was cancelled.",
                    ),
                ),
        }

#
# check-in
#

class fnx_sr_shipping_driver_checkin(osv.TransientModel):
    _name = 'fnx.sr.shipping.driver_checkin'

    _columns = {
        'possible_records': fields.one2many(
                'fnx.sr.shipping.driver_checkin.sub', 'master_id',
                string='Possibles',
                ),
        }

    def default_get(self, cr, uid, fields=None, context=None):
        res = {}.fromkeys(fields)
        ctx = context or {}
        source_ids = ctx.get('active_ids', [])
        if not (source_ids and 'possible_records' in (fields or [])):
            return super(fnx_sr_shipping_driver_checkin, self).default_get(cr, uid, fields=fields, context=context)
        fnx_sr_shipping = self.pool.get('fnx.sr.shipping')
        tickets = []
        for rec in fnx_sr_shipping.browse(cr, uid, source_ids, context=context):
            disp = 'checkin'
            if rec.state not in ('draft', 'ready'):
                disp = 'ignore'
            tickets.append(dict(
                    source_id=rec.id,
                    name=rec.name,
                    partner=rec.partner_id.name,
                    disposition=disp,
                    state=rec.state,
                    ))
        res['possible_records'] = tickets
        return res

    def create(self, cr, uid, values, context=None):
        ctx = context or {}
        source_ids = ctx.get('active_ids', [])
        if source_ids:
            fnx_sr_shipping = self.pool.get('fnx.sr.shipping')
            checkin_ids = [
                    t[2]['source_id']
                    for t in values['possible_records']
                    if t[0] == 0 and t[2]['disposition'] == 'checkin'
                    ]
            fnx_sr_shipping.sr_checkin(cr, uid, checkin_ids, context=context)
        return super(fnx_sr_shipping_driver_checkin, self).create(cr, uid, values, context=context)

    def confirm(self, cr, uid, ids, context=None):
        return {'type': 'ir.actions.act_window_close'}



class fnx_sr_shipping_driver_checkin_sub(osv.TransientModel):
    _name = 'fnx.sr.shipping.driver_checkin.sub'

    _columns = {
        'master_id': fields.many2one('fnx.sr.shipping.driver_checkin', string='Master'),
        'source_id': fields.many2one('fnx.sr.shipping', string='Shipping Ticket'),
        'name': fields.char('Document', size=64),
        'partner': fields.char('Partner', size=128),
        'disposition': fields.selection([
                    ('checkin', 'Check driver in'),
                    ('ignore', 'Ignore'),
                    ],
                string='Action',
                sort_order='definition',
                ),
        'state': fields.selection(
                TicketState,
                string='Status',
                help=(
                    "Draft     --> Initial entry of order.\n"
                    "Ready     --> Order has been pulled and palletized and/or carrier appointment has been confirmed.\n"
                    "Loading/Unloading  --> Order is being transferred to/from the delivery truck.\n"
                    "En Route  --> Order is travelling to destination.\n"
                    "Partially Complete --> Some, not all, of order is done.\n"
                    "Complete  --> Order is done (shipped or received).\n"
                    "Cancelled --> Order was cancelled.",
                    ),
                ),
        }

#
# check-out
#

class fnx_sr_shipping_driver_checkout(osv.TransientModel):
    _name = 'fnx.sr.shipping.driver_checkout'

    _columns = {
        'possible_records': fields.one2many(
                'fnx.sr.shipping.driver_checkout.sub', 'master_id',
                string='Possibles',
                ),
        }

    def default_get(self, cr, uid, fields=None, context=None):
        res = {}.fromkeys(fields)
        ctx = context or {}
        source_ids = ctx.get('active_ids', [])
        if not (source_ids and 'possible_records' in (fields or [])):
            return super(fnx_sr_shipping_driver_checkout, self).default_get(cr, uid, fields=fields, context=context)
        fnx_sr_shipping = self.pool.get('fnx.sr.shipping')
        tickets = []
        for rec in fnx_sr_shipping.browse(cr, uid, source_ids, context=context):
            disp = 'complete'
            if rec.state != 'loading':
                disp = 'ignore'
            tickets.append(dict(
                    source_id=rec.id,
                    name=rec.name,
                    partner=rec.partner_id.name,
                    disposition=disp,
                    state=rec.state,
                    check_in=rec.check_in,
                    shipments=rec.shipments,
                    ))
        res['possible_records'] = tickets
        return res

    def create(self, cr, uid, values, context=None):
        context = context or {}
        source_ids = context.get('active_ids', [])
        if source_ids:
            fnx_sr_shipping = self.pool.get('fnx.sr.shipping')
            for cmd, id, vals in values['possible_records']:
                if cmd == 0:
                    ctx = context.copy()
                    if vals['disposition'] == 'ignore':
                        continue
                    elif vals['disposition'] == 'uncheckin':
                        fnx_sr_shipping.sr_uncheckin(cr, uid, [vals['source_id']], context=context)
                    elif vals['disposition'] == 'partial':
                        ctx['fnxsr_checkin'] = vals['check_in']
                        ctx['fnxsr_shipments'] = vals['shipments']
                        fnx_sr_shipping.sr_checkout_partial(cr, uid, [vals['source_id']], context=ctx)
                    elif vals['disposition'] == 'complete':
                        ctx['fnxsr_checkin'] = vals['check_in']
                        ctx['fnxsr_shipments'] = vals['shipments']
                        fnx_sr_shipping.sr_checkout_full(cr, uid, [vals['source_id']], context=ctx)
                    else:
                        raise ERPError('unknown disposition: %r' % (vals['disposition'], ))
        return super(fnx_sr_shipping_driver_checkout, self).create(cr, uid, values, context=context)

    def confirm(self, cr, uid, ids, context=None):
        return {'type': 'ir.actions.act_window_close'}


class fnx_sr_shipping_driver_checkout_sub(osv.TransientModel):
    _name = 'fnx.sr.shipping.driver_checkout.sub'

    _columns = {
        'master_id': fields.many2one('fnx.sr.shipping.driver_checkout', string='Master'),
        'source_id': fields.many2one('fnx.sr.shipping', string='Shipping Ticket'),
        'name': fields.char('Document', size=64),
        'partner': fields.char('Partner', size=128),
        'check_in': fields.datetime('Check-in time'),
        'shipments': fields.text('Shipments'),
        'disposition': fields.selection([
                    ('complete', 'Complete'),
                    ('partial', 'Partial'),
                    ('ignore', 'Ignore'),
                    ('uncheckin', 'Undo Checkin'),
                    ],
                string='Action',
                sort_order='definition',
                ),
        'state': fields.selection(
                TicketState,
                string='Status',
                help=(
                    "Draft     --> Initial entry of order.\n"
                    "Ready     --> Order has been pulled and palletized and/or carrier appointment has been confirmed.\n"
                    "Loading/Unloading  --> Order is being transferred to/from the delivery truck.\n"
                    "En Route  --> Order is travelling to destination.\n"
                    "Partially Complete --> Some, not all, of order is done.\n"
                    "Complete  --> Order is done (shipped or received).\n"
                    "Cancelled --> Order was cancelled.",
                    ),
                ),
        }

#
# helpers
#

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

