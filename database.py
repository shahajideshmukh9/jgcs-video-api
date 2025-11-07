# database.py
# Database Configuration and Connection Management

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base  # ✅ FIXED: Changed from sqlalchemy.ext.declarative
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database URL from environment variable
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:digambar@localhost:5432/jarbits_db"
)

# Create SQLAlchemy engine
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Enable connection health checks
    pool_size=10,  # Maximum number of database connections in the pool
    max_overflow=20,  # Maximum overflow connections
    echo=False  # Set to True to see SQL queries (for debugging)
)

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create Base class for models
Base = declarative_base()

# Dependency to get database session
def get_db():
    """
    Dependency function to get database session
    Automatically closes session after request is completed
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Initialize database (create tables)
def init_db():
    """
    Initialize database by creating all tables
    Call this function on application startup
    """
    import db_models  # Import models to register them
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created successfully")

# Drop all tables (use with caution!)
def drop_db():
    """
    Drop all database tables
    WARNING: This will delete all data!
    """
    Base.metadata.drop_all(bind=engine)
    print("⚠️  All database tables dropped")

# Test database connection
def test_connection():
    """
    Test database connection
    Returns True if connection is successful, False otherwise
    """
    try:
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        print("✅ Database connection successful")
        return True
    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        return False