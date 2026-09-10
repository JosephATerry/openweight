"""Environment-based PostgreSQL connection configuration."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.engine import URL


DEFAULT_POSTGRES_HOST = "localhost"
DEFAULT_POSTGRES_PORT = 5432
REQUIRED_POSTGRES_VARIABLES = (
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
)


@dataclass(frozen=True)
class PostgresConfig:
    database: str
    user: str
    password: str
    host: str = DEFAULT_POSTGRES_HOST
    port: int = DEFAULT_POSTGRES_PORT
    sslmode: str = "prefer"
    sslrootcert: str | None = None

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "PostgresConfig":
        values = os.environ if environ is None else environ
        missing = [name for name in REQUIRED_POSTGRES_VARIABLES if not values.get(name)]

        if missing:
            raise RuntimeError(
                "Missing required PostgreSQL environment variables: "
                + ", ".join(missing)
            )

        try:
            port = int(values.get("POSTGRES_PORT", str(DEFAULT_POSTGRES_PORT)))
        except ValueError as error:
            raise ValueError("POSTGRES_PORT must be an integer") from error

        if not 1 <= port <= 65535:
            raise ValueError("POSTGRES_PORT must be between 1 and 65535")

        sslmode = values.get("POSTGRES_SSLMODE", "prefer").strip().lower()
        if sslmode not in {"disable", "prefer", "require", "verify-ca", "verify-full"}:
            raise ValueError("POSTGRES_SSLMODE is not supported")
        sslrootcert = values.get("POSTGRES_SSLROOTCERT") or None

        return cls(
            database=values["POSTGRES_DB"],
            user=values["POSTGRES_USER"],
            password=values["POSTGRES_PASSWORD"],
            host=values.get("POSTGRES_HOST", DEFAULT_POSTGRES_HOST),
            port=port,
            sslmode=sslmode,
            sslrootcert=sslrootcert,
        )

    @property
    def sqlalchemy_url(self) -> str:
        url = URL.create(
            drivername="postgresql+psycopg",
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
            query={
                "sslmode": self.sslmode,
                **(
                    {"sslrootcert": self.sslrootcert}
                    if self.sslrootcert is not None
                    else {}
                ),
            },
        )
        return url.render_as_string(hide_password=False)

    @property
    def connect_kwargs(self) -> dict[str, str | int]:
        values: dict[str, str | int] = {
            "dbname": self.database,
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "sslmode": self.sslmode,
        }
        if self.sslrootcert is not None:
            values["sslrootcert"] = self.sslrootcert
        return values
