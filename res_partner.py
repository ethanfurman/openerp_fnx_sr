import logging
from osv import osv, fields

class res_partner(osv.Model):
    "Inherits partner and adds carrier info"
    _name = 'res.partner'
    _inherit = 'res.partner'

    _columns = {
        'is_carrier': fields.boolean('Carrier', help='This partner is used for shipping.'),
        'warehouse_comment': fields.text('Warehouse Notes'),
        'fuel_surcharge': fields.boolean('Fuel surcharge'),
        }

    def fis_carrier_updates(self, cr, uid, *args):
        _logger.info("res_partner.fis_carrier_updates starting...")
        state_table = self.pool.get('res.country.state')
        state_recs = state_table.browse(cr, uid, state_table.search(cr, uid, [(1,'=',1)]))
        state_recs = dict([(r.name, (r.id, r.code, r.country_id.id)) for r in state_recs])
        #state_recs = dict([(r['name'], (r['id'], r['code'], r['country_id.id'])) for r in state_recs])
        country_table = self.pool.get('res.country')
        country_recs = country_table.browse(cr, uid, country_table.search(cr, uid, []))
        country_recs_code = dict([(r.code, r.id) for r in country_recs])
        country_recs_name = dict([(r.name, r.id) for r in country_recs])
        carrier_recs = self.browse(cr, uid, self.search(cr, uid, [('module','=','F27')]))
        carrier_codes = dict([(r.xml_id, r.id) for r in supplier_recs])
        carrier = fisData(27, keymatch='SV10%s')

        for sv_rec in carrier:
            result = {}
            result['is_company'] = True
            result['supplier'] = False
            result['customer'] = False
            result['is_carrier'] = True
            result['use_parent_address'] = False
            result['xml_id'] = key = sv_rec[F27.code]
            result['module'] = 'F27'
            result['name'] = BsnsCase(sv_rec[F27.name])
            addr1, addr2, addr3 = Sift(sv_rec[F27.addr1], sv_rec[F27.addr2], sv_rec[F27.addr3])
            addr2, city, state, postal, country = cszk(addr2, addr3)
            addr3 = ''
            if city and not (addr2 or state or postal or country):
                addr2, city = city, addr2
            addr1 = normalize_address(addr1)
            addr2 = normalize_address(addr2)
            addr1, addr2 = AddrCase(Rise(addr1, addr2))
            city = NameCase(city)
            state, country = NameCase(state), NameCase(country)
            result['street'] = addr1
            result['street2'] = addr2
            result['city'] = city
            result['zip'] = postal
            result['country_id'] = False
            result['state_id'] = False
            if state:
                result['state_id'] = state_recs[state][0]
                result['country_id'] = state_recs[state][2]
            elif country:
                country_id = country_recs_name.get(country, None)
                if country_id is None:
                    _logger.critical("Supplier %s has invalid country <%r>" % (key, country))
                else:
                    result['country_id'] = country_id
            result['phone'] = fix_phone(sv_rec[F27.tele])
            result['fuel_surcharge'] = sv_rec[F27.fuel_surcharge]

        _logger.info('res_partner.fis_updates done!')
        return True
res_partner()
