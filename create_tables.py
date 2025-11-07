#!/usr/bin/env python3
"""
Script to create database tables
Run this once to initialize your database schema
"""

from database import engine, Base
from db_models import MissionDB

def create_tables():
    """Create all tables in the database"""
    print("Creating database tables...")
    
    try:
        # Create all tables defined in Base metadata
        Base.metadata.create_all(bind=engine)
        print("✅ Tables created successfully!")
        print(f"   - {MissionDB.__tablename__}")
        
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
        raise

if __name__ == "__main__":
    create_tables()