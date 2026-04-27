from pathlib import Path
from time import sleep

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal, engine
from app.seed import seed_defaults


def main() -> None:
    wait_for_database()
    project_root = Path(__file__).resolve().parents[2]
    alembic_cfg = Config(str(project_root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(project_root / "migrations"))
    command.upgrade(alembic_cfg, "head")

    db = SessionLocal()
    try:
        seed_defaults(db)
    finally:
        db.close()


def wait_for_database(max_attempts: int = 30) -> None:
    last_error: Exception | None = None
    for _ in range(max_attempts):
        try:
            with engine.connect() as connection:
                connection.execute(text("select 1"))
            return
        except OperationalError as exc:
            last_error = exc
            sleep(1)
    raise RuntimeError("Database is not ready or credentials are invalid") from last_error


if __name__ == "__main__":
    main()
