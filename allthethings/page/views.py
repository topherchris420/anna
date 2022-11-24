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

from flask import Blueprint, __version__, render_template, make_response, redirect, request
from allthethings.extensions import db, ZlibBook, ZlibIsbn, IsbndbIsbns, LibgenliEditions, LibgenliEditionsAddDescr, LibgenliEditionsToFiles, LibgenliElemDescr, LibgenliFiles, LibgenliFilesAddDescr, LibgenliPublishers, LibgenliSeries, LibgenliSeriesAddDescr, LibgenrsDescription, LibgenrsFiction, LibgenrsFictionDescription, LibgenrsFictionHashes, LibgenrsHashes, LibgenrsTopics, LibgenrsUpdated, OlBase, ComputedAllMd5s, ComputedSearchMd5Objs
from sqlalchemy import select, func, text
from sqlalchemy.dialects.mysql import match

page = Blueprint("page", __name__, template_folder="templates")

# Retrieved from https://openlibrary.org/config/edition.json on 2022-10-11
ol_edition_json = json.load(open(os.path.dirname(os.path.realpath(__file__)) + '/ol_edition.json'))
ol_classifications = {}
for classification in ol_edition_json['classifications']:
    if 'website' in classification:
        classification['website'] = classification['website'].split(' ')[0] # sometimes there's a suffix in text..
    ol_classifications[classification['name']] = classification
ol_classifications['lc_classifications']['website'] = 'https://en.wikipedia.org/wiki/Library_of_Congress_Classification'
ol_classifications['dewey_decimal_class']['website'] = 'https://en.wikipedia.org/wiki/List_of_Dewey_Decimal_classes'
ol_identifiers = {}
for identifier in ol_edition_json['identifiers']:
    ol_identifiers[identifier['name']] = identifier

# Taken from https://github.com/internetarchive/openlibrary/blob/e7e8aa5b8c/openlibrary/plugins/openlibrary/pages/languages.page
# because https://openlibrary.org/languages.json doesn't seem to give a complete list? (And ?limit=.. doesn't seem to work.)
ol_languages_json = json.load(open(os.path.dirname(os.path.realpath(__file__)) + '/ol_languages.json'))
ol_languages = {}
for language in ol_languages_json:
    ol_languages[language['key']] = language


# Good pages to test with:
# * http://localhost:8000/zlib/1
# * http://localhost:8000/zlib/100
# * http://localhost:8000/zlib/4698900
# * http://localhost:8000/zlib/19005844
# * http://localhost:8000/zlib/2425562
# * http://localhost:8000/ol/OL100362M
# * http://localhost:8000/ol/OL33897070M
# * http://localhost:8000/ol/OL39479373M
# * http://localhost:8000/ol/OL1016679M
# * http://localhost:8000/ol/OL10045347M
# * http://localhost:8000/ol/OL1183530M
# * http://localhost:8000/ol/OL1002667M
# * http://localhost:8000/ol/OL1000021M
# * http://localhost:8000/ol/OL13573618M
# * http://localhost:8000/ol/OL999950M
# * http://localhost:8000/ol/OL998696M
# * http://localhost:8000/ol/OL22555477M
# * http://localhost:8000/ol/OL15990933M
# * http://localhost:8000/ol/OL6785286M
# * http://localhost:8000/ol/OL3296622M
# * http://localhost:8000/ol/OL2862972M
# * http://localhost:8000/ol/OL24764643M
# * http://localhost:8000/ol/OL7002375M
# * http://localhost:8000/lgrs/nf/288054
# * http://localhost:8000/lgrs/nf/3175616
# * http://localhost:8000/lgrs/nf/2933905
# * http://localhost:8000/lgrs/nf/1125703
# * http://localhost:8000/lgrs/nf/59
# * http://localhost:8000/lgrs/nf/1195487
# * http://localhost:8000/lgrs/nf/1360257
# * http://localhost:8000/lgrs/nf/357571
# * http://localhost:8000/lgrs/nf/2425562
# * http://localhost:8000/lgrs/nf/3354081
# * http://localhost:8000/lgrs/nf/3357578
# * http://localhost:8000/lgrs/nf/3357145
# * http://localhost:8000/lgrs/nf/2040423
# * http://localhost:8000/lgrs/fic/1314135
# * http://localhost:8000/lgrs/fic/25761
# * http://localhost:8000/lgrs/fic/2443846
# * http://localhost:8000/lgrs/fic/2473252
# * http://localhost:8000/lgrs/fic/2340232
# * http://localhost:8000/lgrs/fic/1122239
# * http://localhost:8000/lgrs/fic/6862
# * http://localhost:8000/lgli/file/100
# * http://localhost:8000/lgli/file/1635550
# * http://localhost:8000/lgli/file/94069002
# * http://localhost:8000/lgli/file/40122
# * http://localhost:8000/lgli/file/21174
# * http://localhost:8000/lgli/file/91051161
# * http://localhost:8000/lgli/file/733269
# * http://localhost:8000/lgli/file/156965
# * http://localhost:8000/lgli/file/10000000
# * http://localhost:8000/lgli/file/933304
# * http://localhost:8000/lgli/file/97559799
# * http://localhost:8000/lgli/file/3756440
# * http://localhost:8000/lgli/file/91128129
# * http://localhost:8000/lgli/file/44109
# * http://localhost:8000/lgli/file/2264591
# * http://localhost:8000/lgli/file/151611
# * http://localhost:8000/lgli/file/1868248
# * http://localhost:8000/lgli/file/1761341
# * http://localhost:8000/lgli/file/4031847
# * http://localhost:8000/lgli/file/2827612
# * http://localhost:8000/lgli/file/2096298
# * http://localhost:8000/lgli/file/96751802
# * http://localhost:8000/lgli/file/5064830
# * http://localhost:8000/lgli/file/1747221
# * http://localhost:8000/lgli/file/1833886
# * http://localhost:8000/lgli/file/3908879
# * http://localhost:8000/lgli/file/41752
# * http://localhost:8000/lgli/file/97768237
# * http://localhost:8000/lgli/file/4031335
# * http://localhost:8000/lgli/file/1842179
# * http://localhost:8000/lgli/file/97562793
# * http://localhost:8000/lgli/file/4029864
# * http://localhost:8000/lgli/file/2834701
# * http://localhost:8000/lgli/file/97562143
# * http://localhost:8000/isbn/9789514596933
# * http://localhost:8000/isbn/9780000000439
# * http://localhost:8000/isbn/9780001055506
# * http://localhost:8000/isbn/9780316769174
# * http://localhost:8000/md5/8fcb740b8c13f202e89e05c4937c09ac

# Example: http://193.218.118.109/zlib2/pilimi-zlib2-0-14679999-extra/11078831.pdf
def make_temp_anon_zlib_link(zlibrary_id, pilimi_torrent, extension):
    prefix = "zlib1"
    if "-zlib2-" in pilimi_torrent:
        prefix = "zlib2"
    return f"http://193.218.118.109/{prefix}/{pilimi_torrent.replace('.torrent', '')}/{zlibrary_id}.{extension}"

def make_normalized_filename(slug_info, extension, collection, id):
    slug = slugify.slugify(slug_info, allow_unicode=True, max_length=50, word_boundary=True)
    return f"{slug}--annas-archive--{collection}-{id}.{extension}"


def make_sanitized_isbns(potential_isbns):
    sanitized_isbns = set()
    for potential_isbn in potential_isbns:
        isbn = potential_isbn.replace('-', '').replace(' ', '')
        if isbnlib.is_isbn10(isbn):
            sanitized_isbns.add(isbn)
            sanitized_isbns.add(isbnlib.to_isbn13(isbn))
        if isbnlib.is_isbn13(isbn):
            sanitized_isbns.add(isbn)
            isbn10 = isbnlib.to_isbn10(isbn)
            if isbnlib.is_isbn10(isbn10 or ''):
                sanitized_isbns.add(isbn10)
    return list(sanitized_isbns)

def make_isbns_rich(sanitized_isbns):
    rich_isbns = []
    for isbn in sanitized_isbns:
        if len(isbn) == 13:
            potential_isbn10 = isbnlib.to_isbn10(isbn)
            if isbnlib.is_isbn10(potential_isbn10):
                rich_isbns.append((isbn, potential_isbn10, isbnlib.mask(isbn), isbnlib.mask(potential_isbn10)))
            else:
                rich_isbns.append((isbn, '', isbnlib.mask(isbn), ''))
    return rich_isbns

def strip_description(description):
    return re.sub('<[^<]+?>', '', description.replace('</p>', '\n\n').replace('</P>', '\n\n').replace('<br>', '\n').replace('<BR>', '\n'))

def nice_json(some_dict):
    return orjson.dumps(some_dict, option=orjson.OPT_INDENT_2 | orjson.OPT_NON_STR_KEYS, default=str).decode('utf-8')

@functools.cache
def get_bcp47_lang_codes_parse_substr(substr):
        lang = 'unk'
        try:
            lang = str(langcodes.get(substr))
        except:
            try:
                lang = str(langcodes.find(substr))
            except:
                lang = 'unk'
        # We have a bunch of weird data that gets interpreted as "Egyptian Sign Language" when it's
        # clearly all just Spanish..
        if lang == "esl":
            lang = "es"
        return lang

@functools.cache
def get_bcp47_lang_codes(string):
    potential_codes = set()
    potential_codes.add(get_bcp47_lang_codes_parse_substr(string))
    for substr in re.split(r'[-_,;/]', string):
        potential_codes.add(get_bcp47_lang_codes_parse_substr(substr.strip()))
    potential_codes.discard('unk')
    return list(potential_codes)

def combine_bcp47_lang_codes(sets_of_codes):
    combined_codes = set()
    for codes in sets_of_codes:
        for code in codes:
            combined_codes.add(code)
    return list(combined_codes)


@page.get("/")
def home_page():
    with db.session.connection() as conn:
        popular_md5s = [
            "8336332bf5877e3adbfb60ac70720cd5", # Against intellectual monopoly
            "f0a0beca050610397b9a1c2604c1a472", # Harry Potter
            "61a1797d76fc9a511fb4326f265c957b", # Cryptonomicon
            "4b3cd128c0cc11c1223911336f948523", # Subtle art of not giving a f*ck
            "6d6a96f761636b11f7e397b451c62506", # Game of thrones
            "0d9b713d0dcda4c9832fcb056f3e4102", # Aaron Swartz
            "45126b536bbdd32c0484bd3899e10d39", # Three-body problem
            "6963187473f4f037a28e2fe1153ca793", # How music got free
            "6db7e0c1efc227bc4a11fac3caff619b", # It ends with us
            "7849ad74f44619db11c17b85f1a7f5c8", # Lord of the rings
            "6ed2d768ec1668c73e4fa742e3df78d6", # Physics
        ]
        popular_search_md5_objs_raw = conn.execute(select(ComputedSearchMd5Objs.md5, ComputedSearchMd5Objs.json).where(ComputedSearchMd5Objs.md5.in_(popular_md5s)).limit(1000)).all()
        popular_search_md5_objs_raw.sort(key=lambda popular_search_md5_obj: popular_md5s.index(popular_search_md5_obj.md5))
        popular_search_md5_objs = [SearchMd5Obj(search_md5_obj_raw.md5, *orjson.loads(search_md5_obj_raw.json)) for search_md5_obj_raw in popular_search_md5_objs_raw]

        return render_template(
            "page/home.html",
            header_active="home",
            popular_search_md5_objs=popular_search_md5_objs,
        )


@page.get("/about")
def about_page():
    return render_template("page/about.html", header_active="about")

@page.get("/datasets")
def datasets_page():
    return render_template("page/datasets.html", header_active="datasets")


