# Database Connection Management with cgutils

This project uses the `cgutils` package from congress-core-utils for database connection management. The cgutils package provides unified interfaces for MySQL, Redis, and other database systems with connection pooling and session management.

## Installation

The `cgutils` package is installed via the pyproject.toml dependencies:

```toml
dependencies = [
    "cgutils[mysql,storage]",
    ...
]
```

**Note:** Only `mysql` and `storage` extras are included as this project doesn't use Redis or FastAPI.

## MySQL Connection Management

### Basic Usage

```python
from cgutils.db import get_db, get_session

# Database configuration
db_config = {
    "host": "localhost",  # or "hostname"
    "port": 3306,
    "username": "root",
    "password": "secret",
    "database": "my_database",
}

# Get a session directly
session = get_db(db_config)
try:
    # Use the session for queries
    result = session.execute("SELECT * FROM my_table")
    session.commit()
finally:
    session.close()

# Or use context manager (recommended)
with get_session(db_config) as session:
    session.add(MyModel(name="example"))
    # Automatically commits on success, rolls back on exception
```

### Using with SQLAlchemy Models

```python
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base
from cgutils.db import get_session

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100))
    email = Column(String(100))

# Query with context manager
db_config = {
    "host": "localhost",
    "port": 3306,
    "username": "root",
    "password": "secret",
    "database": "my_app",
}

with get_session(db_config) as session:
    # Create
    new_user = User(name="John Doe", email="john@example.com")
    session.add(new_user)
    session.commit()

    # Read
    users = session.query(User).filter_by(name="John Doe").all()

    # Update
    user = session.query(User).first()
    user.email = "newemail@example.com"

    # Delete
    session.delete(user)
```

### Getting Engine for Advanced Operations

```python
from cgutils.db import get_db

# Get the SQLAlchemy engine
engine = get_db(db_config, return_engine=True)

# Use for raw SQL or metadata operations
with engine.connect() as conn:
    result = conn.execute("SELECT COUNT(*) FROM users")
    count = result.scalar()
```

### Connection Pool Monitoring

```python
from cgutils.db import get_pool_status

# Get pool status for a specific database
status = get_pool_status(db_config)
print(status)
# Output: {
#     'root@localhost:3306/my_database': {
#         'pool_size': 5,
#         'checked_in': 4,
#         'checked_out': 1
#     }
# }

# Get status for all pools
all_status = get_pool_status()
```

## Redis Connection Management

```python
from cgutils.db.redis_utils import get_redis

redis_config = {
    "host": "localhost",
    "port": 6379,
    "password": "secret",
    "db": 0,
}

# Get Redis client
redis_client = get_redis(redis_config)

# Use Redis
redis_client.set("key", "value")
value = redis_client.get("key")
```

## Environment Configuration

It's recommended to use environment variables for database configuration:

```python
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DB_HOST: str
    DB_PORT: int = 3306
    DB_USERNAME: str
    DB_PASSWORD: str
    DB_DATABASE: str

    class Config:
        env_file = ".env"

settings = Settings()

db_config = {
    "host": settings.DB_HOST,
    "port": settings.DB_PORT,
    "username": settings.DB_USERNAME,
    "password": settings.DB_PASSWORD,
    "database": settings.DB_DATABASE,
}
```

## Best Practices

1. **Use Context Managers**: Always use `get_session()` with a context manager to ensure proper cleanup
2. **Connection Pooling**: The manager automatically handles connection pooling - reuse the same `db_config` dict
3. **Error Handling**: Sessions automatically rollback on exceptions when using the context manager
4. **Multi-tenant**: Different database configs create separate connection pools
5. **Monitoring**: Use `get_pool_status()` to monitor connection pool usage in production

## Migration from Direct SQLAlchemy

If you have existing code using SQLAlchemy directly:

### Before
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine("mysql://user:pass@localhost/db")
Session = sessionmaker(bind=engine)

session = Session()
try:
    # database operations
    session.commit()
except:
    session.rollback()
    raise
finally:
    session.close()
```

### After
```python
from cgutils.db import get_session

db_config = {
    "host": "localhost",
    "username": "user",
    "password": "pass",
    "database": "db",
}

with get_session(db_config) as session:
    # database operations
    # auto-commits on success, auto-rollbacks on exception
```

## Additional Resources

- Source code: `../congress-core-utils/cgutils/db/`
- Tests: `../congress-core-utils/tests/test_mysql_manager.py`
- Example usage: See congress-notifications-server and other congress services
