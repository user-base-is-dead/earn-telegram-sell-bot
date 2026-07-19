"""Drop-in stand-ins for the ``asyncpg`` names the query modules reference,
backed by the local SQLite layer (app.db.schema).

Each query module used to ``import asyncpg`` and then annotate/return
``asyncpg.Record``, type a helper as ``asyncpg.Connection``, or catch
``asyncpg.UniqueViolationError``. After the switch from Postgres to a local
SQLite file, those modules simply do ``from app.db import _compat as asyncpg``
instead, so every ``asyncpg.<name>`` reference keeps working unchanged.
"""
import sqlite3

from app.db.schema import Connection, Record

# A UNIQUE/partial-unique index violation surfaces from sqlite3 as
# IntegrityError. The only INSERTs the query modules wrap in a try/except are
# guarded by a unique index (deposits' pending tag, processed_tx), so mapping
# the broader IntegrityError here is exact enough for those call sites.
UniqueViolationError = sqlite3.IntegrityError

__all__ = ["Connection", "Record", "UniqueViolationError"]