def get_zlib_book_dicts(session, key, values):
    zlib_books = session.scalars(select(ZlibBook).where(getattr(ZlibBook, key).in_(values))).unique().all()

    zlib_book_dicts = []
    for zlib_book in zlib_books:
        zlib_book_dict = zlib_book.to_dict()
        zlib_book_dict['sanitized_isbns'] = [record.isbn for record in zlib_book.isbns]
        zlib_book_dict['isbns_rich'] = make_isbns_rich(zlib_book_dict['sanitized_isbns'])
        zlib_book_dict['stripped_description'] = strip_description(zlib_book_dict['description'])
        zlib_book_dict['language_codes'] = get_bcp47_lang_codes(zlib_book_dict['language'] or '')
        edition_varia_normalized = []
        if len((zlib_book_dict.get('series') or '').strip()) > 0:
            edition_varia_normalized.append(zlib_book_dict['series'].strip())
        if len((zlib_book_dict.get('volume') or '').strip()) > 0:
            edition_varia_normalized.append(zlib_book_dict['volume'].strip())
        if len((zlib_book_dict.get('edition') or '').strip()) > 0:
            edition_varia_normalized.append(zlib_book_dict['edition'].strip())
        if len((zlib_book_dict.get('year') or '').strip()) > 0:
            edition_varia_normalized.append(zlib_book_dict['year'].strip())
        zlib_book_dict['edition_varia_normalized'] = ', '.join(edition_varia_normalized)
        zlib_book_dict['ipfs_cid'] = ''
        if len(zlib_book.ipfs) > 0:
            zlib_book_dict['ipfs_cid'] = zlib_book.ipfs[0].ipfs_cid
        zlib_book_dict['normalized_filename'] = make_normalized_filename(f"{zlib_book_dict['title']} {zlib_book_dict['author']} {zlib_book_dict['edition_varia_normalized']}", zlib_book_dict['extension'], "zlib", zlib_book_dict['zlibrary_id'])
        zlib_book_dict['zlib_anon_url'] = ''
        if len(zlib_book_dict['pilimi_torrent'] or '') > 0:
            zlib_book_dict['zlib_anon_url'] = make_temp_anon_zlib_link(zlib_book_dict['zlibrary_id'], zlib_book_dict['pilimi_torrent'], zlib_book_dict['extension'])
        zlib_book_dicts.append(zlib_book_dict)

    return zlib_book_dicts

@page.get("/zlib/<int:zlib_id>")
def zlib_book_page(zlib_id):
    zlib_book_dicts = get_zlib_book_dicts(db.session, "zlibrary_id", [zlib_id])

    if len(zlib_book_dicts) == 0:
        return render_template("page/zlib_book.html", header_active="datasets", zlib_id=zlib_id), 404

    zlib_book_dict = zlib_book_dicts[0]
    return render_template(
        "page/zlib_book.html",
        header_active="datasets",
        zlib_id=zlib_id,
        zlib_book_dict=zlib_book_dict,
        zlib_book_json=nice_json(zlib_book_dict),
    )

@page.get("/ol/<string:ol_book_id>")
def ol_book_page(ol_book_id):
    ol_book_id = ol_book_id[0:20]

    with db.engine.connect() as conn:
        ol_book = conn.execute(select(OlBase).where(OlBase.ol_key == f"/books/{ol_book_id}").limit(1)).first()

        if ol_book == None:
            return render_template("page/ol_book.html", header_active="datasets", ol_book_id=ol_book_id), 404

        ol_book_dict = dict(ol_book)
        ol_book_dict['json'] = orjson.loads(ol_book_dict['json'])

        ol_book_dict['work'] = None
        if 'works' in ol_book_dict['json'] and len(ol_book_dict['json']['works']) > 0:
            ol_work = conn.execute(select(OlBase).where(OlBase.ol_key == ol_book_dict['json']['works'][0]['key']).limit(1)).first()
            if ol_work:
                ol_book_dict['work'] = dict(ol_work)
                ol_book_dict['work']['json'] = orjson.loads(ol_book_dict['work']['json'])

        ol_authors = []
        if 'authors' in ol_book_dict['json'] and len(ol_book_dict['json']['authors']) > 0:
            ol_authors = conn.execute(select(OlBase).where(OlBase.ol_key.in_([author['key'] for author in ol_book_dict['json']['authors']])).limit(10)).all()
        elif ol_book_dict['work'] and 'authors' in ol_book_dict['work']['json'] and len(ol_book_dict['work']['json']['authors']) > 0:
            ol_authors = conn.execute(select(OlBase).where(OlBase.ol_key.in_([author['author']['key'] for author in ol_book_dict['work']['json']['authors']])).limit(10)).all()
        ol_book_dict['authors'] = []
        for author in ol_authors:
            author_dict = dict(author)
            author_dict['json'] = orjson.loads(author_dict['json'])
            ol_book_dict['authors'].append(author_dict)

        ol_book_dict['sanitized_isbns'] = make_sanitized_isbns((ol_book_dict['json'].get('isbn_10') or []) + (ol_book_dict['json'].get('isbn_13') or []))
        ol_book_dict['isbns_rich'] = make_isbns_rich(ol_book_dict['sanitized_isbns'])

        ol_book_dict['classifications_normalized'] = []
        for item in (ol_book_dict['json'].get('lc_classifications') or []):
            ol_book_dict['classifications_normalized'].append(('lc_classifications', item))
        for item in (ol_book_dict['json'].get('dewey_decimal_class') or []):
            ol_book_dict['classifications_normalized'].append(('dewey_decimal_class', item))
        for item in (ol_book_dict['json'].get('dewey_number') or []):
            ol_book_dict['classifications_normalized'].append(('dewey_decimal_class', item))
        for classification_type, items in (ol_book_dict['json'].get('classifications') or {}).items():
            for item in items:
                ol_book_dict['classifications_normalized'].append((classification_type, item))

        if ol_book_dict['work']:
            ol_book_dict['work']['classifications_normalized'] = []
            for item in (ol_book_dict['work']['json'].get('lc_classifications') or []):
                ol_book_dict['work']['classifications_normalized'].append(('lc_classifications', item))
            for item in (ol_book_dict['work']['json'].get('dewey_decimal_class') or []):
                ol_book_dict['work']['classifications_normalized'].append(('dewey_decimal_class', item))
            for item in (ol_book_dict['work']['json'].get('dewey_number') or []):
                ol_book_dict['work']['classifications_normalized'].append(('dewey_decimal_class', item))
            for classification_type, items in (ol_book_dict['work']['json'].get('classifications') or {}).items():
                for item in items:
                    ol_book_dict['work']['classifications_normalized'].append((classification_type, item))

        ol_book_dict['identifiers_normalized'] = []
        for item in (ol_book_dict['json'].get('lccn') or []):
            ol_book_dict['identifiers_normalized'].append(('lccn', item.strip()))
        for item in (ol_book_dict['json'].get('oclc_numbers') or []):
            ol_book_dict['identifiers_normalized'].append(('oclc_numbers', item.strip()))
        for identifier_type, items in (ol_book_dict['json'].get('identifiers') or {}).items():
            for item in items:
                ol_book_dict['identifiers_normalized'].append((identifier_type, item.strip()))

        ol_book_dict['languages_normalized'] = [(ol_languages.get(language['key']) or {'name':language['key']})['name'] for language in (ol_book_dict['json'].get('languages') or [])]
        ol_book_dict['translated_from_normalized'] = [(ol_languages.get(language['key']) or {'name':language['key']})['name'] for language in (ol_book_dict['json'].get('translated_from') or [])]

        ol_book_top = {
            'title': '',
            'subtitle': '',
            'authors': '',
            'description': '',
            'cover': f"https://covers.openlibrary.org/b/olid/{ol_book_id}-M.jpg",
        }

        if len(ol_book_top['title'].strip()) == 0 and 'title' in ol_book_dict['json']:
            if 'title_prefix' in ol_book_dict['json']:
                ol_book_top['title'] = ol_book_dict['json']['title_prefix'] + " " + ol_book_dict['json']['title']
            else:
                ol_book_top['title'] = ol_book_dict['json']['title']
        if len(ol_book_top['title'].strip()) == 0 and ol_book_dict['work'] and 'title' in ol_book_dict['work']['json']:
            ol_book_top['title'] = ol_book_dict['work']['json']['title']
        if len(ol_book_top['title'].strip()) == 0:
            ol_book_top['title'] = '(no title)'

        if len(ol_book_top['subtitle'].strip()) == 0 and 'subtitle' in ol_book_dict['json']:
            ol_book_top['subtitle'] = ol_book_dict['json']['subtitle']
        if len(ol_book_top['subtitle'].strip()) == 0 and ol_book_dict['work'] and 'subtitle' in ol_book_dict['work']['json']:
            ol_book_top['subtitle'] = ol_book_dict['work']['json']['subtitle']

        if len(ol_book_top['authors'].strip()) == 0 and 'by_statement' in ol_book_dict['json']:
            ol_book_top['authors'] = ol_book_dict['json']['by_statement'].replace(' ; ', '; ').strip()
            if ol_book_top['authors'][-1] == '.':
                ol_book_top['authors'] = ol_book_top['authors'][0:-1]
        if len(ol_book_top['authors'].strip()) == 0:
            ol_book_top['authors'] = ",".join([author['json']['name'] for author in ol_book_dict['authors']])
        if len(ol_book_top['authors'].strip()) == 0:
            ol_book_top['authors'] = '(no authors)'

        if len(ol_book_top['description'].strip()) == 0 and 'description' in ol_book_dict['json']:
            if type(ol_book_dict['json']['description']) == str:
                ol_book_top['description'] = ol_book_dict['json']['description']
            else:
                ol_book_top['description'] = ol_book_dict['json']['description']['value']
        if len(ol_book_top['description'].strip()) == 0 and ol_book_dict['work'] and 'description' in ol_book_dict['work']['json']:
            if type(ol_book_dict['work']['json']['description']) == str:
                ol_book_top['description'] = ol_book_dict['work']['json']['description']
            else:
                ol_book_top['description'] = ol_book_dict['work']['json']['description']['value']
        if len(ol_book_top['description'].strip()) == 0 and 'first_sentence' in ol_book_dict['json']:
            if type(ol_book_dict['json']['first_sentence']) == str:
                ol_book_top['description'] = ol_book_dict['json']['first_sentence']
            else:
                ol_book_top['description'] = ol_book_dict['json']['first_sentence']['value']
        if len(ol_book_top['description'].strip()) == 0 and ol_book_dict['work'] and 'first_sentence' in ol_book_dict['work']['json']:
            if type(ol_book_dict['work']['json']['first_sentence']) == str:
                ol_book_top['description'] = ol_book_dict['work']['json']['first_sentence']
            else:
                ol_book_top['description'] = ol_book_dict['work']['json']['first_sentence']['value']

        if len(ol_book_dict['json'].get('covers') or []) > 0:
            ol_book_top['cover'] = f"https://covers.openlibrary.org/b/id/{ol_book_dict['json']['covers'][0]}-M.jpg"
        elif ol_book_dict['work'] and len(ol_book_dict['work']['json'].get('covers') or []) > 0:
            ol_book_top['cover'] = f"https://covers.openlibrary.org/b/id/{ol_book_dict['work']['json']['covers'][0]}-M.jpg"

        return render_template(
            "page/ol_book.html",
            header_active="datasets",
            ol_book_id=ol_book_id,
            ol_book_dict=ol_book_dict,
            ol_book_dict_json=nice_json(ol_book_dict),
            ol_book_top=ol_book_top,
            ol_classifications=ol_classifications,
            ol_identifiers=ol_identifiers,
            ol_languages=ol_languages,
        )


def get_lgrsnf_book_dicts(session, key, values):
    # Hack: we explicitly name all the fields, because otherwise some get overwritten below due to lowercasing the column names.
    lgrsnf_books = session.connection().execute(
            select(LibgenrsUpdated, LibgenrsDescription.descr, LibgenrsDescription.toc, LibgenrsHashes.crc32, LibgenrsHashes.edonkey, LibgenrsHashes.aich, LibgenrsHashes.sha1, LibgenrsHashes.tth, LibgenrsHashes.torrent, LibgenrsHashes.btih, LibgenrsHashes.sha256, LibgenrsHashes.ipfs_cid, LibgenrsTopics.topic_descr)
            .join(LibgenrsDescription, LibgenrsUpdated.MD5 == LibgenrsDescription.md5, isouter=True)
            .join(LibgenrsHashes, LibgenrsUpdated.MD5 == LibgenrsHashes.md5, isouter=True)
            .join(LibgenrsTopics, (LibgenrsUpdated.Topic == LibgenrsTopics.topic_id) & (LibgenrsTopics.lang == "en"), isouter=True)
            .where(getattr(LibgenrsUpdated, key).in_(values))
        ).all()

    lgrs_book_dicts = []
    for lgrsnf_book in lgrsnf_books:
        lgrs_book_dict = dict((k.lower(), v) for k,v in dict(lgrsnf_book).items())
        lgrs_book_dict['sanitized_isbns'] = make_sanitized_isbns(lgrsnf_book.Identifier.split(",") + lgrsnf_book.IdentifierWODash.split(","))
        lgrs_book_dict['isbns_rich'] = make_isbns_rich(lgrs_book_dict['sanitized_isbns'])
        lgrs_book_dict['stripped_description'] = strip_description(lgrs_book_dict.get('descr') or '')
        lgrs_book_dict['language_codes'] = get_bcp47_lang_codes(lgrs_book_dict.get('language') or '')
        lgrs_book_dict['cover_url_normalized'] = f"https://libgen.rs/covers/{lgrs_book_dict['coverurl']}" if len(lgrs_book_dict.get('coverurl') or '') > 0 else ''

        edition_varia_normalized = []
        if len((lgrs_book_dict.get('series') or '').strip()) > 0:
            edition_varia_normalized.append(lgrs_book_dict['series'].strip())
        if len((lgrs_book_dict.get('volume') or '').strip()) > 0:
            edition_varia_normalized.append(lgrs_book_dict['volume'].strip())
        if len((lgrs_book_dict.get('edition') or '').strip()) > 0:
            edition_varia_normalized.append(lgrs_book_dict['edition'].strip())
        if len((lgrs_book_dict.get('periodical') or '').strip()) > 0:
            edition_varia_normalized.append(lgrs_book_dict['periodical'].strip())
        if len((lgrs_book_dict.get('year') or '').strip()) > 0:
            edition_varia_normalized.append(lgrs_book_dict['year'].strip())
        lgrs_book_dict['edition_varia_normalized'] = ', '.join(edition_varia_normalized)

        lgrs_book_dict['normalized_filename'] = make_normalized_filename(f"{lgrs_book_dict['title']} {lgrs_book_dict['author']} {lgrs_book_dict['edition_varia_normalized']}", lgrs_book_dict['extension'], "libgenrs-nf", lgrs_book_dict['id'])

        lgrs_book_dicts.append(lgrs_book_dict)

    return lgrs_book_dicts


