"""Application startup operations kept separate for fail-fast testing."""
from __future__ import annotations


def initialize_application() -> None:
    """Configure dependencies and initialize the database.

    Exceptions intentionally propagate so the process cannot advertise a
    healthy application after database initialization has failed.
    """
    from src.core.config import config
    from src.core.dependency_container import configure_services
    from src.db.session import get_db_session, init_database

    configure_services()
    print("依赖注入容器配置完成")

    init_database()
    print("SQLAlchemy数据库初始化完成")

    if config.allow_anonymous_access:
        from src.services.auth_service import AuthService

        db_gen = get_db_session()
        db = next(db_gen)
        try:
            AuthService.get_or_create_guest_user(
                db, config.guest_username, config.guest_email
            )
            print(f"访客账户已就绪: {config.guest_username}")
        finally:
            db.close()
