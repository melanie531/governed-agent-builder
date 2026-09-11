"""Short-lived bearer material, isolated from streamed business state.

DynamoDB uses per-record conditional operations, never global revision fences.
SQLite is an offline test/local adapter only. No OTP is persisted.
"""
import json
import sqlite3
import time

import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException


class VerificationStore:
    def __init__(self, table_name=None, path=None):
        self.table = boto3.resource('dynamodb').Table(table_name) if table_name else None
        self.path = path
        if not self.table:
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE IF NOT EXISTS challenges (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def put(self, row):
        if self.table:
            self.table.put_item(Item=row, ConditionExpression='attribute_not_exists(id)')
        else:
            with sqlite3.connect(self.path) as db:
                db.execute('DELETE FROM challenges WHERE json_extract(body, "$.expires") <= ?', (int(time.time()),))
                db.execute('INSERT INTO challenges VALUES (?,?)', (row['id'], json.dumps(row)))

    def get(self, key):
        if self.table:
            row = self.table.get_item(Key={'id': key}, ConsistentRead=True).get('Item')
        else:
            with sqlite3.connect(self.path) as db:
                found = db.execute('SELECT body FROM challenges WHERE id=?', (key,)).fetchone()
                row = json.loads(found[0]) if found else None
        if not row or row['expires'] <= time.time():
            raise HTTPException(401, 'Verification expired; start sign-in again')
        return row

    def delete(self, key):
        if self.table:
            self.table.delete_item(Key={'id': key})
        else:
            with sqlite3.connect(self.path) as db:
                db.execute('DELETE FROM challenges WHERE id=?', (key,))

    def reserve(self, key, counter):
        if counter not in ('sends', 'attempts'): raise ValueError('Invalid counter')
        return self._change(key, counter)

    def consume(self, key):
        return self._change(key, None)

    def _change(self, key, counter):
        now = int(time.time())
        limit = 3 if counter == 'sends' else 5
        if self.table:
            try:
                condition = 'attribute_exists(id) AND expires > :now'
                values = {':now': now}
                if not counter:
                    return self.table.delete_item(Key={'id': key}, ConditionExpression=condition,
                        ExpressionAttributeValues=values, ReturnValues='ALL_OLD')['Attributes']
                condition += f' AND {counter} < :limit'
                values.update({':limit': limit, ':one': 1})
                update = f'SET {counter} = {counter} + :one'
                if counter == 'sends':
                    condition += ' AND next_send <= :now'
                    values[':next'] = now + 60
                    update += ', next_send = :next'
                return self.table.update_item(Key={'id': key}, ConditionExpression=condition, UpdateExpression=update,
                    ExpressionAttributeValues=values, ReturnValues='ALL_NEW')['Attributes']
            except ClientError as exc:
                if exc.response['Error']['Code'] != 'ConditionalCheckFailedException': raise
                raise HTTPException(429 if counter else 401, 'Verification unavailable or limit reached; start again when expired') from None
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            found = db.execute('SELECT body FROM challenges WHERE id=?', (key,)).fetchone()
            row = json.loads(found[0]) if found else None
            if not row or row['expires'] <= now:
                raise HTTPException(401, 'Verification expired; start sign-in again')
            if counter:
                if row[counter] >= limit or (counter == 'sends' and row['next_send'] > now):
                    raise HTTPException(429, 'Verification limit reached; wait or start again when expired')
                row[counter] += 1
                if counter == 'sends': row['next_send'] = now + 60
                db.execute('UPDATE challenges SET body=? WHERE id=?', (json.dumps(row), key))
            else:
                db.execute('DELETE FROM challenges WHERE id=?', (key,))
            return row