@page.get("/lgrs/nf/<int:lgrsnf_book_id>")
def lgrsnf_book_page(lgrsnf_book_id):
    lgrs_book_dicts = get_lgrsnf_book_dicts(db.session, "ID", [lgrsnf_book_id])

    if len(lgrs_book_dicts) == 0:
        return render_template("page/lgrs_book.html", header_active="datasets", lgrs_type='nf', lgrs_book_id=lgrsnf_book_id), 404

    return render_template(
        "page/lgrs_book.html",
        header_active="datasets",
        lgrs_type='nf',
        lgrs_book_id=lgrsnf_book_id,
        lgrs_book_dict=lgrs_book_dicts[0],
        lgrs_book_dict_json=nice_json(lgrs_book_dicts[0]),
    )


def get_lgrsfic_book_dicts(session, key, values):
    # Hack: we explicitly name all the fields, because otherwise some get overwritten below due to lowercasing the column names.
    lgrsfic_books = session.connection().execute(
            select(LibgenrsFiction, LibgenrsFictionDescription.Descr, LibgenrsFictionHashes.crc32, LibgenrsFictionHashes.edonkey, LibgenrsFictionHashes.aich, LibgenrsFictionHashes.sha1, LibgenrsFictionHashes.tth, LibgenrsFictionHashes.btih, LibgenrsFictionHashes.sha256, LibgenrsFictionHashes.ipfs_cid)
            .join(LibgenrsFictionDescription, LibgenrsFiction.MD5 == LibgenrsFictionDescription.MD5, isouter=True)
            .join(LibgenrsFictionHashes, LibgenrsFiction.MD5 == LibgenrsFictionHashes.md5, isouter=True)
            .where(getattr(LibgenrsFiction, key).in_(values))
        ).all()

    lgrs_book_dicts = []

    for lgrsfic_book in lgrsfic_books:
        lgrs_book_dict = dict((k.lower(), v) for k,v in dict(lgrsfic_book).items())
        lgrs_book_dict['sanitized_isbns'] = make_sanitized_isbns(lgrsfic_book.Identifier.split(","))
        lgrs_book_dict['isbns_rich'] = make_isbns_rich(lgrs_book_dict['sanitized_isbns'])
        lgrs_book_dict['stripped_description'] = strip_description(lgrs_book_dict.get('descr') or '')
        lgrs_book_dict['language_codes'] = get_bcp47_lang_codes(lgrs_book_dict.get('language') or '')
        lgrs_book_dict['cover_url_normalized'] = f"https://libgen.rs/fictioncovers/{lgrs_book_dict['coverurl']}" if len(lgrs_book_dict.get('coverurl') or '') > 0 else ''

        edition_varia_normalized = []
        if len((lgrs_book_dict.get('series') or '').strip()) > 0:
            edition_varia_normalized.append(lgrs_book_dict['series'].strip())
        if len((lgrs_book_dict.get('edition') or '').strip()) > 0:
            edition_varia_normalized.append(lgrs_book_dict['edition'].strip())
        if len((lgrs_book_dict.get('year') or '').strip()) > 0:
            edition_varia_normalized.append(lgrs_book_dict['year'].strip())
        lgrs_book_dict['edition_varia_normalized'] = ', '.join(edition_varia_normalized)

        lgrs_book_dict['normalized_filename'] = make_normalized_filename(f"{lgrs_book_dict['title']} {lgrs_book_dict['author']} {lgrs_book_dict['edition_varia_normalized']}", lgrs_book_dict['extension'], "libgenrs-fic", lgrs_book_dict['id'])

        lgrs_book_dicts.append(lgrs_book_dict)

    return lgrs_book_dicts


@page.get("/lgrs/fic/<int:lgrsfic_book_id>")
def lgrsfic_book_page(lgrsfic_book_id):
    lgrs_book_dicts = get_lgrsfic_book_dicts(db.session, "ID", [lgrsfic_book_id])

    if len(lgrs_book_dicts) == 0:
        return render_template("page/lgrs_book.html", header_active="datasets", lgrs_type='fic', lgrs_book_id=lgrsfic_book_id), 404

    return render_template(
        "page/lgrs_book.html",
        header_active="datasets",
        lgrs_type='fic',
        lgrs_book_id=lgrsfic_book_id,
        lgrs_book_dict=lgrs_book_dicts[0],
        lgrs_book_dict_json=nice_json(lgrs_book_dicts[0]),
    )

libgenli_elem_descr_output = None
def libgenli_elem_descr(conn):
    global libgenli_elem_descr_output
    if libgenli_elem_descr_output == None:
        all_descr = conn.execute(select(LibgenliElemDescr).limit(10000)).all()
        output = {}
        for descr in all_descr:
            output[descr.key] = dict(descr)
        libgenli_elem_descr_output = output
    return libgenli_elem_descr_output

def lgli_normalize_meta_field(field_name):
    return field_name.lower().replace(' ', '').replace('-', '').replace('.', '').replace('/', '').replace('(','').replace(')', '')

def lgli_map_descriptions(descriptions):
    descrs_mapped = {}
    for descr in descriptions:
        normalized_base_field = lgli_normalize_meta_field(descr['meta']['name_en'])
        normalized_base_field_first = normalized_base_field + '_first'
        normalized_base_field_multiple = normalized_base_field + '_multiple'
        if normalized_base_field not in descrs_mapped:
            descrs_mapped[normalized_base_field_first] = descr['value']
        if normalized_base_field_multiple in descrs_mapped:
            descrs_mapped[normalized_base_field_multiple].append(descr['value'])
        else:
            descrs_mapped[normalized_base_field_multiple] = [descr['value']]
        for i in [1,2,3]:
            add_field_name = f"name_add{i}_en"
            add_field_value = f"value_add{i}"
            if len(descr['meta'][add_field_name]) > 0:
                normalized_add_field = normalized_base_field + "_" + lgli_normalize_meta_field(descr['meta'][add_field_name])
                normalized_add_field_first = normalized_add_field + '_first'
                normalized_add_field_multiple = normalized_add_field + '_multiple'
                if normalized_add_field not in descrs_mapped:
                    descrs_mapped[normalized_add_field_first] = descr[add_field_value]
                if normalized_add_field_multiple in descrs_mapped:
                    descrs_mapped[normalized_add_field_multiple].append(descr[add_field_value])
                else:
                    descrs_mapped[normalized_add_field_multiple] = [descr[add_field_value]]
        if len(descr.get('publisher_title') or '') > 0:
            normalized_base_field = 'publisher_title'
            normalized_base_field_first = normalized_base_field + '_first'
            normalized_base_field_multiple = normalized_base_field + '_multiple'
            if normalized_base_field not in descrs_mapped:
                descrs_mapped[normalized_base_field_first] = descr['publisher_title']
            if normalized_base_field_multiple in descrs_mapped:
                descrs_mapped[normalized_base_field_multiple].append(descr['publisher_title'])
            else:
                descrs_mapped[normalized_base_field_multiple] = [descr['publisher_title']]

    return descrs_mapped

