"""WSGI entrypoint for Render / PythonAnywhere.

Exports both `app` and `application` so either gunicorn wsgi:app
or gunicorn wsgi:application works.
"""

from app import app  # noqa: F401

# PythonAnywhere convention
application = app
