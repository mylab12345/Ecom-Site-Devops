from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from .config import settings
import time
import logging

logger = logging.getLogger(__name__)

# Retry logic for container startup ordering
engine = None
for i in range(10):
    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
        # test connection
        with engine.connect() as conn:
            conn.execute
        break
    except Exception as e:
        logger.warning(f"DB connection failed attempt {i+1}/10: {e}")
        time.sleep(3)
        if i == 9:
            engine = create_engine(settings.database_url, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
