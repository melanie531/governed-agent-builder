"""Fresh, role-bound OAuth sessions; tokens never enter tool arguments or results."""
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
import json
import os
import re

import sqlglot
from sqlglot import exp

SAFE_ERROR = "Snowflake operation failed; no result was returned."
MAX_ROWS = 200
MAX_BYTES = 64_000
# SQL permissions remain the security boundary. This additional allowlist excludes
# stored procedures, external/UDF calls, stage/file operations and system functions.
FUNCTIONS = set("""
ABS ACOS APPROX_DISTINCT ARRAY_AGG ARRAY_SIZE ASC ASCII ASIN ATAN ATAN2 AVG
CASE CAST CEIL COALESCE CONCAT CONCAT_WS CONVERT_TIMEZONE CORR COUNT COVAR_POP COVAR_SAMP
CURRENT_ACCOUNT CURRENT_DATABASE CURRENT_DATE CURRENT_ROLE CURRENT_SCHEMA
CURRENT_TIMESTAMP CURRENT_USER CURRENT_VERSION CURRENT_WAREHOUSE DATE_ADD DATE_DIFF
DATE_PART DATE_TRUNC DAY DENSE_RANK EXTRACT FIRST_VALUE FLOOR GREATEST GROUP_CONCAT
IF IFF IFNULL INITCAP LAG LAST_DAY LAST_VALUE LEAD LEAST LEFT LENGTH LN LOG LOG10
LOWER LPAD LTRIM MAX MEDIAN MIN MOD MONTH NTH_VALUE NTILE NULLIF NVL NVL2
PERCENT_RANK PERCENTILE_CONT PERCENTILE_DISC POW POWER RANK REGEXP_EXTRACT
REGEXP_LIKE REGEXP_REPLACE REPLACE RIGHT ROUND ROW_NUMBER RPAD RTRIM SIGN
SPLIT SPLIT_PART SQRT STDDEV STDDEV_POP STDDEV_SAMP STR_TO_DATE STR_TO_TIME
SUBSTRING SUM TIME_TO_STR TIMESTAMP_ADD TIMESTAMP_DIFF TIMESTAMP_TRUNC TO_CHAR TO_DATE TO_NUMBER
TO_TIMESTAMP TRIM TRUNC TRY_CAST UPPER VAR_POP VAR_SAMP VARIANCE WEEK YEAR
""".split())


def readonly_sql(sql):
    if not isinstance(sql, str) or not sql.strip() or len(sql) > 16_000:
        raise ValueError("Provide one read-only SELECT query of at most 16000 characters.")
    try:
        statements = sqlglot.parse(sql, read="snowflake")
    except sqlglot.errors.ParseError:
        raise ValueError("The query must be valid read-only Snowflake SQL.") from None
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
        raise ValueError("Only a single SELECT or WITH ... SELECT query is allowed.")
    for node in statements[0].walk():
        if isinstance(node, (exp.DDL, exp.DML, exp.Command, exp.Into)):
            raise ValueError("Queries cannot change data, sessions, roles or Snowflake objects.")
        if isinstance(node, exp.Func):
            name = node.name.upper() if isinstance(node, exp.Anonymous) else node.sql_name().upper()
            if name not in FUNCTIONS or isinstance(node.parent, exp.Dot):
                raise ValueError("This function is not in the read-only built-in function allowlist.")
    return sql


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 255 or any(ord(c) < 32 for c in value):
        raise ValueError("Use an exact Snowflake object name of 1 to 255 characters.")
    return '"' + value.replace('"', '""') + '"'


@dataclass(frozen=True)
class Settings:
    account: str
    role: str
    warehouse: str

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.account):
            raise ValueError("Use a Snowflake organization-account identifier.")
        identifier(self.role)
        identifier(self.warehouse)
        if self.role.upper() in {"ACCOUNTADMIN", "SECURITYADMIN", "USERADMIN", "SYSADMIN", "ORGADMIN"}:
            raise ValueError("Use a dedicated Snowflake reader role.")

    @classmethod
    def environment(cls):
        return cls(account=os.environ["SNOWFLAKE_ACCOUNT"], role=os.environ["SNOWFLAKE_ROLE"],
                   warehouse=os.environ["SNOWFLAKE_WAREHOUSE"])


def cell(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)  # Preserve Snowflake numeric precision.
    if isinstance(value, bytes):
        return value.hex()
    return value