lgli_topic_mapping = {
    'l': 'Non-fiction ("libgen")',
    's': 'Standards document',
    'm': 'Magazine',
    'c': 'Comic',
    'f': 'Fiction',
    'r': 'Russian Fiction',
    'a': 'Journal article (Sci-Hub/scimag)'
}
# Hardcoded from the `descr_elems` table.
lgli_edition_type_mapping = {
    "b":"book",
    "ch":"book-chapter",
    "bpart":"book-part",
    "bsect":"book-section",
    "bs":"book-series",
    "bset":"book-set",
    "btrack":"book-track",
    "component":"component",
    "dataset":"dataset",
    "diss":"dissertation",
    "j":"journal",
    "a":"journal-article",
    "ji":"journal-issue",
    "jv":"journal-volume",
    "mon":"monograph",
    "oth":"other",
    "peer-review":"peer-review",
    "posted-content":"posted-content",
    "proc":"proceedings",
    "proca":"proceedings-article",
    "ref":"reference-book",
    "refent":"reference-entry",
    "rep":"report",
    "repser":"report-series",
    "s":"standard",
    "fnz":"Fanzine",
    "m":"Magazine issue",
    "col":"Collection",
    "chb":"Chapbook",
    "nonfict":"Nonfiction",
    "omni":"Omnibus",
    "nov":"Novel",
    "ant":"Anthology",
    "c":"Comics issue",
}
lgli_issue_other_fields = [
    "issue_number_in_year",
    "issue_year_number",
    "issue_number",
    "issue_volume",
    "issue_split",
    "issue_total_number",
    "issue_first_page",
    "issue_last_page",
    "issue_year_end",
    "issue_month_end",
    "issue_day_end",
    "issue_closed",
]
lgli_standard_info_fields = [
    "standardtype",
    "standardtype_standartnumber",
    "standardtype_standartdate",
    "standartnumber",
    "standartstatus",
    "standartstatus_additionalstandartstatus",
]
lgli_date_info_fields = [
    "datepublication",
    "dateintroduction",
    "dateactualizationtext",
    "dateregistration",
    "dateactualizationdescr",
    "dateexpiration",
    "datelastedition",
]
# Hardcoded from the `libgenli_elem_descr` table.
lgli_identifiers = {
    "doi": { "label": "DOI", "url": "https://doi.org/%s", "description": "Digital Object Identifier"},
    "issn_multiple": { "label": "ISSN", "url": "https://urn.issn.org/urn:issn:%s", "description": "International Standard Serial Number"},
    "pii_multiple": { "label": "PII", "url": "", "description": "Publisher Item Identifier", "website": "https://en.wikipedia.org/wiki/Publisher_Item_Identifier"},
    "pmcid_multiple": { "label": "PMC ID", "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/%s/", "description": "PubMed Central ID"},
    "pmid_multiple": { "label": "PMID", "url": "https://pubmed.ncbi.nlm.nih.gov/%s/", "description": "PubMed ID"},
    "asin_multiple": { "label": "ASIN", "url": "https://www.amazon.com/dp/%s", "description": "Amazon Standard Identification Number"},
    "bl_multiple": { "label": "BL", "url": "http://explore.bl.uk/primo_library/libweb/action/dlDisplay.do?vid=BLVU1&amp;docId=BLL01%s", "description": "The British Library"},
    "bnb_multiple": { "label": "BNB", "url": "http://search.bl.uk/primo_library/libweb/action/search.do?fn=search&vl(freeText0)=%s", "description": "The British National Bibliography"},
    "bnf_multiple": { "label": "BNF", "url": "http://catalogue.bnf.fr/ark:/12148/%s", "description": "Bibliotheque nationale de France"},
    "copac_multiple": { "label": "COPAC", "url": "http://copac.jisc.ac.uk/id/%s?style=html", "description": "UK/Irish union catalog"},
    "dnb_multiple": { "label": "DNB", "url": "http://d-nb.info/%s", "description": "Deutsche Nationalbibliothek"},
    "fantlabeditionid_multiple": { "label": "FantLab Edition ID", "url": "https://fantlab.ru/edition%s", "description": "Лаболатория фантастики"},
    "goodreads_multiple": { "label": "Goodreads", "url": "http://www.goodreads.com/book/show/%s", "description": "Goodreads social cataloging site"},
    "jnbjpno_multiple": { "label": "JNB/JPNO", "url": "https://iss.ndl.go.jp/api/openurl?ndl_jpno=%s&amp;locale=en", "description": "The Japanese National Bibliography"},
    "lccn_multiple": { "label": "LCCN", "url": "http://lccn.loc.gov/%s", "description": "Library of Congress Control Number"},
    "ndl_multiple": { "label": "NDL", "url": "http://id.ndl.go.jp/bib/%s/eng", "description": "National Diet Library"},
    "oclcworldcat_multiple": { "label": "OCLC/WorldCat", "url": "https://www.worldcat.org/oclc/%s", "description": "Online Computer Library Center"},
    "openlibrary_multiple": { "label": "Open Library", "url": "https://openlibrary.org/books/%s", "description": ""},
    "sfbg_multiple": { "label": "SFBG", "url": "http://www.sfbg.us/book/%s", "description": "Catalog of books published in Bulgaria"},
    "bn_multiple": { "label": "BN", "url": "http://www.barnesandnoble.com/s/%s", "description": "Barnes and Noble"},
    "ppn_multiple": { "label": "PPN", "url": "http://picarta.pica.nl/xslt/DB=3.9/XMLPRS=Y/PPN?PPN=%s", "description": "De Nederlandse Bibliografie Pica Productie Nummer"},
    "audibleasin_multiple": { "label": "Audible-ASIN", "url": "https://www.audible.com/pd/%s", "description": "Audible ASIN"},
    "ltf_multiple": { "label": "LTF", "url": "http://www.tercerafundacion.net/biblioteca/ver/libro/%s", "description": "La Tercera Fundaci&#243;n"},
    "kbr_multiple": { "label": "KBR", "url": "https://opac.kbr.be/Library/doc/SYRACUSE/%s/", "description": "De Belgische Bibliografie/La Bibliographie de Belgique"},
    "reginald1_multiple": { "label": "Reginald-1", "url": "", "description": "R. Reginald. Science Fiction and Fantasy Literature: A Checklist, 1700-1974, with Contemporary Science Fiction Authors II. Gale Research Co., 1979, 1141p."},
    "reginald3_multiple": { "label": "Reginald-3", "url": "", "description": "Robert Reginald. Science Fiction and Fantasy Literature, 1975-1991: A Bibliography of Science Fiction, Fantasy, and Horror Fiction Books and Nonfiction Monographs. Gale Research Inc., 1992, 1512 p."},
    "bleilergernsback_multiple": { "label": "Bleiler Gernsback", "url": "", "description": "Everett F. Bleiler, Richard Bleiler. Science-Fiction: The Gernsback Years. Kent State University Press, 1998, xxxii+730pp"},
    "bleilersupernatural_multiple": { "label": "Bleiler Supernatural", "url": "", "description": "Everett F. Bleiler. The Guide to Supernatural Fiction. Kent State University Press, 1983, xii+723 p."},
    "bleilerearlyyears_multiple": { "label": "Bleiler Early Years", "url": "", "description": "Richard Bleiler, Everett F. Bleiler. Science-Fiction: The Early Years. Kent State University Press, 1991, xxiii+998 p."},
    "nilf_multiple": { "label": "NILF", "url": "http://nilf.it/%s/", "description": "Numero Identificativo della Letteratura Fantastica / Fantascienza"},
    "noosfere_multiple": { "label": "NooSFere", "url": "https://www.noosfere.org/livres/niourf.asp?numlivre=%s", "description": "NooSFere"},
    "sfleihbuch_multiple": { "label": "SF-Leihbuch", "url": "http://www.sf-leihbuch.de/index.cfm?bid=%s", "description": "Science Fiction-Leihbuch-Datenbank"},
    "nla_multiple": { "label": "NLA", "url": "https://nla.gov.au/nla.cat-vn%s", "description": "National Library of Australia"},
    "porbase_multiple": { "label": "PORBASE", "url": "http://id.bnportugal.gov.pt/bib/porbase/%s", "description": "Biblioteca Nacional de Portugal"},
    "isfdbpubideditions_multiple": { "label": "ISFDB (editions)", "url": "http://www.isfdb.org/cgi-bin/pl.cgi?%s", "description": ""},
    "googlebookid_multiple": { "label": "Google Books", "url": "https://books.google.com/books?id=%s", "description": ""},
    "jstorstableid_multiple": { "label": "JSTOR Stable", "url": "https://www.jstor.org/stable/%s", "description": ""},
    "crossrefbookid_multiple": { "label": "Crossref", "url": "https://data.crossref.org/depositorreport?pubid=%s", "description":""},
}
# Hardcoded from the `libgenli_elem_descr` table.
lgli_classifications = {
    "classification_multiple": { "label": "Classification", "url": "", "description": "" },
    "classificationokp_multiple": { "label": "OKP", "url": "https://classifikators.ru/okp/%s", "description": "" },
    "classificationgostgroup_multiple": { "label": "GOST group", "url": "", "description": "", "website": "https://en.wikipedia.org/wiki/GOST" },
    "classificationoks_multiple": { "label": "OKS", "url": "", "description": "" },
    "libraryofcongressclassification_multiple": { "label": "LCC", "url": "", "description": "Library of Congress Classification", "website": "https://en.wikipedia.org/wiki/Library_of_Congress_Classification" },
    "udc_multiple": { "label": "UDC", "url": "https://libgen.li/biblioservice.php?value=%s&type=udc", "description": "Universal Decimal Classification", "website": "https://en.wikipedia.org/wiki/Universal_Decimal_Classification" },
    "ddc_multiple": { "label": "DDC", "url": "https://libgen.li/biblioservice.php?value=%s&type=ddc", "description": "Dewey Decimal", "website": "https://en.wikipedia.org/wiki/List_of_Dewey_Decimal_classes" },
    "lbc_multiple": { "label": "LBC", "url": "https://libgen.li/biblioservice.php?value=%s&type=bbc", "description": "Library-Bibliographical Classification", "website": "https://www.isko.org/cyclo/lbc" },
}

def get_lgli_file_dicts(session, key, values):
    description_metadata = libgenli_elem_descr(session.connection())

    lgli_files = session.scalars(
        select(LibgenliFiles)
            .where(getattr(LibgenliFiles, key).in_(values))
            .options(
                db.defaultload("add_descrs").load_only("key", "value", "value_add1", "value_add2", "value_add3"),
                db.defaultload("editions.add_descrs").load_only("key", "value", "value_add1", "value_add2", "value_add3"),
                db.defaultload("editions.series").load_only("title", "publisher", "volume", "volume_name"),
                db.defaultload("editions.series.issn_add_descrs").load_only("value"),
                db.defaultload("editions.add_descrs.publisher").load_only("title"),
            )
    ).all()

    lgli_file_dicts = []
    for lgli_file in lgli_files:
        lgli_file_dict = lgli_file.to_dict()
        lgli_file_descriptions_dict = [{**descr.to_dict(), 'meta': description_metadata[descr.key]} for descr in lgli_file.add_descrs]
        lgli_file_dict['descriptions_mapped'] = lgli_map_descriptions(lgli_file_descriptions_dict)
        lgli_file_dict['editions'] = []

        for edition in lgli_file.editions:
            edition_dict = {
                **edition.to_dict(),
                'issue_series_title': edition.series.title if edition.series else '',
                'issue_series_publisher': edition.series.publisher if edition.series else '',
                'issue_series_volume_number': edition.series.volume if edition.series else '',
                'issue_series_volume_name': edition.series.volume_name if edition.series else '',
                'issue_series_issn': edition.series.issn_add_descrs[0].value if edition.series and edition.series.issn_add_descrs else '',
            }

            edition_dict['descriptions_mapped'] = lgli_map_descriptions({
                **descr.to_dict(),
                'meta': description_metadata[descr.key],
                'publisher_title': descr.publisher[0].title if len(descr.publisher) > 0 else '',
            } for descr in edition.add_descrs)
            edition_dict['authors_normalized'] = edition_dict['author'].strip()
            if len(edition_dict['authors_normalized']) == 0 and len(edition_dict['descriptions_mapped'].get('author_multiple') or []) > 0:
                edition_dict['authors_normalized'] = ", ".join(author.strip() for author in edition_dict['descriptions_mapped']['author_multiple'])

            edition_dict['cover_url_guess'] = edition_dict['cover_url']
            if len(edition_dict['descriptions_mapped'].get('coverurl_first') or '') > 0:
                edition_dict['cover_url_guess'] = edition_dict['descriptions_mapped']['coverurl_first']
            if edition_dict['cover_exists'] > 0:
                edition_dict['cover_url_guess'] = f"https://libgen.li/editioncovers/{(edition_dict['e_id'] // 1000) * 1000}/{edition_dict['e_id']}.jpg"

            issue_other_fields = dict((key, edition_dict[key]) for key in lgli_issue_other_fields if edition_dict[key] not in ['', '0', 0, None])
            if len(issue_other_fields) > 0:
                edition_dict['issue_other_fields_json'] = nice_json(issue_other_fields)
            standard_info_fields = dict((key, edition_dict['descriptions_mapped'][key + '_multiple']) for key in lgli_standard_info_fields if edition_dict['descriptions_mapped'].get(key + '_multiple') not in ['', '0', 0, None])
            if len(standard_info_fields) > 0:
                edition_dict['standard_info_fields_json'] = nice_json(standard_info_fields)
            date_info_fields = dict((key, edition_dict['descriptions_mapped'][key + '_multiple']) for key in lgli_date_info_fields if edition_dict['descriptions_mapped'].get(key + '_multiple') not in ['', '0', 0, None])
            if len(date_info_fields) > 0:
                edition_dict['date_info_fields_json'] = nice_json(date_info_fields)

            issue_series_title_normalized = []
            if len((edition_dict['issue_series_title'] or '').strip()) > 0:
                issue_series_title_normalized.append(edition_dict['issue_series_title'].strip())
            if len((edition_dict['issue_series_volume_name'] or '').strip()) > 0:
                issue_series_title_normalized.append(edition_dict['issue_series_volume_name'].strip())
            if len((edition_dict['issue_series_volume_number'] or '').strip()) > 0:
                issue_series_title_normalized.append('Volume ' + edition_dict['issue_series_volume_number'].strip())
            elif len((issue_other_fields.get('issue_year_number') or '').strip()) > 0:
                issue_series_title_normalized.append('#' + issue_other_fields['issue_year_number'].strip())
            edition_dict['issue_series_title_normalized'] = ", ".join(issue_series_title_normalized) if len(issue_series_title_normalized) > 0 else ''

            edition_dict['publisher_normalized'] = ''
            if len((edition_dict['publisher'] or '').strip()) > 0:
                edition_dict['publisher_normalized'] = edition_dict['publisher'].strip()
            elif len((edition_dict['descriptions_mapped'].get('publisher_title_first') or '').strip()) > 0:
                edition_dict['publisher_normalized'] = edition_dict['descriptions_mapped']['publisher_title_first'].strip()
            elif len((edition_dict['issue_series_publisher'] or '').strip()) > 0:
                edition_dict['publisher_normalized'] = edition_dict['issue_series_publisher'].strip()
                if len((edition_dict['issue_series_issn'] or '').strip()) > 0:
                    edition_dict['publisher_normalized'] += ' (ISSN ' + edition_dict['issue_series_issn'].strip() + ')'

            date_normalized = []
            if len((edition_dict['year'] or '').strip()) > 0:
                date_normalized.append(edition_dict['year'].strip())
            if len((edition_dict['month'] or '').strip()) > 0:
                date_normalized.append(edition_dict['month'].strip())
            if len((edition_dict['day'] or '').strip()) > 0:
                date_normalized.append(edition_dict['day'].strip())
            edition_dict['date_normalized'] = " ".join(date_normalized)

            edition_varia_normalized = []
            if len((edition_dict['issue_series_title_normalized'] or '').strip()) > 0:
                edition_varia_normalized.append(edition_dict['issue_series_title_normalized'].strip())
            if len((edition_dict['issue_number'] or '').strip()) > 0:
                edition_varia_normalized.append('#' + edition_dict['issue_number'].strip())
            if len((edition_dict['issue_year_number'] or '').strip()) > 0:
                edition_varia_normalized.append('#' + edition_dict['issue_year_number'].strip())
            if len((edition_dict['issue_volume'] or '').strip()) > 0:
                edition_varia_normalized.append(edition_dict['issue_volume'].strip())
            if (len((edition_dict['issue_first_page'] or '').strip()) > 0) or (len((edition_dict['issue_last_page'] or '').strip()) > 0):
                edition_varia_normalized.append('pages ' + (edition_dict['issue_first_page'] or '').strip() + '-' + (edition_dict['issue_last_page'] or '').strip())
            if len((edition_dict['series_name'] or '').strip()) > 0:
                edition_varia_normalized.append(edition_dict['series_name'].strip())
            if len((edition_dict['edition'] or '').strip()) > 0:
                edition_varia_normalized.append(edition_dict['edition'].strip())
            if len((edition_dict['date_normalized'] or '').strip()) > 0:
                edition_varia_normalized.append(edition_dict['date_normalized'].strip())
            edition_dict['edition_varia_normalized'] = ', '.join(edition_varia_normalized)

            language_multiple_codes = [get_bcp47_lang_codes(language_code) for language_code in (edition_dict['descriptions_mapped'].get('language_multiple') or [])]
            edition_dict['language_codes'] = combine_bcp47_lang_codes(language_multiple_codes)
            languageoriginal_multiple_codes = [get_bcp47_lang_codes(language_code) for language_code in (edition_dict['descriptions_mapped'].get('languageoriginal_multiple') or [])]
            edition_dict['languageoriginal_codes'] = combine_bcp47_lang_codes(languageoriginal_multiple_codes)

            edition_dict['identifiers_normalized'] = []
            if len(edition_dict['doi'].strip()) > 0:
                edition_dict['identifiers_normalized'].append(('doi', edition_dict['doi'].strip()))
            for key, values in edition_dict['descriptions_mapped'].items():
                if key in lgli_identifiers:
                    for value in values:
                        edition_dict['identifiers_normalized'].append((key, value.strip()))

            edition_dict['classifications_normalized'] = []
            for key, values in edition_dict['descriptions_mapped'].items():
                if key in lgli_classifications:
                    for value in values:
                        edition_dict['classifications_normalized'].append((key, value.strip()))

            edition_dict['sanitized_isbns'] = make_sanitized_isbns(edition_dict['descriptions_mapped'].get('isbn_multiple') or [])
            edition_dict['isbns_rich'] = make_isbns_rich(edition_dict['sanitized_isbns'])

            edition_dict['stripped_description'] = ''
            if len(edition_dict['descriptions_mapped'].get('description_multiple') or []) > 0:
                edition_dict['stripped_description'] = strip_description("\n\n".join(edition_dict['descriptions_mapped']['description_multiple']))

            lgli_file_dict['editions'].append(edition_dict)

        lgli_file_dict['cover_url_guess'] = ''
        if lgli_file_dict['cover_exists'] > 0:
            lgli_file_dict['cover_url_guess'] = f"https://libgen.li/comicscovers/{lgli_file_dict['md5'].lower()}.jpg"
            if lgli_file_dict['libgen_id'] and lgli_file_dict['libgen_id'] > 0:
                lgli_file_dict['cover_url_guess'] = f"https://libgen.li/covers/{(lgli_file_dict['libgen_id'] // 1000) * 1000}/{lgli_file_dict['md5'].lower()}.jpg"
            if lgli_file_dict['comics_id'] and lgli_file_dict['comics_id'] > 0:
                lgli_file_dict['cover_url_guess'] = f"https://libgen.li/comicscovers_repository/{(lgli_file_dict['comics_id'] // 1000) * 1000}/{lgli_file_dict['md5'].lower()}.jpg"
            if lgli_file_dict['fiction_id'] and lgli_file_dict['fiction_id'] > 0:
                lgli_file_dict['cover_url_guess'] = f"https://libgen.li/fictioncovers/{(lgli_file_dict['fiction_id'] // 1000) * 1000}/{lgli_file_dict['md5'].lower()}.jpg"
            if lgli_file_dict['fiction_rus_id'] and lgli_file_dict['fiction_rus_id'] > 0:
                lgli_file_dict['cover_url_guess'] = f"https://libgen.li/fictionruscovers/{(lgli_file_dict['fiction_rus_id'] // 1000) * 1000}/{lgli_file_dict['md5'].lower()}.jpg"
            if lgli_file_dict['magz_id'] and lgli_file_dict['magz_id'] > 0:
                lgli_file_dict['cover_url_guess'] = f"https://libgen.li/magzcovers/{(lgli_file_dict['magz_id'] // 1000) * 1000}/{lgli_file_dict['md5'].lower()}.jpg"

        lgli_file_dict['cover_url_guess_normalized'] = ''
        if len(lgli_file_dict['cover_url_guess']) > 0:
            lgli_file_dict['cover_url_guess_normalized'] = lgli_file_dict['cover_url_guess']
        else:
            for edition_dict in lgli_file_dict['editions']:
                if len(edition_dict['cover_url_guess']) > 0:
                    lgli_file_dict['cover_url_guess_normalized'] = edition_dict['cover_url_guess']

        lgli_file_dict['scimag_url_guess'] = ''
        if len(lgli_file_dict['scimag_archive_path']) > 0:
            lgli_file_dict['scimag_url_guess'] = lgli_file_dict['scimag_archive_path'].replace('\\', '/')
            if lgli_file_dict['scimag_url_guess'].endswith('.' + lgli_file_dict['extension']):
                lgli_file_dict['scimag_url_guess'] = lgli_file_dict['scimag_url_guess'][0:-len('.' + lgli_file_dict['extension'])]
            if lgli_file_dict['scimag_url_guess'].startswith('10.0000/') and '%2F' in lgli_file_dict['scimag_url_guess']:
                lgli_file_dict['scimag_url_guess'] = 'http://' + lgli_file_dict['scimag_url_guess'][len('10.0000/'):].replace('%2F', '/')
            else:
                lgli_file_dict['scimag_url_guess'] = 'https://doi.org/' + lgli_file_dict['scimag_url_guess']

        lgli_file_dicts.append(lgli_file_dict)

    return lgli_file_dicts


