import os
import json
import uuid
from functools import wraps
from rdflib import Graph, URIRef
from flask import current_app as app, session

from api.rest.base import BaseResource, CommandResource
from api import api_rest

# TODO: dynamic
VENDURE_URL = 'http://173.234.24.74:3000/shop-api'
MERCHANT_URI = 'https://savvyco.com/'

from session import disable_session
from catalog_engine import CatalogEngine
from catalog_engine.backend.vendure_backend import VendureBackend
from catalog_engine.catalog_cart import CatalogCart

class Commerce(CommandResource, BaseResource):
    class Commands:
        @disable_session
        def get_product(self, **data):
            print('get_product: {}'.format(data))
            res = {}
            ce = CatalogEngine()
            gr, item_uuid, category_path, index = ce.get_product_by_key(data['key'], category=data.get('category', None))
            #print(gr.serialize(format='turtle'))
            jsld = gr.serialize(format='json-ld')
            res['graph'] = json.loads(jsld)
            res['uuid'] = item_uuid
            res['path'] = category_path
            res['index'] = index
            res['result'] = 'ok'
            return res

        @disable_session
        def get_product_list(self, **data):
            res = {}
            ce = CatalogEngine()
            gr, item_uuid = ce.get_summary_by_category_slug(data['filters']['category'])
            #print(gr.serialize(format='turtle'))
            jsld = gr.serialize(format='json-ld')
            res['graph'] = json.loads(jsld)
            res['uuid'] = item_uuid
            res['result'] = 'ok'
            #print(res)
            return res

        @disable_session
        def get_collection_list(self, **data):
            res = {}
            gr = Graph()
            with open('merchant.rdf') as f:
                gr.parse(data=f.read(), format='xml')
            vb = VendureBackend(gr, URIRef(MERCHANT_URI), VENDURE_URL)
            item_uuid = vb.build_catalog()
            jsld = gr.serialize(format='json-ld')
            res['graph'] = json.loads(jsld)
            res['uuid'] = item_uuid
            res['result'] = 'ok'
            return res

        @disable_session
        def sync_merchant(self, **data):
            res = {}
            gr = Graph()
            with open('merchant.rdf') as f:
                gr.parse(data=f.read(), format='xml')
            vb = VendureBackend(gr, URIRef(MERCHANT_URI), VENDURE_URL)
            res.update(vb.sync_merchant(root_id='1'))
            res['result'] = 'ok'
            return res

        def get_cart(self, **data):
            print('get_cart: {} session: {}'.format(data, session.sid))
            ct = CatalogCart()
            res = ct.get_cart()
            session['cart'] = res['id']
            del res['id']
            res[app.session_cookie_name] = session.sid
            res['result'] = 'ok'
            return res

        def add_cart_item(self, **data):
            print('add_cart_item: {} session: {}'.format(data, session.sid))
            ct = CatalogCart()
            res = ct.add_cart_item(data['key'], data['quantity'])
            session['cart'] = res['id']
            del res['id']
            res[app.session_cookie_name] = session.sid
            res['result'] = 'ok'
            return res

        def update_cart_item(self, **data):
            print('update_cart_item: {} session: {}'.format(data, session.sid))
            ct = CatalogCart()
            res = ct.update_cart_item(data['key'], data['quantity'])
            session['cart'] = res['id']
            del res['id']
            res[app.session_cookie_name] = session.sid
            res['result'] = 'ok'
            return res

        def remove_cart_item(self, **data):
            print('remove_cart_item: {} session: {}'.format(data, session.sid))
            ct = CatalogCart()
            res = ct.remove_cart_item(data['key'])
            session['cart'] = res['id']
            del res['id']
            res[app.session_cookie_name] = session.sid
            res['result'] = 'ok'
            return res

        def prepare_checkout(self, **data):
            print('prepare_checkout: {} session: {}'.format(data, session.sid))
            ct = CatalogCart()
            res = ct.prepare_checkout()
            session['cart'] = res['id']
            del res['id']
            res[app.session_cookie_name] = session.sid
            pass

        def checkout(self, **data):
            pass

api_rest.add_resource(Commerce, '/commerce')

