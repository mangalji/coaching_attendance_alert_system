"""Project configuration package."""

# PyMySQL is a portable MySQL driver and exposes the MySQLdb-compatible API that
# Django's MySQL backend expects. Importing it here keeps MySQL selectable through
# environment configuration without affecting SQLite or PostgreSQL deployments.
try:
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:
    pass
