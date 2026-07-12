from collections.abc import Generator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from flipthis_video_maker.database.session import Base, make_engine


@pytest.fixture
def db(tmp_path: pytest.TempPathFactory) -> Generator[Session, None, None]:
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, class_=Session)
    with factory() as session:
        yield session
    engine.dispose()