@page.get("/lgli/file/<int:lgli_file_id>")
def lgli_file_page(lgli_file_id):
    lgli_file_dicts = get_lgli_file_dicts(db.session, "f_id", [lgli_file_id])

    if len(lgli_file_dicts) == 0:
        return render_template("page/lgli_file.html", header_active="datasets", lgli_file_id=lgli_file_id), 404

    lgli_file_dict = lgli_file_dicts[0]

    lgli_file_top = { 'title': '', 'author': '', 'description': '' }
    if len(lgli_file_dict['editions']) > 0:
        for edition_dict in lgli_file_dict['editions']:
            if len(edition_dict['title'].strip()) > 0:
                lgli_file_top['title'] = edition_dict['title'].strip()
                break
        if len(lgli_file_top['title'].strip()) == 0:
            lgli_file_top['title'] = lgli_file_dict['locator'].split('\\')[-1].strip()
        else:
            lgli_file_top['description'] = lgli_file_dict['locator'].split('\\')[-1].strip()
        for edition_dict in lgli_file_dict['editions']:
            if len(edition_dict['authors_normalized']) > 0:
                lgli_file_top['author'] = edition_dict['authors_normalized']
                break
        for edition_dict in lgli_file_dict['editions']:
            if len(edition_dict['descriptions_mapped'].get('description_multiple') or []) > 0:
                lgli_file_top['description'] = strip_description("\n\n".join(edition_dict['descriptions_mapped']['description_multiple']))
        for edition_dict in lgli_file_dict['editions']:
            if len(edition_dict['edition_varia_normalized']) > 0:
                lgli_file_top['description'] = strip_description(edition_dict['edition_varia_normalized']) + ('\n\n' if len(lgli_file_top['description']) > 0 else '') + lgli_file_top['description']
                break
    if len(lgli_file_dict['scimag_archive_path']) > 0:
        lgli_file_top['title'] = lgli_file_dict['scimag_archive_path']

    return render_template(
        "page/lgli_file.html",
        header_active="datasets",
        lgli_file_id=lgli_file_id,
        lgli_file_dict=lgli_file_dict,
        lgli_file_top=lgli_file_top,
        lgli_file_dict_json=nice_json(lgli_file_dict),
        lgli_topic_mapping=lgli_topic_mapping,
        lgli_edition_type_mapping=lgli_edition_type_mapping,
        lgli_identifiers=lgli_identifiers,
        lgli_classifications=lgli_classifications,
    )

@page.get("/isbn/<string:isbn_input>")
def isbn_page(isbn_input):
    isbn_input = isbn_input[0:20]

    canonical_isbn13 = isbnlib.get_canonical_isbn(isbn_input, output='isbn13')
    if len(canonical_isbn13) != 13 or len(isbnlib.info(canonical_isbn13)) == 0:
        # TODO, check if a different prefix would help, like in
        # https://github.com/inventaire/isbn3/blob/d792973ac0e13a48466d199b39326c96026b7fc3/lib/audit.js
        return render_template("page/isbn.html", header_active="datasets", isbn_input=isbn_input)

    if canonical_isbn13 != isbn_input:
        return redirect(f"/isbn/{canonical_isbn13}", code=301)

    barcode_bytesio = io.BytesIO()
    barcode.ISBN13(canonical_isbn13, writer=barcode.writer.SVGWriter()).write(barcode_bytesio)
    barcode_bytesio.seek(0)
    barcode_svg = barcode_bytesio.read().decode('utf-8').replace('fill:white', 'fill:transparent').replace(canonical_isbn13, '')

    isbn13_mask = isbnlib.mask(canonical_isbn13)
    isbn_dict = {
        "ean13": isbnlib.ean13(canonical_isbn13),
        "isbn10": isbnlib.to_isbn10(canonical_isbn13),
        "doi": isbnlib.doi(canonical_isbn13),
        "info": isbnlib.info(canonical_isbn13),
        "mask": isbn13_mask,
        "mask_split": isbn13_mask.split('-'),
        "barcode_svg": barcode_svg,
    }
    if isbn_dict['isbn10']:
        isbn_dict['mask10'] = isbnlib.mask(isbn_dict['isbn10'])

    with db.engine.connect() as conn:
        isbndb_books = {}
        if isbn_dict['isbn10']:
            isbndb10_all = conn.execute(select(IsbndbIsbns).where(IsbndbIsbns.isbn10 == isbn_dict['isbn10']).limit(100)).all()
            for isbndb10 in isbndb10_all:
                # ISBNdb has a bug where they just chop off the prefix of ISBN-13, which is incorrect if the prefix is anything
                # besides "978"; so we double-check on this.
                if isbndb10['isbn13'][0:3] == '978':
                    isbndb_books[isbndb10['isbn13'] + '-' + isbndb10['isbn10']] = { **isbndb10, 'source_isbn': isbn_dict['isbn10'], 'matchtype': 'ISBN-10' }
        isbndb13_all = conn.execute(select(IsbndbIsbns).where(IsbndbIsbns.isbn13 == canonical_isbn13).limit(100)).all()
        for isbndb13 in isbndb13_all:
            key = isbndb13['isbn13'] + '-' + isbndb13['isbn10']
            if key in isbndb_books:
                isbndb_books[key]['matchtype'] = 'ISBN-10 and ISBN-13'
            else:
                isbndb_books[key] = { **isbndb13, 'source_isbn': canonical_isbn13, 'matchtype': 'ISBN-13' }

        for isbndb_book in isbndb_books.values():
            isbndb_book['json'] = orjson.loads(isbndb_book['json'])
        # There seem to be a bunch of ISBNdb books with only a language, which is not very useful.
        isbn_dict['isbndb'] = [isbndb_book for isbndb_book in isbndb_books.values() if len(isbndb_book['json'].get('title') or '') > 0 or len(isbndb_book['json'].get('title_long') or '') > 0 or len(isbndb_book['json'].get('authors') or []) > 0 or len(isbndb_book['json'].get('synopsis') or '') > 0 or len(isbndb_book['json'].get('overview') or '') > 0]

        for isbndb_dict in isbn_dict['isbndb']:
            isbndb_dict['language_codes'] = get_bcp47_lang_codes(isbndb_dict['json'].get('language') or '')
            isbndb_dict['languages_and_codes'] = [(langcodes.get(lang_code).display_name(), lang_code) for lang_code in isbndb_dict['language_codes']]
            isbndb_dict['stripped_description'] = '\n\n'.join([strip_description(isbndb_dict['json'].get('synopsis') or ''),  strip_description(isbndb_dict['json'].get('overview') or '')]).strip()

        search_md5_objs_raw = conn.execute(select(ComputedSearchMd5Objs.md5, ComputedSearchMd5Objs.json).where(match(ComputedSearchMd5Objs.json, against=f'"{canonical_isbn13}"').in_boolean_mode()).limit(100)).all()
        # Get the language codes from the first match.
        language_codes_probs = {}
        if len(isbn_dict['isbndb']) > 0:
            for lang_code in isbn_dict['isbndb'][0]['language_codes']:
                language_codes_probs[lang_code] = 1.0
        for lang_code, quality in request.accept_languages:
            for code in get_bcp47_lang_codes(lang_code):
                language_codes_probs[code] = quality
        search_md5_objs = sort_search_md5_objs([SearchMd5Obj(search_md5_obj_raw.md5, *orjson.loads(search_md5_obj_raw.json)) for search_md5_obj_raw in search_md5_objs_raw], language_codes_probs)
        isbn_dict['search_md5_objs'] = search_md5_objs
        # TODO: add IPFS CIDs to these objects so we can show a preview.
        # isbn_dict['search_md5_objs_pdf_index'] = next((i for i, search_md5_obj in enumerate(search_md5_objs) if search_md5_obj.extension_best == 'pdf' and len(search_md5_obj['ipfs_cids']) > 0), -1)

        return render_template(
            "page/isbn.html",
            header_active="datasets",
            isbn_input=isbn_input,
            isbn_dict=isbn_dict,
            isbn_dict_json=nice_json(isbn_dict),
        )

