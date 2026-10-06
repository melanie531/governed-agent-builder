"""Structured persistence contract shared by SQLite and DynamoDB adapters."""
import json

SCHEMA = {
    "sessions": ("id persona csrf expires", "id"),
    "components": ("id body", "id"), "foundations": ("id body", "id"),
    "catalog_history": ("id resource body created", "id"),
    "grants": ("persona component", "persona component"),
    "agents": ("id owner workspace current_version created", "id"),
    "versions": ("agent version digest body created", "agent version"),
    "jobs": ("id agent version requester idem stage result created updated deadline attempts", "id"),
    "events": ("id job stage detail created", "id"),
    "requests": ("id requester workspace component reason status decision created", "id"),
    "general_requests": ("id requester workspace summary details status resolution created updated", "id"),
    "audit": ("id actor action resource detail created", "id"),
    "settings": ("key body", "key"),
    "oidc_flows": ("state_hash verifier nonce expires", "state_hash"),
    "hosted_sessions": ("id_hash subject access_token csrf expires active_group refresh_token qa", "id_hash"),
    "principals": ("id body expires", "id"),
    "job_authority": ("id session_hash", "id"),
}
AUTO_ID = {"audit", "events", "catalog_history"}


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result(list):
    def fetchone(self):
        return self[0] if self else None

    def fetchall(self):
        return self


def validate(table, fields=()):
    if table not in SCHEMA or any(f not in SCHEMA[table][0].split() for f in fields):
        raise ValueError("Unknown repository entity or field")


def matches(row, where, any_of=False):
    values = []
    for field, op, value in where:
        actual = row.get(field)
        if op == "=": values.append(actual == value)
        elif op == ">": values.append(actual is not None and actual > value)
        elif op == "<": values.append(actual is not None and actual < value)
        elif op == "not_in": values.append(actual not in value)
        else: raise ValueError("Unknown repository predicate")
    return (any(values) if any_of else all(values)) if values else True


def record_key(table, row):
    return json.dumps([row[k] for k in SCHEMA[table][1].split()], separators=(",", ":"))


class SQLiteRepository:
    """SQL generation stays inside this adapter. No SQL in hosted application."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, *args):
        # Compatibility for existing local tests and schema bootstrap only.
        return self.connection.execute(*args)

    def executemany(self, *args):
        return self.connection.executemany(*args)

    def executescript(self, *args):
        return self.connection.executescript(*args)

    def _where(self, table, where, any_of):
        validate(table, [w[0] for w in where])
        clauses, args = [], []
        for field, op, value in where:
            if op == "not_in":
                clauses.append(f'{field} NOT IN ({",".join("?" for _ in value)})')
                args.extend(value)
            elif op in ("=", ">", "<"):
                clauses.append(f"{field}{op}?")
                args.append(value)
            else:
                raise ValueError("Unknown predicate")
        return (" WHERE " + (" OR " if any_of else " AND ").join(clauses) if clauses else ""), args

    def select(self, table, columns=None, where=(), order=None, descending=False, limit=None, count=False, any_of=False):
        validate(table, ([] if columns is None else columns) + ([order] if order else []))
        clause, args = self._where(table, where, any_of)
        fields = "COUNT(*)" if count else ",".join(columns) if columns else "*"
        sql = f"SELECT {fields} FROM {table}" + clause
        if order: sql += f' ORDER BY {order}' + (' DESC' if descending else '')
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return self.connection.execute(sql, args)

    def insert(self, table, values, ignore=False, upsert=False):
        validate(table, values)
        keys = list(values)
        sql = f'INSERT {"OR IGNORE " if ignore else ""}INTO {table} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})'
        if upsert:
            primary = SCHEMA[table][1].split()
            sql += f' ON CONFLICT({",".join(primary)}) DO UPDATE SET ' + ','.join(f'{k}=excluded.{k}' for k in keys if k not in primary)
        return self.connection.execute(sql, [values[k] for k in keys])

    def update(self, table, values, where=(), increments=None):
        increments = increments or {}
        validate(table, list(values) + list(increments))
        clause, args = self._where(table, where, False)
        assignments = [f'{k}=?' for k in values] + [f'{k}={k}+?' for k in increments]
        return self.connection.execute(f'UPDATE {table} SET {",".join(assignments)}' + clause, [*values.values(), *increments.values(), *args])

    def delete(self, table, where=(), any_of=False):
        clause, args = self._where(table, where, any_of)
        return self.connection.execute(f'DELETE FROM {table}' + clause, args)
