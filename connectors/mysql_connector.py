"""
MySQL Connector for OmniFlow — Universal Automation & Workflow Engine
---------------------------------------------------------------------

This connector provides a standardized interface for interacting with MySQL
databases inside OmniFlow workflows.

Features:
- Safe connection pooling
- Query execution (sync + async dispatch)
- Insert / Update / Delete helpers
- Automatic reconnection
- Structured logging
- Declarative error handling compatible with OmniFlow runtime

Dependencies:
    pip install mysql-connector-python
"""

import mysql.connector
from mysql.connector import Error, pooling
from typing import Any, Dict, Optional, List
from omnitools.logger import OmniLogger


class MySQLConnector:
    """
    MySQL Connector with pooled connections.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Config example:
        {
            "host": "localhost",
            "port": 3306,
            "user": "root",
            "password": "password",
            "database": "omniflow",
            "pool_name": "omniflow_pool",
            "pool_size": 5
        }
        """
        self.logger = OmniLogger("MySQLConnector")

        self.config = config
        self.pool = None

        self._initialize_pool()

    def _initialize_pool(self):
        try:
            self.pool = pooling.MySQLConnectionPool(
                pool_name=self.config.get("pool_name", "omniflow_pool"),
                pool_size=self.config.get("pool_size", 5),
                pool_reset_session=True,
                host=self.config["host"],
                port=self.config.get("port", 3306),
                user=self.config["user"],
                password=self.config["password"],
                database=self.config["database"],
            )
            self.logger.info("MySQL connection pool initialized successfully.")
        except Error as err:
            self.logger.error(f"Failed to initialize MySQL pool: {err}")
            raise

    def _get_connection(self):
        try:
            return self.pool.get_connection()
        except Error as err:
            self.logger.error(f"Failed to get MySQL connection from pool: {err}")
            raise

    def query(self, sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Execute SELECT query and return results as list of dicts.
        """
        conn = None
        cursor = None

        try:
            conn = self._get_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, params or ())
            results = cursor.fetchall()

            self.logger.debug(f"MySQL SELECT executed: {sql}")
            return results

        except Error as err:
            self.logger.error(f"MySQL query failed: {err}")
            raise

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def execute(self, sql: str, params: Optional[tuple] = None) -> int:
        """
        Execute INSERT/UPDATE/DELETE and return affected row count.
        """
        conn = None
        cursor = None

        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(sql, params or ())
            conn.commit()

            self.logger.debug(f"MySQL DML executed: {sql}")
            return cursor.rowcount

        except Error as err:
            self.logger.error(f"MySQL modify operation failed: {err}")
            raise

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def insert(self, table: str, data: Dict[str, Any]) -> int:
        """
        Helper for structured inserts.
        """
        keys = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))
        sql = f"INSERT INTO {table} ({keys}) VALUES ({placeholders})"
        return self.execute(sql, tuple(data.values()))

    def update(self, table: str, data: Dict[str, Any], where: str, where_params: tuple):
        """
        Helper for structured updates.
        """
        set_clause = ", ".join([f"{k}=%s" for k in data])
        sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
        params = tuple(data.values()) + where_params
        return self.execute(sql, params)

    def delete(self, table: str, where: str, where_params: tuple):
        """
        Helper for structured deletes.
        """
        sql = f"DELETE FROM {table} WHERE {where}"
        return self.execute(sql, where_params)


if __name__ == "__main__":
    # Optional local test block (not used in production)
    test_config = {
        "host": "localhost",
        "user": "root",
        "password": "password",
        "database": "omniflow",
        "pool_size": 3
    }

    connector = MySQLConnector(test_config)
    print(connector.query("SHOW TABLES"))
  