def is_string_subsequence(needle, haystack):
    i_needle = 0
    i_haystack = 0
    while i_needle < len(needle) and i_haystack < len(haystack):
        if needle[i_needle].lower() == haystack[i_haystack].lower():
            i_needle += 1
        i_haystack += 1
    return i_needle == len(needle)

def sort_by_length_and_filter_subsequences_with_longest_string(strings):
    strings = [string for string in sorted(set(strings), key=len, reverse=True) if len(string) > 0]
    if len(strings) == 0:
        return []
    longest_string = strings[0]
    strings_filtered = [longest_string]
    for string in strings[1:]:
        if not is_string_subsequence(string, longest_string):
            strings_filtered.append(string)
    return strings_filtered



def get_md5_dicts(session, canonical_md5s):
    # canonical_and_upper_md5s = canonical_md5s + [md5.upper() for md5 in canonical_md5s]
    lgrsnf_book_dicts = dict((item['md5'].lower(), item) for item in get_lgrsnf_book_dicts(session, "MD5", canonical_md5s))
    lgrsfic_book_dicts = dict((item['md5'].lower(), item) for item in get_lgrsfic_book_dicts(session, "MD5", canonical_md5s))
    lgli_file_dicts = dict((item['md5'].lower(), item) for item in get_lgli_file_dicts(session, "md5", canonical_md5s))
    zlib_book_dicts1 = dict((item['md5_reported'].lower(), item) for item in get_zlib_book_dicts(session, "md5_reported", canonical_md5s))
    zlib_book_dicts2 = dict((item['md5'].lower(), item) for item in get_zlib_book_dicts(session, "md5", canonical_md5s))

    md5_dicts = []
    for canonical_md5 in canonical_md5s:
        md5_dict = {}
        md5_dict['md5'] = canonical_md5
        md5_dict['lgrsnf_book'] = lgrsnf_book_dicts.get(canonical_md5)
        md5_dict['lgrsfic_book'] = lgrsfic_book_dicts.get(canonical_md5)
        md5_dict['lgli_file'] = lgli_file_dicts.get(canonical_md5)
        if md5_dict.get('lgli_file'):
            md5_dict['lgli_file']['editions'] = md5_dict['lgli_file']['editions'][0:5]
        md5_dict['zlib_book'] = zlib_book_dicts1.get(canonical_md5) or zlib_book_dicts2.get(canonical_md5)

        ipfs_infos = set()
        if md5_dict['lgrsnf_book'] and len(md5_dict['lgrsnf_book'].get('ipfs_cid') or '') > 0:
            ipfs_infos.add((md5_dict['lgrsnf_book']['ipfs_cid'].lower(), md5_dict['lgrsnf_book']['normalized_filename'], 'lgrsnf'))
        if md5_dict['lgrsfic_book'] and len(md5_dict['lgrsfic_book'].get('ipfs_cid') or '') > 0:
            ipfs_infos.add((md5_dict['lgrsfic_book']['ipfs_cid'].lower(), md5_dict['lgrsfic_book']['normalized_filename'], 'lgrsfic'))
        if md5_dict['zlib_book'] and len(md5_dict['zlib_book'].get('ipfs_cid') or '') > 0:
            ipfs_infos.add((md5_dict['zlib_book']['ipfs_cid'].lower(), md5_dict['zlib_book']['normalized_filename'], 'zlib'))
        md5_dict['ipfs_infos'] = list(ipfs_infos)

        md5_dict['file_unified_data'] = {}

        original_filename_multiple = [
            ((md5_dict['lgrsnf_book'] or {}).get('locator') or '').strip(),
            ((md5_dict['lgrsfic_book'] or {}).get('locator') or '').strip(),
            ((md5_dict['lgli_file'] or {}).get('locator') or '').strip(),
            (((md5_dict['lgli_file'] or {}).get('descriptions_mapped') or {}).get('library_filename_first') or '').strip(),
            ((md5_dict['lgli_file'] or {}).get('scimag_archive_path') or '').strip(),
        ]
        md5_dict['file_unified_data']['original_filename_multiple'] = sort_by_length_and_filter_subsequences_with_longest_string(original_filename_multiple)
        md5_dict['file_unified_data']['original_filename_best'] = min(md5_dict['file_unified_data']['original_filename_multiple'], key=len) if len(md5_dict['file_unified_data']['original_filename_multiple']) > 0 else ''
        md5_dict['file_unified_data']['original_filename_best_name_only'] =  re.split(r'[\\/]', md5_dict['file_unified_data']['original_filename_best'])[-1]

        # Select the cover_url_normalized in order of what is likely to be the best one: zlib, lgrsnf, lgrsfic, lgli.
        zlib_cover = ((md5_dict['zlib_book'] or {}).get('cover_url') or '').strip()
        cover_url_multiple = [
            # Put the zlib_cover at the beginning if it starts with the right prefix.
            # zlib_cover.strip() if zlib_cover.startswith('https://covers.zlibcdn2.com') else '',
            ((md5_dict['lgrsnf_book'] or {}).get('cover_url_normalized') or '').strip(),
            ((md5_dict['lgrsfic_book'] or {}).get('cover_url_normalized') or '').strip(),
            ((md5_dict['lgli_file'] or {}).get('cover_url_guess_normalized') or '').strip(),
            # Otherwie put it at the end.
            # '' if zlib_cover.startswith('https://covers.zlibcdn2.com') else zlib_cover.strip(),
            # Temporarily always put it at the end because their servers are down.
            zlib_cover.strip()
        ]
        md5_dict['file_unified_data']['cover_url_multiple'] = list(dict.fromkeys(filter(len, cover_url_multiple)))
        md5_dict['file_unified_data']['cover_url_best'] = (md5_dict['file_unified_data']['cover_url_multiple'] + [''])[0]

        extension_multiple = [
            ((md5_dict['zlib_book'] or {}).get('extension') or '').strip(),
            ((md5_dict['lgrsnf_book'] or {}).get('extension') or '').strip(),
            ((md5_dict['lgrsfic_book'] or {}).get('extension') or '').strip(),
            ((md5_dict['lgli_file'] or {}).get('extension') or '').strip(),
        ]
        if "epub" in extension_multiple:
            md5_dict['file_unified_data']['extension_best'] = "epub"
        elif "pdf" in extension_multiple:
            md5_dict['file_unified_data']['extension_best'] = "pdf"
        else:
            md5_dict['file_unified_data']['extension_best'] = max(extension_multiple, key=len)
        md5_dict['file_unified_data']['extension_multiple'] = list(dict.fromkeys(filter(len, extension_multiple)))

        filesize_multiple = [
            (md5_dict['zlib_book'] or {}).get('filesize_reported') or 0,
            (md5_dict['zlib_book'] or {}).get('filesize') or 0,
            (md5_dict['lgrsnf_book'] or {}).get('filesize') or 0,
            (md5_dict['lgrsfic_book'] or {}).get('filesize') or 0,
            (md5_dict['lgli_file'] or {}).get('filesize') or 0,
        ]
        md5_dict['file_unified_data']['filesize_best'] = max(filesize_multiple)
        md5_dict['file_unified_data']['filesize_multiple'] = list(dict.fromkeys(filter(lambda fz: fz > 0, filesize_multiple)))

        lgli_single_edition = md5_dict['lgli_file']['editions'][0] if len((md5_dict.get('lgli_file') or {}).get('editions') or []) == 1 else None
        lgli_all_editions = md5_dict['lgli_file']['editions'] if md5_dict.get('lgli_file') else []

        title_multiple = [
            ((md5_dict['zlib_book'] or {}).get('title') or '').strip(),
            ((md5_dict['lgrsnf_book'] or {}).get('title') or '').strip(),
            ((md5_dict['lgrsfic_book'] or {}).get('title') or '').strip(),
            ((lgli_single_edition or {}).get('title') or '').strip(),
        ]
        md5_dict['file_unified_data']['title_best'] = max(title_multiple, key=len)
        title_multiple += [(edition.get('title') or '').strip() for edition in lgli_all_editions]
        title_multiple += [(edition['descriptions_mapped'].get('maintitleonoriginallanguage_first') or '').strip() for edition in lgli_all_editions]
        title_multiple += [(edition['descriptions_mapped'].get('maintitleonenglishtranslate_first') or '').strip() for edition in lgli_all_editions]
        if md5_dict['file_unified_data']['title_best'] == '':
            md5_dict['file_unified_data']['title_best'] = max(title_multiple, key=len)
        md5_dict['file_unified_data']['title_multiple'] = sort_by_length_and_filter_subsequences_with_longest_string(title_multiple)

        author_multiple = [
            (md5_dict['zlib_book'] or {}).get('author', '').strip(),
            (md5_dict['lgrsnf_book'] or {}).get('author', '').strip(),
            (md5_dict['lgrsfic_book'] or {}).get('author', '').strip(),
            (lgli_single_edition or {}).get('authors_normalized', '').strip(),
        ]
        md5_dict['file_unified_data']['author_best'] = max(author_multiple, key=len)
        author_multiple += [edition.get('authors_normalized', '').strip() for edition in lgli_all_editions]
        if md5_dict['file_unified_data']['author_best'] == '':
            md5_dict['file_unified_data']['author_best'] = max(author_multiple, key=len)
        md5_dict['file_unified_data']['author_multiple'] = sort_by_length_and_filter_subsequences_with_longest_string(author_multiple)

        publisher_multiple = [
            ((md5_dict['zlib_book'] or {}).get('publisher') or '').strip(),
            ((md5_dict['lgrsnf_book'] or {}).get('publisher') or '').strip(),
            ((md5_dict['lgrsfic_book'] or {}).get('publisher') or '').strip(),
            ((lgli_single_edition or {}).get('publisher_normalized') or '').strip(),
        ]
        md5_dict['file_unified_data']['publisher_best'] = max(publisher_multiple, key=len)
        publisher_multiple += [(edition.get('publisher_normalized') or '').strip() for edition in lgli_all_editions]
        if md5_dict['file_unified_data']['publisher_best'] == '':
            md5_dict['file_unified_data']['publisher_best'] = max(publisher_multiple, key=len)
        md5_dict['file_unified_data']['publisher_multiple'] = sort_by_length_and_filter_subsequences_with_longest_string(publisher_multiple)

        edition_varia_multiple = [
            ((md5_dict['zlib_book'] or {}).get('edition_varia_normalized') or '').strip(),
            ((md5_dict['lgrsnf_book'] or {}).get('edition_varia_normalized') or '').strip(),
            ((md5_dict['lgrsfic_book'] or {}).get('edition_varia_normalized') or '').strip(),
            ((lgli_single_edition or {}).get('edition_varia_normalized') or '').strip(),
        ]
        md5_dict['file_unified_data']['edition_varia_best'] = max(edition_varia_multiple, key=len)
        edition_varia_multiple += [(edition.get('edition_varia_normalized') or '').strip() for edition in lgli_all_editions]
        if md5_dict['file_unified_data']['edition_varia_best'] == '':
            md5_dict['file_unified_data']['edition_varia_best'] = max(edition_varia_multiple, key=len)
        md5_dict['file_unified_data']['edition_varia_multiple'] = sort_by_length_and_filter_subsequences_with_longest_string(edition_varia_multiple)

        comments_multiple = [
            ((md5_dict['lgrsnf_book'] or {}).get('commentary') or '').strip(),
            ((md5_dict['lgrsfic_book'] or {}).get('commentary') or '').strip(),
            ' -- '.join(filter(len, [((md5_dict['lgrsnf_book'] or {}).get('library') or '').strip(), (md5_dict['lgrsnf_book'] or {}).get('issue', '').strip()])),
            ' -- '.join(filter(len, [((md5_dict['lgrsfic_book'] or {}).get('library') or '').strip(), (md5_dict['lgrsfic_book'] or {}).get('issue', '').strip()])),
            ' -- '.join(filter(len, [((md5_dict['lgli_file'] or {}).get('descriptions_mapped') or {}).get('descriptions_mapped.library_first', '').strip(), (md5_dict['lgli_file'] or {}).get('descriptions_mapped', {}).get('descriptions_mapped.library_issue_first', '').strip()])),
            ((lgli_single_edition or {}).get('commentary') or '').strip(),
            ((lgli_single_edition or {}).get('editions_add_info') or '').strip(),
            ((lgli_single_edition or {}).get('commentary') or '').strip(),
            *[note.strip() for note in (((lgli_single_edition or {}).get('descriptions_mapped') or {}).get('descriptions_mapped.notes_multiple') or [])],
        ]
        md5_dict['file_unified_data']['comments_best'] = max(comments_multiple, key=len)
        comments_multiple += [(edition.get('comments_normalized') or '').strip() for edition in lgli_all_editions]
        for edition in lgli_all_editions:
            comments_multiple.append((edition.get('editions_add_info') or '').strip())
            comments_multiple.append((edition.get('commentary') or '').strip())
            for note in (edition.get('descriptions_mapped') or {}).get('descriptions_mapped.notes_multiple', []):
                comments_multiple.append(note.strip())
        if md5_dict['file_unified_data']['comments_best'] == '':
            md5_dict['file_unified_data']['comments_best'] = max(comments_multiple, key=len)
        md5_dict['file_unified_data']['comments_multiple'] = sort_by_length_and_filter_subsequences_with_longest_string(comments_multiple)

        stripped_description_multiple = [
            ((md5_dict['zlib_book'] or {}).get('stripped_description') or '').strip(),
            ((md5_dict['lgrsnf_book'] or {}).get('stripped_description') or '').strip(),
            ((md5_dict['lgrsfic_book'] or {}).get('stripped_description') or '').strip(),
            ((lgli_single_edition or {}).get('stripped_description') or '').strip(),
        ]
        md5_dict['file_unified_data']['stripped_description_best'] = max(stripped_description_multiple, key=len)
        stripped_description_multiple += [(edition.get('stripped_description') or '').strip() for edition in lgli_all_editions]
        if md5_dict['file_unified_data']['stripped_description_best'] == '':
            md5_dict['file_unified_data']['stripped_description_best'] = max(stripped_description_multiple, key=len)
        md5_dict['file_unified_data']['stripped_description_multiple'] = sort_by_length_and_filter_subsequences_with_longest_string(stripped_description_multiple)

        md5_dict['file_unified_data']['language_codes'] = combine_bcp47_lang_codes([
            ((md5_dict['zlib_book'] or {}).get('language_codes') or []),
            ((md5_dict['lgrsnf_book'] or {}).get('language_codes') or []),
            ((md5_dict['lgrsfic_book'] or {}).get('language_codes') or []),
            ((lgli_single_edition or {}).get('language_codes') or []),
        ])
        if len(md5_dict['file_unified_data']['language_codes']) == 0:
            md5_dict['file_unified_data']['language_codes'] = combine_bcp47_lang_codes([(edition.get('language_codes') or []) for edition in lgli_all_editions])
        md5_dict['file_unified_data']['languages_and_codes'] = [(langcodes.get(lang_code).display_name(), lang_code) for lang_code in md5_dict['file_unified_data']['language_codes']]

        md5_dict['file_unified_data']['sanitized_isbns'] = list(set([
            *((md5_dict['zlib_book'] or {}).get('sanitized_isbns') or []),
            *((md5_dict['lgrsnf_book'] or {}).get('sanitized_isbns') or []),
            *((md5_dict['lgrsfic_book'] or {}).get('sanitized_isbns') or []),
            *([isbn for edition in lgli_all_editions for isbn in (edition.get('sanitized_isbns') or [])]),
        ]))
        md5_dict['file_unified_data']['asin_multiple'] = list(set(item for item in [
            (md5_dict['lgrsnf_book'] or {}).get('asin', '').strip(),
            (md5_dict['lgrsfic_book'] or {}).get('asin', '').strip(),
            *[item[1] for edition in lgli_all_editions for item in edition['identifiers_normalized'] if item[0] == 'asin_multiple'],
        ] if item != ''))
        md5_dict['file_unified_data']['googlebookid_multiple'] = list(set(item for item in [
            (md5_dict['lgrsnf_book'] or {}).get('googlebookid', '').strip(),
            (md5_dict['lgrsfic_book'] or {}).get('googlebookid', '').strip(),
            *[item[1] for edition in lgli_all_editions for item in edition['identifiers_normalized'] if item[0] == 'googlebookid_multiple'],
        ] if item != ''))
        md5_dict['file_unified_data']['openlibraryid_multiple'] = list(set(item for item in [
            (md5_dict['lgrsnf_book'] or {}).get('openlibraryid', '').strip(),
            *[item[1] for edition in lgli_all_editions for item in edition['identifiers_normalized'] if item[0] == 'openlibrary_multiple'],
        ] if item != ''))
        md5_dict['file_unified_data']['doi_multiple'] = list(set(item for item in [
            (md5_dict['lgrsnf_book'] or {}).get('doi', '').strip(),
            *[item[1] for edition in lgli_all_editions for item in edition['identifiers_normalized'] if item[0] == 'doi'],
        ] if item != ''))

        if md5_dict['lgrsnf_book'] != None:
            md5_dict['lgrsnf_book'] = {
                'id': md5_dict['lgrsnf_book']['id'],
                'md5': md5_dict['lgrsnf_book']['md5'],
            }
        if md5_dict['lgrsfic_book'] != None:
            md5_dict['lgrsfic_book'] = {
                'id': md5_dict['lgrsfic_book']['id'],
                'md5': md5_dict['lgrsfic_book']['md5'],
            }
        if md5_dict['lgli_file'] != None:
            md5_dict['lgli_file'] = {
                'f_id': md5_dict['lgli_file']['f_id'],
                'md5': md5_dict['lgli_file']['md5'],
                'libgen_topic': md5_dict['lgli_file']['libgen_topic'],
                'editions': [{'e_id': edition['e_id']} for edition in md5_dict['lgli_file']['editions']],
            }
        if md5_dict['zlib_book'] != None:
            md5_dict['zlib_book'] = {
                'zlibrary_id': md5_dict['zlib_book']['zlibrary_id'],
                'md5': md5_dict['zlib_book']['md5'],
                'md5_reported': md5_dict['zlib_book']['md5_reported'],
                'filesize': md5_dict['zlib_book']['filesize'],
                'filesize_reported': md5_dict['zlib_book']['filesize_reported'],
                'in_libgen': md5_dict['zlib_book']['in_libgen'],
                'pilimi_torrent': md5_dict['zlib_book']['pilimi_torrent'],
            }

        md5_dicts.append(md5_dict)

    return md5_dicts


