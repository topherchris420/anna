import os
import json
import orjson
import re
import zlib
import isbnlib
import httpx
import functools
import collections
import barcode
import io
import langcodes
import tqdm
import concurrent
import threading
import yappi
import multiprocessing
import langdetect
import gc
import random
import slugify
import elasticsearch.helpers
import time
import pathlib

from config import settings
from flask import Blueprint, __version__, render_template, make_response, redirect, request
from allthethings.extensions import db, es, Reflected

# Import data-imports CLI commands
try:
    from data_imports import imports as import_cli
    HAS_IMPORTS = True
except ImportError:
    HAS_IMPORTS = False
from sqlalchemy import select, func, text, create_engine
from sqlalchemy.dialects.mysql import match
from pymysql.constants import CLIENT
from allthethings.extensions import ComputedAllMd5s

from allthethings.page.views import get_md5_dicts

cli = Blueprint("cli", __name__, template_folder="templates")

# ./run flask cli dbreset
@cli.cli.command('dbreset')
def dbreset():
    print("Erasing entire database! Did you double-check that any production/large databases are offline/inaccessible from here?")
    time.sleep(2)
    print("Giving you 5 seconds to abort..")
    time.sleep(5)

    # Per https://stackoverflow.com/a/4060259
    __location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))

    engine = create_engine(settings.SQLALCHEMY_DATABASE_URI, connect_args={"client_flag": CLIENT.MULTI_STATEMENTS})
    cursor = engine.raw_connection().cursor()

    # Generated with `docker-compose exec mariadb mysqldump -u allthethings -ppassword --opt --where="1 limit 100" --skip-comments --ignore-table=computed_all_md5s allthethings > dump.sql`
    cursor.execute(pathlib.Path(os.path.join(__location__, 'dump.sql')).read_text())
    cursor.close()

    mysql_build_computed_all_md5s_internal()

    time.sleep(1)
    Reflected.prepare(db.engine)
    elastic_reset_md5_dicts_internal()
    elastic_build_md5_dicts_internal()

    print("Done! Search for example for 'Rhythms of the brain': http://localhost:8000/search?q=Rhythms+of+the+brain")


def chunks(l, n):
    for i in range(0, len(l), n):
        yield l[i:i + n]

def query_yield_batches(conn, qry, pk_attr, maxrq):
    """specialized windowed query generator (using LIMIT/OFFSET)

    This recipe is to select through a large number of rows thats too
    large to fetch at once. The technique depends on the primary key
    of the FROM clause being an integer value, and selects items
    using LIMIT."""

    firstid = None
    while True:
        q = qry
        if firstid is not None:
            q = qry.where(pk_attr > firstid)
        batch = conn.execute(q.order_by(pk_attr).limit(maxrq)).all()
        if len(batch) == 0:
            break
        yield batch
        firstid = batch[-1][0]


# Rebuild "computed_all_md5s" table in MySQL. At the time of writing, this isn't
# used in the app, but it is used for `./run flask cli elastic_build_md5_dicts`.
# ./run flask cli mysql_build_computed_all_md5s
@cli.cli.command('mysql_build_computed_all_md5s')
def mysql_build_computed_all_md5s():
    print("Erasing entire MySQL 'computed_all_md5s' table! Did you double-check that any production/large databases are offline/inaccessible from here?")
    time.sleep(2)
    print("Giving you 5 seconds to abort..")
    time.sleep(5)

    mysql_build_computed_all_md5s_internal()

def mysql_build_computed_all_md5s_internal():
    engine = create_engine(settings.SQLALCHEMY_DATABASE_URI, connect_args={"client_flag": CLIENT.MULTI_STATEMENTS})
    cursor = engine.raw_connection().cursor()
    sql = """
        DROP TABLE IF EXISTS `computed_all_md5s`;
        CREATE TABLE computed_all_md5s (
            md5 CHAR(32) NOT NULL,
            PRIMARY KEY (md5)
        ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 SELECT md5 FROM libgenli_files;
        INSERT IGNORE INTO computed_all_md5s SELECT md5 FROM zlib_book WHERE md5 != '';
        INSERT IGNORE INTO computed_all_md5s SELECT md5_reported FROM zlib_book WHERE md5_reported != '';
        INSERT IGNORE INTO computed_all_md5s SELECT MD5 FROM libgenrs_updated;
        INSERT IGNORE INTO computed_all_md5s SELECT MD5 FROM libgenrs_fiction;
    """
    cursor.execute(sql)
    cursor.close()


