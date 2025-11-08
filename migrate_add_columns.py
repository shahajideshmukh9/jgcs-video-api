#!/usr/bin/env python3
"""
Database Migration Script
Adds started_at, completed_at, and paused_at fields to missions table
"""

from database import engine, SessionLocal
from sqlalchemy import text
import sys

def add_mission_timestamp_fields():
    """Add timestamp fields to missions table"""
    print("🔄 Starting database migration...")
    print("   Adding: started_at, completed_at, paused_at fields")
    
    db = SessionLocal()
    
    try:
        # Check if columns already exist
        check_query = text("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'missions' 
            AND column_name IN ('started_at', 'completed_at', 'paused_at')
        """)
        
        existing_columns = [row[0] for row in db.execute(check_query).fetchall()]
        
        # Add started_at column if it doesn't exist
        if 'started_at' not in existing_columns:
            db.execute(text("""
                ALTER TABLE missions 
                ADD COLUMN started_at TIMESTAMP WITH TIME ZONE
            """))
            print("✅ Added column: started_at")
        else:
            print("ℹ️  Column 'started_at' already exists")
        
        # Add completed_at column if it doesn't exist
        if 'completed_at' not in existing_columns:
            db.execute(text("""
                ALTER TABLE missions 
                ADD COLUMN completed_at TIMESTAMP WITH TIME ZONE
            """))
            print("✅ Added column: completed_at")
        else:
            print("ℹ️  Column 'completed_at' already exists")
        
        # Add paused_at column if it doesn't exist
        if 'paused_at' not in existing_columns:
            db.execute(text("""
                ALTER TABLE missions 
                ADD COLUMN paused_at TIMESTAMP WITH TIME ZONE
            """))
            print("✅ Added column: paused_at")
        else:
            print("ℹ️  Column 'paused_at' already exists")
        
        # Commit the changes
        db.commit()
        print("\n✅ Migration completed successfully!")
        
        # Show updated table structure
        print("\n📋 Updated table structure:")
        result = db.execute(text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns 
            WHERE table_name = 'missions'
            ORDER BY ordinal_position
        """))
        
        for row in result.fetchall():
            nullable = "NULL" if row[2] == 'YES' else "NOT NULL"
            print(f"   - {row[0]}: {row[1]} ({nullable})")
        
        return True
        
    except Exception as e:
        db.rollback()
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        db.close()

if __name__ == "__main__":
    print("=" * 70)
    print("DATABASE MIGRATION: Add Mission Timestamp Fields")
    print("=" * 70)
    print()
    
    # Confirm before running
    response = input("This will modify the missions table. Continue? (yes/no): ")
    
    if response.lower() != 'yes':
        print("❌ Migration cancelled")
        sys.exit(0)
    
    print()
    success = add_mission_timestamp_fields()
    
    print()
    print("=" * 70)
    
    if success:
        print("🎉 Migration successful! You can now use the status update features.")
        print()
        print("Next steps:")
        print("  1. Update your crud.py with the new update_mission_status function")
        print("  2. Update your main.py with the new PATCH endpoint")
        print("  3. Restart your API: uvicorn main:app --reload")
        print("  4. Test the new endpoints at http://localhost:8000/docs")
    else:
        print("⚠️  Migration failed. Please check the error messages above.")
        sys.exit(1)
    
    print("=" * 70)