@page.get("/md5/<string:md5_input>")
def md5_page(md5_input):
    md5_input = md5_input[0:50]
    canonical_md5 = md5_input.strip().lower()[0:32]

    if not bool(re.match(r"^[a-fA-F\d]{32}$", canonical_md5)):
        return render_template("page/md5.html", header_active="datasets", md5_input=md5_input)

    if canonical_md5 != md5_input:
        return redirect(f"/md5/{canonical_md5}", code=301)

    md5_dicts = get_md5_dicts(db.session, [canonical_md5])

    if len(md5_dicts) == 0:
        return render_template("page/md5.html", header_active="datasets", md5_input=md5_input)

    md5_dict = md5_dicts[0]
    md5_dict['isbns_rich'] = make_isbns_rich(md5_dict['file_unified_data']['sanitized_isbns'])
    md5_dict['download_urls'] = []
    if len(md5_dict['ipfs_infos']) > 0:
        md5_dict['download_urls'].append(('IPFS Gateway #1', f"https://cloudflare-ipfs.com/ipfs/{md5_dict['ipfs_infos'][0][0].lower()}?filename={md5_dict['ipfs_infos'][0][1]}", "(you might need to try multiple times with IPFS)"))
        md5_dict['download_urls'].append(('IPFS Gateway #2', f"https://ipfs.io/ipfs/{md5_dict['ipfs_infos'][0][0].lower()}?filename={md5_dict['ipfs_infos'][0][1]}", ""))
        md5_dict['download_urls'].append(('IPFS Gateway #3', f"https://crustwebsites.net/ipfs/{md5_dict['ipfs_infos'][0][0].lower()}?filename={md5_dict['ipfs_infos'][0][1]}", ""))
        md5_dict['download_urls'].append(('IPFS Gateway #4', f"https://gateway.pinata.cloud/ipfs/{md5_dict['ipfs_infos'][0][0].lower()}?filename={md5_dict['ipfs_infos'][0][1]}", ""))
    shown_click_get = False
    if md5_dict['lgrsnf_book'] != None:
        md5_dict['download_urls'].append(('Library Genesis ".rs-fork" Non-Fiction', f"http://library.lol/main/{md5_dict['lgrsnf_book']['md5'].lower()}", f"({'also ' if shown_click_get else ''}click “GET” at the top)"))
        shown_click_get = True
    if md5_dict['lgrsfic_book'] != None:
        md5_dict['download_urls'].append(('Library Genesis ".rs-fork" Fiction', f"http://library.lol/fiction/{md5_dict['lgrsfic_book']['md5'].lower()}", f"({'also ' if shown_click_get else ''}click “GET” at the top)"))
        shown_click_get = True
    if md5_dict['lgli_file'] != None:
        md5_dict['download_urls'].append(('Library Genesis ".li-fork"', f"http://libgen.li/ads.php?md5={md5_dict['lgli_file']['md5'].lower()}", f"({'also ' if shown_click_get else ''}click “GET” at the top)"))
        shown_click_get = True
    for doi in md5_dict['file_unified_data']['doi_multiple']:
        md5_dict['download_urls'].append((f"Sci-Hub: {doi}", f"https://sci-hub.se/{doi}", ""))
    if md5_dict['zlib_book'] != None:
        if len(md5_dict['download_urls']) == 0 or (len(md5_dict['ipfs_infos']) > 0 and md5_dict['ipfs_infos'][0][2] == 'zlib'):
            md5_dict['download_urls'].append((f"Z-Library Anonymous Mirror #1", make_temp_anon_zlib_link(md5_dict['zlib_book']['zlibrary_id'], md5_dict['zlib_book']['pilimi_torrent'], md5_dict['file_unified_data']['extension_best']), "(we are working on better mirrors; bear with us)"))
            md5_dict['download_urls'].append((f"Z-Library TOR", f"http://zlibrary24tuxziyiyfr7zd46ytefdqbqd2axkmxm4o5374ptpc52fad.onion/md5/{md5_dict['zlib_book']['md5_reported'].lower()}", "(requires TOR browser)"))

    return render_template(
        "page/md5.html",
        header_active="datasets",
        md5_input=md5_input,
        md5_dict=md5_dict,
        md5_dict_json=nice_json(md5_dict),
    )


SearchMd5Obj = collections.namedtuple('SearchMd5Obj', 'md5 cover_url_best languages_and_codes extension_best filesize_best original_filename_best_name_only title_best publisher_best edition_varia_best author_best sanitized_isbns asin_multiple googlebookid_multiple openlibraryid_multiple doi_multiple has_description')

def get_search_md5_objs(session, canonical_md5s):
    md5_dicts = get_md5_dicts(session, canonical_md5s)
    search_md5_objs = []
    for md5_dict in md5_dicts:
        search_md5_objs.append(SearchMd5Obj(
            md5=md5_dict['md5'],
            cover_url_best=md5_dict['file_unified_data']['cover_url_best'][:1000],
            languages_and_codes=md5_dict['file_unified_data']['languages_and_codes'][:10],
            extension_best=md5_dict['file_unified_data']['extension_best'][:100],
            filesize_best=md5_dict['file_unified_data']['filesize_best'],
            original_filename_best_name_only=md5_dict['file_unified_data']['original_filename_best_name_only'][:1000],
            title_best=md5_dict['file_unified_data']['title_best'][:1000],
            publisher_best=md5_dict['file_unified_data']['publisher_best'][:1000],
            edition_varia_best=md5_dict['file_unified_data']['edition_varia_best'][:1000],
            author_best=md5_dict['file_unified_data']['author_best'][:1000],
            sanitized_isbns=md5_dict['file_unified_data']['sanitized_isbns'][:50],
            asin_multiple=md5_dict['file_unified_data']['asin_multiple'][:50],
            googlebookid_multiple=md5_dict['file_unified_data']['googlebookid_multiple'][:50],
            openlibraryid_multiple=md5_dict['file_unified_data']['openlibraryid_multiple'][:50],
            doi_multiple=md5_dict['file_unified_data']['doi_multiple'][:50],
            has_description=len(md5_dict['file_unified_data']['stripped_description_best']) > 0,
        ))
    return search_md5_objs