class Database:
    def __init__(self, settings, *, access_token, connect=None):
        if not isinstance(access_token, str) or not 1 <= len(access_token) <= 131072:
            raise ValueError("Connect your account in Studio before querying Snowflake.")
        if connect is None:
            import snowflake.connector
            connect = snowflake.connector.connect
        self.settings, self.connect = settings, connect
        self._access_token = access_token

    def execute(self, sql, max_rows=MAX_ROWS):
        if type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS:
            raise ValueError("max_rows must be between 1 and 200.")
        connection = cursor = None
        stage = step = "connect"
        try:
            connection = self.connect(
                account=self.settings.account, role=self.settings.role, warehouse=self.settings.warehouse,
                authenticator="oauth", token=self._access_token, secondary_roles="NONE",
                login_timeout=10, network_timeout=25, socket_timeout=10,
                client_session_keep_alive=False,
                session_parameters={"STATEMENT_TIMEOUT_IN_SECONDS": 20,
                                    "STATEMENT_QUEUED_TIMEOUT_IN_SECONDS": 5,
                                    "QUERY_TAG": "agentcore_runtime_readonly_mcp",
                                    "ROWS_PER_RESULTSET": max_rows + 1})
            stage = "session_setup"
            step = "cursor"
            cursor = connection.cursor()

            def roles_are_restricted():
                cursor.execute("SELECT CURRENT_ROLE(), CURRENT_SECONDARY_ROLES()")
                row = cursor.fetchone()
                if not isinstance(row, (tuple, list)) or len(row) != 2 or row[0] != self.settings.role:
                    raise ValueError("Snowflake role verification failed")
                secondary = json.loads(row[1])
                if (not isinstance(secondary, dict)
                        or not isinstance(secondary.get("roles"), str)
                        or not isinstance(secondary.get("value"), str)):
                    raise ValueError("Snowflake secondary-role verification failed")
                # Snowflake reports requested roles as a list. An empty list
                # can be encoded as "" rather than the explicit NONE setting.
                return secondary["roles"] == "" and secondary["value"] in ("", "NONE")

            step = "inspect_roles"
            if not roles_are_restricted():
                step = "verify_roles"
                raise ValueError("Snowflake secondary roles remained active")
            stage = step = "statement"
            cursor.execute(sql, timeout=25)
            stage = step = "result"
            names = [str(column[0]) for column in cursor.description or []]
            result = {"columns": names, "rows": [], "truncated": False, "query_id": cursor.sfqid}
            rows = cursor.fetchmany(max_rows + 1)
            result["truncated"] = len(rows) > max_rows
            if len(json.dumps(result).encode()) > MAX_BYTES:
                return {"error": "Result metadata exceeds the response limit. Select fewer columns."}
            for row in rows[:max_rows]:
                converted = [cell(value) for value in row]
                if len(json.dumps({**result, "rows": [*result["rows"], converted]}, default=str).encode()) > MAX_BYTES:
                    result["truncated"] = True
                    break
                result["rows"].append(converted)
            return result
        except Exception as exc:
            # Stages and steps are fixed code-owned metadata. Connector messages
            # can include SQL, URLs or credentials, so only bounded codes escape.
            result = {"error": SAFE_ERROR, "failure_stage": stage, "failure_step": step}
            errno = getattr(exc, "errno", None)
            if type(errno) is int and 0 < errno <= 999999:
                result["snowflake_errno"] = errno
            sqlstate = getattr(exc, "sqlstate", None)
            if type(sqlstate) is str and re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
                result["snowflake_sqlstate"] = sqlstate
            return result
        finally:
            for resource in (cursor, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass

    def test_connection(self):
        return self.execute("SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE(), CURRENT_VERSION()", 1)

    def list_databases(self):
        return self.execute("SHOW DATABASES LIMIT 201")

    def list_schemas(self, database):
        return self.execute("SHOW SCHEMAS IN DATABASE " + identifier(database) + " LIMIT 201")

    def list_tables(self, database, schema):
        return self.execute("SHOW TABLES IN SCHEMA " + identifier(database) + "." + identifier(schema) + " LIMIT 201")

    def list_views(self, database, schema):
        return self.execute("SHOW VIEWS IN SCHEMA " + identifier(database) + "." + identifier(schema) + " LIMIT 201")

    def describe_table(self, database, schema, table):
        return self.execute("DESCRIBE TABLE " + ".".join(identifier(v) for v in (database, schema, table)))

    def query(self, sql, max_rows=MAX_ROWS):
        return self.execute(readonly_sql(sql), max_rows)
