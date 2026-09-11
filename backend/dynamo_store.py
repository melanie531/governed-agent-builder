"""Durable DynamoDB repository. No SQL or local database in hosted mode.

A global conditional revision fences bounded transactions, including predicate
reads (quotas, grants and active-job exclusion). Low-throughput demo deliberately
trades concurrency for serializable governance checks. Conflicts fail closed.
"""
import copy
import json
from contextlib import contextmanager

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from fastapi import HTTPException

from .catalog import COMPONENTS, FOUNDATIONS
from .repository import AUTO_ID, SCHEMA, Result, Row, matches, record_key, validate


class DynamoStore:
    def __init__(self, table_name, resource=None):
        self.table = (resource or boto3.resource("dynamodb")).Table(table_name)

    def initialize(self):
        with self.tx() as db:
            if db.select("settings", where=[("key", "=", "seeded")]).fetchone():
                return
            for item in COMPONENTS:
                db.insert("components", {"id": item["id"], "body": json.dumps(item)}, ignore=True)
            for item in FOUNDATIONS:
                db.insert("foundations", {"id": item["id"], "body": json.dumps({**item, "approved": True})}, ignore=True)
            db.insert("settings", {"key": "seeded", "body": "true"})
            db.insert("settings", {"key": "policy", "body": json.dumps({"version": 1, "require_judge": False, "minimum_score": 1})})

    @contextmanager
    def tx(self):
        unit = DynamoUnit(self.table)
        yield unit
        unit.commit()


class DynamoUnit:
    def __init__(self, table):
        self.table = table
        fence = table.get_item(Key={"pk": "_revision", "sk": "_revision"}, ConsistentRead=True).get("Item", {})
        self.revision = int(fence.get("revision", 0))
        self.loaded, self.original = {}, {}

    def _rows(self, table):
        validate(table)
        if table not in self.loaded:
            rows, kwargs = {}, {"KeyConditionExpression": Key("pk").eq(table), "ConsistentRead": True}
            while True:
                page = self.table.query(**kwargs)
                for item in page.get("Items", []):
                    rows[item["sk"]] = json.loads(item["body"])
                if not page.get("LastEvaluatedKey"): break
                kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
            self.loaded[table] = rows
            self.original[table] = copy.deepcopy(rows)
        return self.loaded[table]

    def select(self, table, columns=None, where=(), order=None, descending=False, limit=None, count=False, any_of=False):
        validate(table, ([] if columns is None else columns) + [w[0] for w in where] + ([order] if order else []))
        rows = [copy.deepcopy(r) for r in self._rows(table).values() if matches(r, where, any_of)]
        if count: return Result([Row({"count": len(rows)})])
        if order: rows.sort(key=lambda r: r[order], reverse=descending)
        if limit is not None: rows = rows[:limit]
        return Result(Row({k: r[k] for k in columns}) if columns else Row(r) for r in rows)

    def insert(self, table, values, ignore=False, upsert=False):
        validate(table, values)
        rows = self._rows(table)
        row = {k: None for k in SCHEMA[table][0].split()}
        row.update(values)
        if table in AUTO_ID and row["id"] is None:
            row["id"] = max((r["id"] for r in rows.values()), default=0) + 1
        if table == "jobs" and row["attempts"] is None: row["attempts"] = 0
        key = record_key(table, row)
        if key in rows and not upsert:
            if ignore: return
            raise HTTPException(409, "Record already exists; reload and retry")
        rows[key] = row

    def update(self, table, values, where=(), increments=None):
        increments = increments or {}
        validate(table, list(values) + list(increments) + [w[0] for w in where])
        if set(values) & set(SCHEMA[table][1].split()):
            raise ValueError("Primary keys are immutable")
        for row in self._rows(table).values():
            if matches(row, where):
                row.update(values)
                for key, value in increments.items(): row[key] += value

    def delete(self, table, where=(), any_of=False):
        validate(table, [w[0] for w in where])
        rows = self._rows(table)
        for key in [k for k, r in rows.items() if matches(r, where, any_of)]: del rows[key]

    def commit(self):
        changes = []
        for table, rows in self.loaded.items():
            original = self.original[table]
            for key in original.keys() - rows.keys():
                changes.append({"Delete": {"TableName": self.table.name, "Key": {"pk": table, "sk": key}}})
            for key, row in rows.items():
                if original.get(key) != row:
                    body = json.dumps(row, separators=(",", ":"))
                    if len(body.encode()) > 350000:
                        raise HTTPException(413, "Evidence exceeds durable record limit")
                    changes.append({"Put": {"TableName": self.table.name, "Item": {"pk": table, "sk": key, "body": body}}})
        fence_key = {"pk": "_revision", "sk": "_revision"}
        condition = "#r = :old" if self.revision else "attribute_not_exists(#r)"
        names = {"#r": "revision"}
        values = {":old": self.revision} if self.revision else {}
        if changes:
            values[":new"] = self.revision + 1
            fence = {"Update": {"TableName": self.table.name, "Key": fence_key, "UpdateExpression": "SET #r = :new", "ConditionExpression": condition, "ExpressionAttributeNames": names, "ExpressionAttributeValues": values}}
        else:
            fence = {"ConditionCheck": {"TableName": self.table.name, "Key": fence_key, "ConditionExpression": condition, "ExpressionAttributeNames": names}}
            if values: fence["ConditionCheck"]["ExpressionAttributeValues"] = values
        if len(changes) > 99:
            raise HTTPException(429, "Transaction bound exceeded; contact operator")
        try:
            self.table.meta.client.transact_write_items(TransactItems=[fence, *changes])
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("TransactionCanceledException", "TransactionConflictException"):
                raise HTTPException(409, "Concurrent governance update; reload and retry") from None
            raise