def sort_search_md5_objs(search_md5_objs, language_codes_probs):
    def score_fn(search_md5_obj):
        language_codes = [item[1] for item in search_md5_obj.languages_and_codes]
        score = 0
        if search_md5_obj.filesize_best > 500000:
            score += 10000
        for lang_code, prob in language_codes_probs.items():
            if lang_code in language_codes:
                score += prob * 1000
        if len(language_codes) == 0:
            score += 100
        if search_md5_obj.extension_best in ['epub', 'pdf']:
            score += 100
        if len(search_md5_obj.cover_url_best) > 0:
            # Since we only use the zlib cover as a last resort, and zlib is down / only on Tor,
            # stronlgy demote zlib-only books for now.
            if 'covers.zlibcdn2.com' in search_md5_obj.cover_url_best:
                score -= 100
            else:
                score += 30
        if len(search_md5_obj.title_best) > 0:
            score += 100
        if len(search_md5_obj.author_best) > 0:
            score += 10
        if len(search_md5_obj.publisher_best) > 0:
            score += 10
        if len(search_md5_obj.edition_varia_best) > 0:
            score += 10
        if len(search_md5_obj.original_filename_best_name_only) > 0:
            score += 10
        if len(search_md5_obj.sanitized_isbns) > 0:
            score += 10
        if len(search_md5_obj.asin_multiple) > 0:
            score += 10
        if len(search_md5_obj.googlebookid_multiple) > 0:
            score += 10
        if len(search_md5_obj.openlibraryid_multiple) > 0:
            score += 10
        if len(search_md5_obj.doi_multiple) > 0:
            # For now demote DOI quite a bit, since tons of papers can drown out books.
            score -= 700
        if search_md5_obj.has_description > 0:
            score += 10
        return score

    return sorted(search_md5_objs, key=score_fn, reverse=True)

# InnoDB stop words of 3 characters or more
# INNODB_LONG_STOP_WORDS = [ 'about', 'an', 'are','com', 'for', 'from', 'how', 'that', 'the', 'this', 'was', 'what', 'when', 'where', 'who', 'will', 'with', 'und', 'the', 'www']
# def filter_innodb_words(words):
#     for word in words:
#         length = len(word)
#         if length >= 3 and length <= 84 and word not in INNODB_LONG_STOP_WORDS:
#             yield word


@page.get("/search")
def search_page():
    search_input = request.args.get("q", "").strip()

    if bool(re.match(r"^[a-fA-F\d]{32}$", search_input)):
        return redirect(f"/md5/{search_input}", code=301)

    if bool(re.match(r"^OL\d+M$", search_input)):
        return redirect(f"/ol/{search_input}", code=301)

    canonical_isbn13 = isbnlib.get_canonical_isbn(search_input, output='isbn13')
    if len(canonical_isbn13) == 13 and len(isbnlib.info(canonical_isbn13)) > 0:
        return redirect(f"/isbn/{canonical_isbn13}", code=301)

    language_codes_probs = {}
    language_detection = []
    try:
        language_detection = langdetect.detect_langs(search_input)
    except langdetect.lang_detect_exception.LangDetectException:
        pass
    for item in language_detection:
        for code in get_bcp47_lang_codes(item.lang):
            language_codes_probs[code] = item.prob
    for lang_code, quality in request.accept_languages:
        for code in get_bcp47_lang_codes(lang_code):
            language_codes_probs[code] = quality
    if len(language_codes_probs) == 0:
        language_codes_probs['en'] = 1.0

    # file_search_cols = [ComputedFileSearchIndex.search_text_combined,  ComputedFileSearchIndex.sanitized_isbns,  ComputedFileSearchIndex.asin_multiple,  ComputedFileSearchIndex.googlebookid_multiple,  ComputedFileSearchIndex.openlibraryid_multiple,  ComputedFileSearchIndex.doi_multiple]

    with db.session.connection() as conn:
        with db.engine.connect() as conn2:
            if conn == conn2:
                raise Exception("conn should be different than conn2 here")

            # For some fulltext searches it mysteriously takes a long, long time to resolve.. E.g. "seeing science"
            # We really need to switch to a proper search engine.
            # For now, this super hacky workaround to at least kill the query after a few seconds.
            # From https://stackoverflow.com/a/60216991
            timeout_seconds = 10
            timeout_thread_id = conn.connection.thread_id()
            timeout_thread = threading.Timer(timeout_seconds, lambda: conn2.execute("KILL QUERY {}".format(timeout_thread_id)))
            timeout_thread.start()

            total_results = 100
            remaining_results = total_results
            search_md5_objs = []
            seen_md5s = set()
            search_terms = search_input.split(' ')
            max_search_md5_objs_reached = False
            max_additional_search_md5_objs_reached = False
            if '"' not in search_input and not any(term.startswith('-') for term in search_terms):
                search_md5_objs_raw = conn.execute(select(ComputedSearchMd5Objs.md5, ComputedSearchMd5Objs.json).where(match(ComputedSearchMd5Objs.json, against=f'"{search_input}"').in_boolean_mode()).limit(remaining_results)).all()
                search_md5_objs = sort_search_md5_objs([SearchMd5Obj(search_md5_obj_raw.md5, *orjson.loads(search_md5_obj_raw.json)) for search_md5_obj_raw in search_md5_objs_raw], language_codes_probs)
                seen_md5s = set([search_md5_obj.md5 for search_md5_obj in search_md5_objs])
                remaining_results = total_results - len(seen_md5s)

            if remaining_results > 0:
                # Add "+" to search terms that don't already have "+" or "-" in them:
                processed_search_input = ' '.join([f'+{search_term}' if not (search_term.startswith('+') or search_term.startswith('-')) else search_term for search_term in search_terms])
                search_md5_objs_raw = conn.execute(select(ComputedSearchMd5Objs.md5, ComputedSearchMd5Objs.json).where(match(ComputedSearchMd5Objs.json, against=processed_search_input).in_boolean_mode()).limit(remaining_results)).all()
                if len(search_md5_objs_raw) >= remaining_results:
                    max_search_md5_objs_reached = True
                search_md5_objs += sort_search_md5_objs([SearchMd5Obj(search_md5_obj_raw.md5, *orjson.loads(search_md5_obj_raw.json)) for search_md5_obj_raw in search_md5_objs_raw if search_md5_obj_raw.md5 not in seen_md5s], language_codes_probs)
                seen_md5s = set([search_md5_obj.md5 for search_md5_obj in search_md5_objs])
                remaining_results = total_results - len(seen_md5s)
            else:
                max_search_md5_objs_reached = True

            additional_search_md5_objs = []
            if remaining_results > 0:
                search_md5_objs_raw = conn.execute(select(ComputedSearchMd5Objs.md5, ComputedSearchMd5Objs.json).where(match(ComputedSearchMd5Objs.json, against=search_input).in_natural_language_mode()).limit(remaining_results)).all()
                if len(search_md5_objs_raw) >= remaining_results:
                    max_additional_search_md5_objs_reached = True
                # Don't do custom sorting on these; otherwise we'll get a bunch of garbage at the top, since the last few results can be pretty bad.
                additional_search_md5_objs = sort_search_md5_objs([SearchMd5Obj(search_md5_obj_raw.md5, *orjson.loads(search_md5_obj_raw.json)) for search_md5_obj_raw in search_md5_objs_raw if search_md5_obj_raw.md5 not in seen_md5s], language_codes_probs)

            timeout_thread.cancel()

            search_dict = {}
            search_dict['search_md5_objs'] = search_md5_objs
            search_dict['additional_search_md5_objs'] = additional_search_md5_objs
            search_dict['max_search_md5_objs_reached'] = max_search_md5_objs_reached
            search_dict['max_additional_search_md5_objs_reached'] = max_additional_search_md5_objs_reached

            return render_template(
                "page/search.html",
                header_active="search",
                search_input=search_input,
                search_dict=search_dict,
            )



def generate_computed_file_info_process_md5s(canonical_md5s):
    with db.Session(db.engine) as session:
        search_md5_objs = get_search_md5_objs(session, canonical_md5s)

        data = []
        for search_md5_obj in search_md5_objs:
            # search_text_combined_list = []
            # for item in md5_dict['file_unified_data']['title_multiple']:
            #     search_text_combined_list.append(item.lower())
            # for item in md5_dict['file_unified_data']['author_multiple']:
            #     search_text_combined_list.append(item.lower())
            # for item in md5_dict['file_unified_data']['edition_varia_multiple']:
            #     search_text_combined_list.append(item.lower())
            # for item in md5_dict['file_unified_data']['publisher_multiple']:
            #     search_text_combined_list.append(item.lower())
            # for item in md5_dict['file_unified_data']['original_filename_multiple']:
            #     search_text_combined_list.append(item.lower())
            # search_text_combined = '   ///   '.join(search_text_combined_list)
            # language_codes = ",".join(md5_dict['file_unified_data']['language_codes'])
            # data.append({ 'md5': md5_dict['md5'], 'language_codes': language_codes[0:10], 'json': orjson.dumps(md5_dict, ensure_ascii=False), 'search_text_combined': search_text_combined[0:30000] })
            data.append({ 'md5': search_md5_obj.md5, 'json': orjson.dumps(search_md5_obj[1:], ensure_ascii=False) })
        # session.connection().execute(text("INSERT INTO computed_file_info (md5, language_codes, json, search_text_combined) VALUES (:md5, :language_codes, :json, :search_text_combined)"), data)
        # session.connection().execute(text("REPLACE INTO computed_file_info (md5, json, search_text_combined) VALUES (:md5, :json, :search_text_combined)"), data)
        session.connection().execute(text("INSERT INTO computed_file_info (md5, json) VALUES (:md5, :json)"), data)
        # pbar.update(len(data))
        # print(f"Processed {len(data)} md5s")
        del search_md5_objs
    gc.collect()

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

# CREATE TABLE computed_all_md5s (
#     md5 CHAR(32) NOT NULL,
#     PRIMARY KEY (md5)
# ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 SELECT md5 FROM libgenli_files;
# INSERT IGNORE INTO computed_all_md5s SELECT md5 FROM zlib_book WHERE md5 != '';
# INSERT IGNORE INTO computed_all_md5s SELECT md5_reported FROM zlib_book WHERE md5_reported != '';
# INSERT IGNORE INTO computed_all_md5s SELECT MD5 FROM libgenrs_updated;
# INSERT IGNORE INTO computed_all_md5s SELECT MD5 FROM libgenrs_fiction;

# CREATE TABLE computed_file_info (
#     `id` INT NOT NULL AUTO_INCREMENT,
#     `md5` CHAR(32) CHARSET=utf8mb4 COLLATE=utf8mb4_bin NOT NULL,
#     `json` LONGTEXT NOT NULL,
#     PRIMARY KEY (`id`)
# ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
# ALTER TABLE computed_file_info ADD INDEX md5 (md5);
# ALTER TABLE computed_file_info ADD FULLTEXT KEY `json` (`json`);

# SET SESSION TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;
# CREATE TABLE computed_search_md5_objs (
#     `md5` CHAR(32) CHARSET=utf8mb4 COLLATE=utf8mb4_bin NOT NULL,
#     `json` LONGTEXT NOT NULL,
#     PRIMARY KEY (`md5`),
#     FULLTEXT KEY `json` (`json`)
# -- Significant benefits for MyISAM in search: https://stackoverflow.com/a/45674350 and https://mariadb.com/resources/blog/storage-engine-choice-aria/
# ) ENGINE=MyISAM DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci IGNORE SELECT `md5`, `json` FROM computed_file_info LIMIT 10000000;


# ./run flask page generate_computed_file_info
def generate_computed_file_info_internal():
    THREADS = 100
    CHUNK_SIZE = 150
    BATCH_SIZE = 100000
    # BATCH_SIZE = 320000
    # THREADS = 10
    # CHUNK_SIZE = 100
    # BATCH_SIZE = 5000

    first_md5 = ''
    # first_md5 = '03f5fda962bf419e836b8e8c7e652e7b'

    with db.engine.connect() as conn:
        # with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS) as executor:
            # , smoothing=0.005
            with tqdm.tqdm(total=conn.execute(select([func.count()]).where(ComputedAllMd5s.md5 >= first_md5)).scalar(), bar_format='{l_bar}{bar}{r_bar} {eta}') as pbar:
            # with tqdm.tqdm(total=100000, bar_format='{l_bar}{bar}{r_bar} {eta}') as pbar:
                for batch in query_yield_batches(conn, select(ComputedAllMd5s.md5).where(ComputedAllMd5s.md5 >= first_md5), ComputedAllMd5s.md5, BATCH_SIZE):
                    with multiprocessing.Pool(THREADS) as executor:
                        print(f"Processing {len(batch)} md5s from computed_all_md5s (starting md5: {batch[0][0]})...")
                        executor.map(generate_computed_file_info_process_md5s, chunks([item[0] for item in batch], CHUNK_SIZE))
                        pbar.update(len(batch))

                # executor.shutdown()
                print(f"Done!")

@page.cli.command('generate_computed_file_info')
def generate_computed_file_info():
    yappi.set_clock_type("wall")
    yappi.start()
    generate_computed_file_info_internal()
    yappi.stop()
    stats = yappi.get_func_stats()
    stats.save("profile.prof", type="pstat")