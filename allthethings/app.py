import hashlib
import os

from celery import Celery
from flask import Flask
from werkzeug.security import safe_join
from werkzeug.debug import DebuggedApplication
from werkzeug.middleware.proxy_fix import ProxyFix

from allthethings.page.views import page
from allthethings.up.views import up
from allthethings.cli.views import cli
from allthethings.extensions import db, es, debug_toolbar, flask_static_digest, Base, Reflected

def create_celery_app(app=None):
    """
    Create a new Celery app and tie together the Celery config to the app's
    config. Wrap all tasks in the context of the application.

    :param app: Flask app
    :return: Celery app
    """
    app = app or create_app()

    celery = Celery(app.import_name)
    celery.conf.update(app.config.get("CELERY_CONFIG", {}))
    TaskBase = celery.Task

    class ContextTask(TaskBase):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return TaskBase.__call__(self, *args, **kwargs)

    celery.Task = ContextTask

    return celery


def create_app(settings_override=None):
    """
    Create a Flask application using the app factory pattern.

    :param settings_override: Override settings
    :return: Flask app
    """
    app = Flask(__name__, static_folder="../public", static_url_path="")

    app.config.from_object("config.settings")

    if settings_override:
        app.config.update(settings_override)

    middleware(app)

    app.register_blueprint(up)
    # Legacy Anna's Archive book/paper search is preserved under /legacy. The
    # modern Vers3Dynamics Engineering Intelligence platform owns the root.
    app.register_blueprint(page, url_prefix="/legacy")
    app.register_blueprint(cli)

    # Register data-imports CLI commands
    try:
        from data_imports import imports as import_cli
        app.register_blueprint(import_cli)
    except ImportError:
        pass  # Data imports not available

    # Register the Engineering Intelligence platform (web UI, REST API, CLI).
    # Guarded so the legacy app still boots if an optional dependency is absent.
    try:
        from allthethings.engine_web.views import engine_web
        from allthethings.engine_api.views import engine_api
        from allthethings.engine_cli.views import engine_cli
        app.register_blueprint(engine_web)
        app.register_blueprint(engine_api)
        app.register_blueprint(engine_cli)
    except Exception as err:  # pragma: no cover - defensive
        app.logger.warning(
            "Engineering Intelligence platform not fully loaded: %r", err
        )

    extensions(app)

    return app


def extensions(app):
    """
    Register 0 or more extensions (mutates the app passed in).

    :param app: Flask application instance
    :return: None
    """
    debug_toolbar.init_app(app)
    db.init_app(app)
    flask_static_digest.init_app(app)
    with app.app_context():
        try:
            Reflected.prepare(db.engine)
        except:
            print("Error in loading tables; reset using './run flask cli dbreset'")
    es.init_app(app)

    # https://stackoverflow.com/a/18095320
    hash_cache = {}
    @app.url_defaults
    def add_hash_for_static_files(endpoint, values):
        '''Add content hash argument for url to make url unique.
        It's have sense for updates to avoid caches.
        '''
        if endpoint != 'static':
            return
        filename = values['filename']
        if filename in hash_cache:
            values['hash'] = hash_cache[filename]
            return
        filepath = safe_join(app.static_folder, filename)
        if os.path.isfile(filepath):
            with open(filepath, 'rb') as static_file:
                filehash = hashlib.md5(static_file.read()).hexdigest()[:20]
                values['hash'] = hash_cache[filename] = filehash

    return None


def middleware(app):
    """
    Register 0 or more middleware (mutates the app passed in).

    :param app: Flask application instance
    :return: None
    """
    # Enable the Flask interactive debugger in the brower for development.
    if app.debug:
        app.wsgi_app = DebuggedApplication(app.wsgi_app, evalex=True)

    # Set the real IP address into request.remote_addr when behind a proxy.
    app.wsgi_app = ProxyFix(app.wsgi_app)

    return None


celery_app = create_celery_app()
