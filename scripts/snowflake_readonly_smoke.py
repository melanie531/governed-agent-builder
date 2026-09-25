#!/usr/bin/env python3
"""Standalone, opt-in read-only Snowflake smoke. NOT wired into the app default path.

Refuses unless SNOWFLAKE_SMOKE=1 and every SNOWFLAKE_SMOKE_SSM_* variable names an SSM
parameter (a /path, never a value). Credentials and identifiers are read from SSM at run time
only and are never accepted as arguments, printed or logged. Only a view from the SSM
whitelist can be queried, through one fixed read-only statement shape; there is no SQL input.

Needs the Snowflake connector, which is not a project dependency:
  SNOWFLAKE_SMOKE=1 SNOWFLAKE_SMOKE_SSM_...=/... \\
    uv run --with snowflake-connector-python python scripts/snowflake_readonly_smoke.py --query <APPROVED_VIEW>
"""
import argparse
import json
import os
import re
import sys

import boto3

SSM_ENV = {
    "account": "SNOWFLAKE_SMOKE_SSM_ACCOUNT",
    "user": "SNOWFLAKE_SMOKE_SSM_USER",
    "private_key": "SNOWFLAKE_SMOKE_SSM_PRIVATE_KEY",
    "role": "SNOWFLAKE_SMOKE_SSM_ROLE",
    "warehouse": "SNOWFLAKE_SMOKE_SSM_WAREHOUSE",
    "database": "SNOWFLAKE_SMOKE_SSM_DATABASE",
    "schema": "SNOWFLAKE_SMOKE_SSM_SCHEMA",
    "approved_views": "SNOWFLAKE_SMOKE_SSM_APPROVED_VIEWS",
}
SSM_PARAMETER_NAME = re.compile(r"^/[A-Za-z0-9_.\-/]{1,1000}$")
IDENTIFIER = re.compile(r"^[A-Z_][A-Z0-9_$]{0,254}$")
ROW_LIMIT = 5


class SmokeRefused(Exception):
    pass


def parameter_names(env):
    if env.get("SNOWFLAKE_SMOKE") != "1":
        raise SmokeRefused("SNOWFLAKE_SMOKE=1 is required")
    missing = [var for var in SSM_ENV.values() if not env.get(var)]
    if missing:
        raise SmokeRefused("missing SSM parameter names: " + ", ".join(missing))
    not_names = [var for var in SSM_ENV.values() if not SSM_PARAMETER_NAME.match(env[var])]
    if not_names:
        raise SmokeRefused("must hold SSM parameter names (/path), not values: " + ", ".join(not_names))
    return {key: env[var] for key, var in SSM_ENV.items()}


def read_config(ssm, names):
    config = {key: ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"] for key, name in names.items()}
    try:
        views = json.loads(config["approved_views"])
    except ValueError:
        raise SmokeRefused("approved views must be a JSON list of identifiers") from None
    if not isinstance(views, list) or not views or not all(isinstance(v, str) and IDENTIFIER.match(v) for v in views):
        raise SmokeRefused("approved views must be a JSON list of identifiers")
    if not all(IDENTIFIER.match(config[key]) for key in ("role", "warehouse", "database", "schema")):
        raise SmokeRefused("role, warehouse, database and schema must be plain identifiers")
    config["approved_views"] = views
    return config


def statement(config, query):
    if query not in config["approved_views"]:
        raise SmokeRefused("query is not a whitelisted approved view")
    return f'SELECT * FROM "{config["database"]}"."{config["schema"]}"."{query}" LIMIT {ROW_LIMIT}'


def snowflake_connect(config):
    import snowflake.connector
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(config["private_key"].encode(), password=None)
    der = key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return snowflake.connector.connect(account=config["account"], user=config["user"], private_key=der,
                                       role=config["role"], warehouse=config["warehouse"],
                                       database=config["database"], schema=config["schema"])


def run(config, query, connect):
    sql = statement(config, query)
    connection = connect(config)
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        column_count = len(cursor.description)
    finally:
        connection.close()
    # Counts only: row values, view names and identifiers are never echoed.
    return {"row_count": len(rows), "column_count": column_count, "row_limit": ROW_LIMIT}


def main(argv=None, env=None, ssm=None, connect=snowflake_connect):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--query", required=True, help="name of an approved view from the SSM whitelist")
    args = p.parse_args(argv)
    try:
        if not IDENTIFIER.match(args.query):
            raise SmokeRefused("query must be an approved view name, not SQL")
        names = parameter_names(os.environ if env is None else env)
        config = read_config(ssm or boto3.client("ssm"), names)
        result = run(config, args.query, connect)
    except SmokeRefused as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc)}))
        return 2
    except Exception as exc:
        # Driver and AWS messages can contain account or user identifiers; report the type only.
        print(json.dumps({"status": "FAILED", "error": type(exc).__name__}))
        return 1
    print(json.dumps({"status": "PASS", **result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