# Recreate "md5_dicts" index in ElasticSearch, without filling it with data yet.
# (That is done with `./run flask cli elastic_build_md5_dicts`)
# ./run flask cli elastic_reset_md5_dicts
@cli.cli.command('elastic_reset_md5_dicts')
def elastic_reset_md5_dicts():
    print("Erasing entire ElasticSearch 'md5_dicts' index! Did you double-check that any production/large databases are offline/inaccessible from here?")
    time.sleep(2)
    print("Giving you 5 seconds to abort..")
    time.sleep(5)

    elastic_reset_md5_dicts_internal()

def elastic_reset_md5_dicts_internal():
    es.options(ignore_status=[400,404]).indices.delete(index='md5_dicts')
    es.indices.create(index='md5_dicts', body={
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "lgrsnf_book": {
                    "properties": {
                        "id": { "type": "integer", "index": False, "doc_values": False },
                        "md5": { "type": "keyword", "index": False, "doc_values": False }
                    }
                },
                "lgrsfic_book": {
                    "properties": {
                        "id": { "type": "integer", "index": False, "doc_values": False },
                        "md5": { "type": "keyword", "index": False, "doc_values": False }
                    }
                },
                "lgli_file": {
                    "properties": {
                        "f_id": { "type": "integer", "index": False, "doc_values": False },
                        "md5": { "type": "keyword", "index": False, "doc_values": False },
                        "libgen_topic": { "type": "keyword", "index": False, "doc_values": False }
                    }
                },
                "zlib_book": {
                    "properties": {
                        "zlibrary_id": { "type": "integer", "index": False, "doc_values": False },
                        "md5": { "type": "keyword", "index": False, "doc_values": False },
                        "md5_reported": { "type": "keyword", "index": False, "doc_values": False },
                        "filesize": { "type": "long", "index": False, "doc_values": False },
                        "filesize_reported": { "type": "long", "index": False, "doc_values": False },
                        "in_libgen": { "type": "byte", "index": False, "doc_values": False },
                        "pilimi_torrent": { "type": "keyword", "index": False, "doc_values": False }
                    }
                },
                "ipfs_infos": {
                    "properties": {
                        "ipfs_cid": { "type": "keyword", "index": False, "doc_values": False },
                        "filename": { "type": "keyword", "index": False, "doc_values": False },
                        "from": { "type": "keyword", "index": False, "doc_values": False }
                    }
                },
                "file_unified_data": {
                    "properties": {
                        "original_filename_best": { "type": "keyword", "index": False, "doc_values": False },
                        "original_filename_additional": { "type": "keyword", "index": False, "doc_values": False },
                        "original_filename_best_name_only": { "type": "keyword", "index": False, "doc_values": False },
                        "cover_url_best": { "type": "keyword", "index": False, "doc_values": False },
                        "cover_url_additional": { "type": "keyword", "index": False, "doc_values": False },
                        "extension_best": { "type": "keyword", "index": True, "doc_values": False },
                        "extension_additional": { "type": "keyword", "index": False, "doc_values": False },
                        "filesize_best": { "type": "long", "index": False, "doc_values": False },
                        "filesize_additional": { "type": "long", "index": False, "doc_values": False },
                        "title_best": { "type": "keyword", "index": False, "doc_values": False },
                        "title_additional": { "type": "keyword", "index": False, "doc_values": False },
                        "author_best": { "type": "keyword", "index": False, "doc_values": False },
                        "author_additional": { "type": "keyword", "index": False, "doc_values": False },
                        "publisher_best": { "type": "keyword", "index": False, "doc_values": False },
                        "publisher_additional": { "type": "keyword", "index": False, "doc_values": False },
                        "edition_varia_best": { "type": "keyword", "index": False, "doc_values": False },
                        "edition_varia_additional": { "type": "keyword", "index": False, "doc_values": False },
                        "year_best": { "type": "keyword", "index": True, "doc_values": True },
                        "year_additional": { "type": "keyword", "index": False, "doc_values": False },
                        "comments_best": { "type": "keyword", "index": False, "doc_values": False },
                        "comments_additional": { "type": "keyword", "index": False, "doc_values": False },
                        "stripped_description_best": { "type": "keyword", "index": False, "doc_values": False },
                        "stripped_description_additional": { "type": "keyword", "index": False, "doc_values": False },
                        "language_codes": { "type": "keyword", "index": False, "doc_values": False },
                        "language_names": { "type": "keyword", "index": False, "doc_values": False },
                        "most_likely_language_code": { "type": "keyword", "index": True, "doc_values": False },
                        "most_likely_language_name": { "type": "keyword", "index": False, "doc_values": False },
                        "sanitized_isbns": { "type": "keyword", "index": True, "doc_values": False },
                        "asin_multiple": { "type": "keyword", "index": True, "doc_values": False },
                        "googlebookid_multiple": { "type": "keyword", "index": True, "doc_values": False },
                        "openlibraryid_multiple": { "type": "keyword", "index": True, "doc_values": False },
                        "doi_multiple": { "type": "keyword", "index": True, "doc_values": False },
                        "problems": {
                            "properties": {
                                "type": { "type": "keyword", "index": False, "doc_values": False },
                                "descr": { "type": "keyword", "index": False, "doc_values": False }
                            }
                        },
                        "content_type": { "type": "keyword", "index": True, "doc_values": False }
                    }
                },
                "search_text": { "type": "text", "index": True }
            }
        },
        "settings": {
            "index.number_of_replicas": 0,
            "index.search.slowlog.threshold.query.warn": "2s",
            "index.store.preload": ["nvd", "dvd"]
        }
    })

