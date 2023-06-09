import json
import uuid
import pprint
import krock32
from decimal import Decimal
from flask import current_app as app, session
from rdflib import Graph, URIRef

from note.sql import *
from catalog_engine.rdf_data import DataCoder

class CatalogCart():
    def __init__(self):
        with open('/home/mfrager/vend/catalog-server/object_schema.json') as f: # TODO: config
            self.obj_schema = json.load(f)

    def build_cart(self):
        sess = sql_row('client_session', session_id=session.sid)
        if not(sess.exists()):
            raise Exception('Invalid session: {}'.format(session.sid))
        crc = sql_row('client_cart', session_id=sess.sql_id(), checkout_cancel=0)
        if crc.exists():
            return crc
        now = sql_now()
        crc = sql_insert('client_cart', {
            'session_id': sess.sql_id(),
            'cart_data': '{}',
            'ts_created': now,
            'ts_updated': now,
            'checkout_complete': False,
            'checkout_cancel': False,
            'cart_currency': 'USD',
            'cart_subtotal': 0,
            'cart_tax': 0,
            'cart_total': 0,
        })
        return crc

    def decode_entry_key(self, slug):
        index = None
        if '.' in slug:
            pts = slug.split('.', 2)
            slug = pts[0]
            index = pts[1]
        if '-' in slug:
            slug = slug.split('-')[-1]
        decoder = krock32.Decoder(strict=False, checksum=False)
        decoder.update(slug)
        entry_key = decoder.finalize()
        if len(entry_key) != 10:
            raise Exception('Invalid entry key size')
        return entry_key, index

    def get_cart_items(self, cart_id):
        ct = nsql.table('client_cart_item').get(
            select = 'count(id) as ct',
            where = {'cart_id': cart_id},
            result = list,
        )[0][0]
        tq = nsql.table('client_cart_item').get(
            select = 'sum(quantity) as ct',
            where = {'cart_id': cart_id},
            result = list,
        )[0][0]
        q = nsql.table('client_cart_item').get(
            select = [
                '(price * quantity) as total',
                'image_url as image',
                'entry_key as id',
                'product_index',
                'option_data',
                'quantity',
                'price',
                'label',
            ],
            where = {'cart_id': cart_id},
            order = 'client_cart_item.id asc',
            limit = 100,
        )
        items = []
        for r in q:
            encoder = krock32.Encoder(checksum=False)
            encoder.update(r['id'])
            r['id'] = encoder.finalize().upper()
            if r['product_index']:
                r['id'] = r['id'] + '.' + str(r['product_index'])
            del r['product_index']
            r['option_data'] = json.loads(r['option_data'])
            items.append(r)
        return {
            'item_count': ct,
            'item_quantity': tq,
            'items': items,
        }

    def add_cart_item(self, slug, quantity):
        entry_key, index = self.decode_entry_key(slug)
        entry = sql_row('entry', entry_key=entry_key)
        if not entry.exists():
            raise Exception('Invalid entry')
        qty = int(round(Decimal(quantity), 0))
        if qty <= 0:
            raise Exception('Invalid quantity')
        cart = self.build_cart()
        cart_id = cart.sql_id()
        item = sql_row('client_cart_item', cart_id=cart_id, entry_key=entry_key)
        if item.exists():
            item.update({
                'quantity': item['quantity'] + qty
            })
        else:
            record = sql_row('record', id=entry['record_id'])
            gr = Graph()
            gr.parse(data=record['data'], format='json-ld')
            coder = DataCoder(self.obj_schema, gr, None)
            product = coder.decode_rdf(entry['external_uri'])
            img = None
            label = product['name']
            if 'image' in product and len(product['image']) > 0 and 'url' in product['image'][0]:
                img = product['image'][0]['url']
            if product['type'] == 'IProductGroup':
                if index is None:
                    index = 0
                else:
                    index = int(index)
                if index >= len(product['hasVariant']):
                    raise Exception('Invalid product variant index: {}'.format(index))
                product = product['hasVariant'][index]
                # Use variant image if exists
                if 'image' in product and len(product['image']) > 0 and 'url' in product['image'][0]:
                    img = product['image'][0]['url']
            offer = product['offers'][0]
            price = Decimal(offer['price'])
            sql_insert('client_cart_item', {
                'cart_id': cart_id,
                'backend_id': entry['backend_id'],
                'option_data': '{}',
                'product_index': index,
                'entry_id': entry.sql_id(),
                'entry_key': entry_key,
                'quantity': qty,
                'price': str(price),
                'label': label,
                'image_url': img,
            })
        cart_updated = sql_row('client_cart', id=cart_id)
        cart_data = cart_updated.data()
        cart_data['cart_data'] = json.loads(cart_data['cart_data'])
        cart_data['cart_items'] = self.get_cart_items(cart_id)
        del cart_data['session_id']
        return cart_data

    def update_cart_item(self, slug, quantity):
        qty = int(round(Decimal(quantity), 0))
        if qty <= 0:
            raise Exception('Invalid quantity')
        entry_key, index = self.decode_entry_key(slug)
        cart = self.build_cart()
        cart_id = cart.sql_id()
        item = sql_row('client_cart_item', cart_id=cart_id, entry_key=entry_key)
        if not item.exists():
            raise Exception('Invalid cart entry key: {}'.format(slug))
        item.update({
            'quantity': qty,
        })
        cart_updated = sql_row('client_cart', id=cart_id)
        cart_data = cart_updated.data()
        cart_data['cart_data'] = json.loads(cart_data['cart_data'])
        cart_data['cart_items'] = self.get_cart_items(cart_id)
        del cart_data['session_id']
        return cart_data
 
    def remove_cart_item(self, slug):
        entry_key, index = self.decode_entry_key(slug)
        cart = self.build_cart()
        cart_id = cart.sql_id()
        item = sql_row('client_cart_item', cart_id=cart_id, entry_key=entry_key)
        if item.exists():
            item.delete()
        cart_updated = sql_row('client_cart', id=cart_id)
        cart_data = cart_updated.data()
        cart_data['cart_data'] = json.loads(cart_data['cart_data'])
        cart_data['cart_items'] = self.get_cart_items(cart_id)
        del cart_data['session_id']
        return cart_data
 
