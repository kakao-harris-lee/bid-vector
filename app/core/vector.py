"""Vector type helpers with a graceful fallback when pgvector is unavailable."""

from sqlalchemy.types import UserDefinedType

try:
    from pgvector.sqlalchemy import VECTOR as PGVECTOR_VECTOR

    VECTOR = PGVECTOR_VECTOR
    PGVECTOR_SQLALCHEMY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when optional dependency is absent locally
    PGVECTOR_SQLALCHEMY_AVAILABLE = False

    class VECTOR(UserDefinedType):
        """Fallback VECTOR type so metadata can still be created in lightweight environments."""

        cache_ok = True

        def __init__(self, dimensions: int):
            self.dimensions = dimensions

        def get_col_spec(self, **kw):
            return f"VECTOR({self.dimensions})"