# Regenerate "md5_dicts" index in ElasticSearch.
# ./run flask cli elastic_build_md5_dicts
@cli.cli.command('elastic_build_md5_dicts')
def elastic_build_md5_dicts():
    elastic_build_md5_dicts_internal()

def elastic_build_md5_dicts_job(canonical_md5s):
    try:
        with db.Session(db.engine) as session:
            md5_dicts = get_md5_dicts(db.session, canonical_md5s)
            for md5_dict in md5_dicts:
                md5_dict['_op_type'] = 'index'
                md5_dict['_index'] = 'md5_dicts'
                md5_dict['_id'] = md5_dict['md5']
                del md5_dict['md5']
                
            elasticsearch.helpers.bulk(es, md5_dicts, request_timeout=30)
            # print(f"Processed {len(md5_dicts)} md5s")
    except Exception as err:
        print(repr(err))
        raise err

def elastic_build_md5_dicts_internal():
    THREADS = 60
    CHUNK_SIZE = 70
    BATCH_SIZE = 100000

    first_md5 = ''
    # Uncomment to resume from a given md5, e.g. after a crash
    # first_md5 = '0337ca7b631f796fa2f465ef42cb815c'

    with db.engine.connect() as conn:
        total = conn.execute(select([func.count(ComputedAllMd5s.md5)])).scalar()
        with tqdm.tqdm(total=total, bar_format='{l_bar}{bar}{r_bar} {eta}') as pbar:
            for batch in query_yield_batches(conn, select(ComputedAllMd5s.md5).where(ComputedAllMd5s.md5 >= first_md5), ComputedAllMd5s.md5, BATCH_SIZE):
                with multiprocessing.Pool(THREADS) as executor:
                    print(f"Processing {len(batch)} md5s from computed_all_md5s (starting md5: {batch[0][0]})...")
                    executor.map(elastic_build_md5_dicts_job, chunks([item[0] for item in batch], CHUNK_SIZE))
                    pbar.update(len(batch))

            print(f"Done!")
