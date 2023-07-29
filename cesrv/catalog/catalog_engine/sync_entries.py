import uuid
import json
import secrets
import requests
import canonicaljson
from blake3 import blake3
from rdflib import Graph, Literal, URIRef, Namespace, BNode
from rdflib.namespace import RDF, SKOS, RDFS, XSD, OWL, DC, DCTERMS
from flask import current_app as app

from note.sql import * 
from note.logging import *
from catalog_engine.rdf_data import DataCoder
from catalog_engine.backend.vendure_backend import VendureBackend
from .sync_data import DataSync

class SyncEntries(DataSync):
    def __init__(self, backend, listing, listing_params):
        self.backend = backend
        self.listing = listing
        self.listing_params = listing_params
        self.obj_schema = app.config['CATALOG_SCHEMA']
        self.update = True
        self.src_data = {}
        self.dst_data = {}
        self.dst_listings = {}

    def load(self):
        bk = self.backend['backend_name']
        bkid = self.backend.sql_id()
        log_warn('Sync: {}'.format(self.backend.data()))
        if bk == 'vendure':
            # Get source data
            gr = Graph()
            backend_data = json.loads(self.backend['config_data'])
            vb = VendureBackend(bkid, gr, URIRef(self.listing['merchant_uri']), backend_data['vendure_url'])
            seen = {}
            for rc in self.listing_params:
                coll = vb.get_collection(rc['collection']['slug'])
                #log_warn('Collection: {} {}'.format(rc, coll))
                for prod in coll['products']:
                    #log_warn(prod['productId'])
                    if prod['productId'] in seen:
                        continue
                    seen[prod['productId']] = True
                    pgr = Graph()
                    slug = None
                    data_summary = None
                    indexfield = {}
                    current = sql_row('entry', backend_id=bkid, external_id=prod['productId'])
                    if current.exists():
                        record_uuid = str(uuid.UUID(bytes=current['uuid']))
                    else:
                        record_uuid = str(uuid.uuid4())
                    obj_list = vb.get_product_spec(prod['productId'])
                    for idx in range(len(obj_list)):
                        obj = obj_list[idx]
                        if idx == 0:
                            obj['uuid'] = record_uuid
                            if 'alternateName' in obj:
                                slug = obj['alternateName'].lower()
                            elif 'name' in obj:
                                slug = self.convert_to_slug(obj['name'])
                            summ = vb.summarize_product_spec(obj)
                            if 'name' in summ:
                                indexfield['name'] = summ['name']
                            if 'offers' in summ:
                                if 'price' in summ['offers'][0]:
                                    indexfield['price'] = summ['offers'][0]['price']
                            # TODO: index brand (to string)
                            sgr = Graph()
                            summ_coder = DataCoder(self.obj_schema, sgr, summ['id'])
                            summ_coder.encode_rdf(summ)
                            data_summary = self.json_hash(json.loads(sgr.serialize(format='json-ld')), digest=False)
                            type_id = sql_query('SELECT entry_type_id({})'.format(sql_param()), [obj['type']], list)[0][0]
                        coder = DataCoder(self.obj_schema, pgr, obj['id'])
                        coder.encode_rdf(obj)
                    jsld = json.loads(pgr.serialize(format='json-ld'))
                    data, data_hash = self.json_hash(jsld)
                    src_entry = {
                        'external_id': prod['productId'],
                        'external_uri': obj_list[0]['id'],
                        'type_id': type_id,
                        'uuid': record_uuid,
                        'slug': slug,
                        'data': data,
                        'data_hash': data_hash,
                        'data_summary': data_summary, 
                        'entry_name': indexfield.get('name', None),
                        'entry_brand': indexfield.get('brand', None),
                        'entry_price': indexfield.get('price', None),
                    }
                    self.src_data[str(src_entry['external_id'])] = src_entry
            # Get destination data
            etq = nsql.table('entry').get(
                select = ['id', 'data_hash', 'external_id', 'external_uri'],
                where = {'backend_id': bkid},
            )
            for r in etq:
                self.dst_data[str(r['external_id'])] = {
                    'id': r['id'],
                    'data_hash': r['data_hash'],
                    'external_id': r['external_id'],
                    'external_uri': r['external_uri'],
                }

    def src_items(self):
        return self.src_data.keys()

    def src_has(self, item):
        res = item in self.src_data
        return res

    def dst_items(self):
        return self.dst_data.keys()

    def dst_has(self, item):
        res = item in self.dst_data
        if res:
            self.dst_listings.setdefault(str(item), {})
            cur = sql_row('entry', backend_id=self.backend.sql_id(), external_id=item)
            listings = nsql.table('entry_listing').get(
                select = ['listing_posted_id'],
                where = {'entry_id': cur.sql_id()},
                order = 'listing_posted_id asc',
            )
            for lst in listings:
                self.dst_listings[str(item)][str(lst['listing_posted_id'])] = True
        return res

    def dst_add(self, item):
        log_warn('Add {}'.format(item))
        ts = sql_now()
        inp = self.src_data[item]
        entry_key = self.new_entry_key()
        # Base entry
        entry = sql_insert('entry', {
            'backend_id': self.backend.sql_id(),
            'entry_key': entry_key,
            'external_id': inp['external_id'],
            'external_uri': inp['external_uri'],
            'type_id': inp['type_id'],
            'slug': inp['slug'],
            'data': inp['data'],
            'data_hash': inp['data_hash'],
            'data_summary': inp['data_summary'],
            'ts_created': ts,
            'ts_updated': ts,
            'user_id': self.backend['user_id'],
            'uuid': uuid.UUID(inp['uuid']).bytes,
        })
        sql_insert('entry_listing', {
            'entry_id': entry.sql_id(),
            'entry_version': 0,
            'listing_posted_id': self.listing['id'],
        })
        # Categories
        # TODO: this

    def dst_delete(self, item):
        log_warn('Delete {}'.format(item))
        cur = sql_row('entry', backend_id=self.backend.sql_id(), external_id=item)
        listings = nsql.table('entry_listing').get(
            select = ['id', 'listing_posted_id'],
            where = {'entry_id': cur.sql_id()},
            order = 'listing_posted_id asc',
        )
        ct = len(listings)
        for lst in listings:
            if str(self.listing['id']) == str(lst['listing_posted_id']):
                nsql.table('entry_listing').delete(where = {'id': lst['id']})
                break
        if ct == 1:
            # Removed last listing -> entry connection
            cur.delete()

    def dst_eq(self, item):
        orig_item = self.dst_data[item]
        new_item = self.src_data[item]
        #log_warn(orig_item['data_hash'])
        #log_warn(new_item['data_hash'])
        #log_warn(orig_item['external_uri'])
        #log_warn(new_item['external_uri'])
        if (
            orig_item['data_hash'] == new_item['data_hash'] and
            orig_item['external_uri'] == new_item['external_uri']
        ):
            return False, None, None
        return True, orig_item, new_item

    def dst_update(self, item, orig_item, inp):
        entry = sql_row('entry', backend_id=self.backend.sql_id(), external_id=item)
        entry.update({
            'external_uri': inp['external_uri'],
            'type_id': inp['type_id'],
            'slug': inp['slug'],
            'data': inp['data'],
            'data_hash': inp['data_hash'],
            'data_summary': inp['data_summary'],
            'ts_updated': sql_now(),
        })
        prm = sql_param()
        sql_exec(f'UPDATE `entry_listing` SET `entry_version`=`entry_version` + 1 WHERE `entry_id`={prm} AND `listing_posted_id`={prm}', [
            entry.sql_id(),
            self.listing['id'],
        ])

    def finalize(self):
        for item in self.dst_listings.keys():
            listing_group = self.dst_listings[item]
            if str(self.listing['id']) not in listing_group:
                # Exists from other listing, add this listing -> entry connection
                entry = sql_row('entry', backend_id=self.backend.sql_id(), external_id=item)
                sql_insert('entry_listing', {
                    'entry_id': entry.sql_id(),
                    'entry_version': 0,
                    'listing_posted_id': self.listing['id'],
                })

    def new_entry_key(self):
        for i in range(10):
            token = secrets.token_bytes(10)
            rc = sql_row('entry', entry_key=token)
            if not(rc.exists()):
                return token
        raise Exception('Duplicate entry keys after 10 tries')

    def json_hash(self, data, digest=True):
        enc = canonicaljson.encode_canonical_json(data)
        if digest:
            hs = blake3()
            hs.update(enc)
            return enc.decode('utf8'), hs.digest() 
        else:
            return enc.decode('utf8')

    def convert_to_slug(self, label):
        # Convert to lower case
        slug = label.lower()
        # Remove extra characters
        slug = re.sub(r'[^a-z0-9\s-]', '', slug)
        # Replace spaces with '-'
        slug = re.sub(r'\s+', '-', slug)
        return slug

