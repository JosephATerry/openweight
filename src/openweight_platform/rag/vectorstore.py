"""PostgreSQL-backed policy vector-store construction and inspection."""

import psycopg
from langchain_core.embeddings import Embeddings
from langchain_postgres import Column, PGEngine, PGVectorStore
from psycopg import sql

from openweight_platform.rag.database import PostgresConfig
from openweight_platform.rag.embeddings import EMBEDDING_DIMENSION


POLICY_CHUNKS_TABLE = "policy_chunks"
POLICY_CHUNKS_SCHEMA = "public"
POLICY_METADATA_COLUMNS = (
    "policy_id",
    "title",
    "domain",
    "source_path",
    "chunk_index",
)


def policy_chunks_table_exists(config: PostgresConfig) -> bool:
    """Return whether the configured policy vector table exists."""

    qualified_table = f"{POLICY_CHUNKS_SCHEMA}.{POLICY_CHUNKS_TABLE}"

    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)", (qualified_table,))
            row = cursor.fetchone()

    return row is not None and row[0] is not None


def create_policy_vector_store(
    config: PostgresConfig,
    embeddings: Embeddings,
    *,
    initialize: bool = False,
) -> tuple[PGEngine, PGVectorStore]:
    """Create a PGEngine and policy PGVectorStore, optionally creating its table."""

    engine = PGEngine.from_connection_string(url=config.sqlalchemy_url)

    if initialize and not policy_chunks_table_exists(config):
        engine.init_vectorstore_table(
            table_name=POLICY_CHUNKS_TABLE,
            vector_size=EMBEDDING_DIMENSION,
            metadata_columns=[
                Column("policy_id", "TEXT", nullable=False),
                Column("title", "TEXT", nullable=False),
                Column("domain", "TEXT", nullable=False),
                Column("source_path", "TEXT", nullable=False),
                Column("chunk_index", "INTEGER", nullable=False),
            ],
            store_metadata=False,
        )

    store = PGVectorStore.create_sync(
        engine=engine,
        table_name=POLICY_CHUNKS_TABLE,
        embedding_service=embeddings,
        metadata_columns=list(POLICY_METADATA_COLUMNS),
    )
    return engine, store


def count_policy_chunks(config: PostgresConfig) -> int:
    """Count rows in the policy vector table."""

    statement = sql.SQL("SELECT count(*) FROM {}.{}").format(
        sql.Identifier(POLICY_CHUNKS_SCHEMA),
        sql.Identifier(POLICY_CHUNKS_TABLE),
    )

    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(statement)
            row = cursor.fetchone()

    if row is None:
        raise RuntimeError("PostgreSQL did not return a policy chunk count")

    return int(row[0])
