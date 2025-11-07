#!/usr/bin/env python3
"""
Database Connection Tester
This script helps diagnose PostgreSQL connection issues
"""

import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
import psycopg2

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def test_env_variables():
    """Test if environment variables are set"""
    print_section("1. Checking Environment Variables")
    
    db_url = os.getenv('DATABASE_URL')
    if db_url:
        print(f"✅ DATABASE_URL found: {mask_password(db_url)}")
        return db_url
    else:
        print("❌ DATABASE_URL not found in environment")
        print("\nTrying individual variables...")
        
        db_host = os.getenv('DB_HOST', 'localhost')
        db_port = os.getenv('DB_PORT', '5432')
        db_user = os.getenv('DB_USER', 'postgres')
        db_password = os.getenv('DB_PASSWORD', '')
        db_name = os.getenv('DB_NAME', 'uav_mission_db')
        
        if db_password:
            db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
            print(f"✅ Constructed URL: {mask_password(db_url)}")
            return db_url
        else:
            print("❌ No password found in environment variables")
            return None

def mask_password(url):
    """Mask password in connection string for security"""
    if '://' in url and '@' in url:
        parts = url.split('://')
        if len(parts) == 2:
            protocol = parts[0]
            rest = parts[1]
            if '@' in rest:
                creds, host_part = rest.split('@', 1)
                if ':' in creds:
                    user, _ = creds.split(':', 1)
                    return f"{protocol}://{user}:****@{host_part}"
    return url

def test_psycopg2_connection(db_url):
    """Test direct psycopg2 connection"""
    print_section("2. Testing Direct psycopg2 Connection")
    
    try:
        # Parse connection string
        # Format: postgresql://user:password@host:port/dbname
        url_parts = db_url.replace('postgresql://', '')
        creds, host_db = url_parts.split('@')
        user, password = creds.split(':')
        host_port, dbname = host_db.split('/')
        host, port = host_port.split(':') if ':' in host_port else (host_port, '5432')
        
        conn = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname
        )
        print("✅ psycopg2 connection successful!")
        
        # Test query
        cursor = conn.cursor()
        cursor.execute("SELECT version();")
        version = cursor.fetchone()
        print(f"✅ PostgreSQL version: {version[0]}")
        
        cursor.close()
        conn.close()
        return True
    except psycopg2.OperationalError as e:
        print(f"❌ psycopg2 connection failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def test_sqlalchemy_connection(db_url):
    """Test SQLAlchemy connection"""
    print_section("3. Testing SQLAlchemy Connection")
    
    try:
        engine = create_engine(db_url)
        connection = engine.connect()
        print("✅ SQLAlchemy connection successful!")
        
        # Test query
        result = connection.execute(text("SELECT current_database();"))
        db_name = result.fetchone()[0]
        print(f"✅ Connected to database: {db_name}")
        
        # Check if tables exist
        result = connection.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """))
        tables = result.fetchall()
        
        if tables:
            print(f"✅ Found {len(tables)} tables:")
            for table in tables:
                print(f"   - {table[0]}")
        else:
            print("⚠️  No tables found in database (you may need to run migrations)")
        
        connection.close()
        return True
    except OperationalError as e:
        print(f"❌ SQLAlchemy connection failed: {e}")
        print("\nCommon causes:")
        print("  1. Wrong password")
        print("  2. PostgreSQL not running")
        print("  3. Database doesn't exist")
        print("  4. Wrong host or port")
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

def check_postgresql_service():
    """Check if PostgreSQL service is running"""
    print_section("4. Checking PostgreSQL Service")
    
    import subprocess
    
    try:
        # Try systemctl (Linux)
        result = subprocess.run(['systemctl', 'status', 'postgresql'], 
                              capture_output=True, text=True, timeout=5)
        if 'active (running)' in result.stdout:
            print("✅ PostgreSQL service is running (systemctl)")
            return True
    except:
        pass
    
    try:
        # Try pg_isready
        result = subprocess.run(['pg_isready', '-h', 'localhost'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            print("✅ PostgreSQL is accepting connections (pg_isready)")
            return True
    except:
        pass
    
    print("⚠️  Could not verify PostgreSQL service status")
    print("   Try manually: systemctl status postgresql")
    return False

def provide_solutions():
    """Provide solution recommendations"""
    print_section("💡 Recommended Solutions")
    
    print("1. Verify PostgreSQL is running:")
    print("   sudo systemctl start postgresql")
    print()
    print("2. Reset PostgreSQL password:")
    print("   sudo -u postgres psql")
    print("   postgres=# ALTER USER postgres PASSWORD 'newpassword';")
    print()
    print("3. Create database if it doesn't exist:")
    print("   sudo -u postgres createdb uav_mission_db")
    print()
    print("4. Update .env file with correct credentials:")
    print("   DATABASE_URL=postgresql://postgres:yourpassword@localhost:5432/uav_mission_db")
    print()
    print("5. Check pg_hba.conf for authentication settings:")
    print("   Location: /etc/postgresql/*/main/pg_hba.conf")
    print("   Ensure: host all all 127.0.0.1/32 md5")
    print()

def main():
    """Main test runner"""
    print("\n" + "="*60)
    print("  PostgreSQL Connection Diagnostic Tool")
    print("="*60)
    
    # Load .env file if python-dotenv is available
    try:
        from dotenv import load_dotenv
        load_dotenv()
        print("✅ Loaded .env file")
    except ImportError:
        print("⚠️  python-dotenv not installed, using system environment")
    
    # Run tests
    db_url = test_env_variables()
    
    if not db_url:
        print("\n❌ Cannot proceed without database URL")
        provide_solutions()
        sys.exit(1)
    
    check_postgresql_service()
    psycopg2_ok = test_psycopg2_connection(db_url)
    sqlalchemy_ok = test_sqlalchemy_connection(db_url)
    
    # Summary
    print_section("📊 Test Summary")
    
    if psycopg2_ok and sqlalchemy_ok:
        print("✅ All tests passed! Database connection is working.")
        print("✅ Your application should be able to connect successfully.")
    else:
        print("❌ Some tests failed. See solutions below.")
        provide_solutions()
    
    print("\n" + "="*60 + "\n")

if __name__ == "__main__":
    main()