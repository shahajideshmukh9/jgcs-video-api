#!/usr/bin/env python3
"""
Load Default Missions - Jarbits Mission Management System
Populates the database with 5 sample UAV missions
"""

from database import SessionLocal, init_db
from models import MissionCreate, Corridor, MissionStats, Waypoint
from crud import create_mission
from sqlalchemy.exc import IntegrityError

# Sample missions data
DEFAULT_MISSIONS = [
    {
        "mission_name": "Northern Border Patrol Alpha",
        "mission_type": "Border Surveillance",
        "corridor": Corridor(
            value="northern",
            label="Northern Border Corridor",
            color="blue",
            description="India-Nepal border surveillance"
        ),
        "mission_stats": MissionStats(
            total_distance=87.65,
            flight_time=35.1,
            battery_usage=87.65
        ),
        "waypoints": [
            Waypoint(
                id="start",
                label="Start: Mohanlalganj",
                coords="26.7465° N, 80.8769° E",
                alt="120m AGL",
                color="bg-green-500",
                lat=26.7465,
                lon=80.8769
            ),
            Waypoint(
                id="wp1",
                label="Checkpoint 1: Malihabad",
                coords="26.9237° N, 80.7128° E",
                alt="150m AGL",
                color="bg-blue-500",
                lat=26.9237,
                lon=80.7128
            ),
            Waypoint(
                id="wp2",
                label="Checkpoint 2: Kakori",
                coords="26.8654° N, 80.7891° E",
                alt="140m AGL",
                color="bg-blue-500",
                lat=26.8654,
                lon=80.7891
            ),
            Waypoint(
                id="end",
                label="End: Base Station",
                coords="26.8467° N, 80.9462° E",
                alt="100m AGL",
                color="bg-red-500",
                lat=26.8467,
                lon=80.9462
            )
        ],
        "created_by": "admin",
        "notes": "Standard northern border patrol route with 4 waypoints",
        "vehicle_id": "UAV-001",
        "operator_id": "OP-101"
    },
    {
        "mission_name": "Western Border Recon Bravo",
        "mission_type": "Border Reconnaissance",
        "corridor": Corridor(
            value="western",
            label="Western Border Corridor",
            color="orange",
            description="India-Pakistan border region"
        ),
        "mission_stats": MissionStats(
            total_distance=78.4,
            flight_time=38.2,
            battery_usage=82.5
        ),
        "waypoints": [
            Waypoint(
                id="start",
                label="Start: Western Base",
                coords="27.1234° N, 79.8765° E",
                alt="180m AGL",
                color="bg-green-500",
                lat=27.1234,
                lon=79.8765
            ),
            Waypoint(
                id="wp1",
                label="Border Point Alpha",
                coords="27.1789° N, 79.9234° E",
                alt="220m AGL",
                color="bg-orange-500",
                lat=27.1789,
                lon=79.9234
            ),
            Waypoint(
                id="wp2",
                label="Border Point Bravo",
                coords="27.2123° N, 79.9678° E",
                alt="200m AGL",
                color="bg-orange-500",
                lat=27.2123,
                lon=79.9678
            ),
            Waypoint(
                id="wp3",
                label="Observation Post",
                coords="27.2456° N, 79.8934° E",
                alt="210m AGL",
                color="bg-orange-500",
                lat=27.2456,
                lon=79.8934
            ),
            Waypoint(
                id="end",
                label="End: Western Base",
                coords="27.1234° N, 79.8765° E",
                alt="180m AGL",
                color="bg-red-500",
                lat=27.1234,
                lon=79.8765
            )
        ],
        "created_by": "admin",
        "notes": "High-altitude reconnaissance mission in western border region",
        "vehicle_id": "UAV-002",
        "operator_id": "OP-102"
    },
    {
        "mission_name": "Eastern Surveillance Charlie",
        "mission_type": "Area Surveillance",
        "corridor": Corridor(
            value="eastern",
            label="Eastern Border Corridor",
            color="green",
            description="India-Bangladesh/Myanmar border"
        ),
        "mission_stats": MissionStats(
            total_distance=65.4,
            flight_time=28.5,
            battery_usage=71.25
        ),
        "waypoints": [
            Waypoint(
                id="start",
                label="Start: Forward Base",
                coords="26.5678° N, 81.2345° E",
                alt="110m AGL",
                color="bg-green-500",
                lat=26.5678,
                lon=81.2345
            ),
            Waypoint(
                id="wp1",
                label="Surveillance Point Alpha",
                coords="26.6234° N, 81.3456° E",
                alt="130m AGL",
                color="bg-green-600",
                lat=26.6234,
                lon=81.3456
            ),
            Waypoint(
                id="wp2",
                label="Surveillance Point Beta",
                coords="26.6789° N, 81.4012° E",
                alt="125m AGL",
                color="bg-green-600",
                lat=26.6789,
                lon=81.4012
            ),
            Waypoint(
                id="end",
                label="End: Return Base",
                coords="26.5678° N, 81.2345° E",
                alt="110m AGL",
                color="bg-red-500",
                lat=26.5678,
                lon=81.2345
            )
        ],
        "created_by": "admin",
        "notes": "Circular route for area surveillance with return to base",
        "vehicle_id": "UAV-003",
        "operator_id": "OP-103"
    },
    {
        "mission_name": "Coastal Monitoring Delta",
        "mission_type": "Coastal Patrol",
        "corridor": Corridor(
            value="southern",
            label="Southern Coastal Corridor",
            color="purple",
            description="Coastal surveillance and monitoring"
        ),
        "mission_stats": MissionStats(
            total_distance=92.3,
            flight_time=41.2,
            battery_usage=91.5
        ),
        "waypoints": [
            Waypoint(
                id="start",
                label="Start: Coastal Station",
                coords="26.3456° N, 80.1234° E",
                alt="100m AGL",
                color="bg-green-500",
                lat=26.3456,
                lon=80.1234
            ),
            Waypoint(
                id="wp1",
                label="Waypoint: Beach Sector 1",
                coords="26.4123° N, 80.2456° E",
                alt="120m AGL",
                color="bg-purple-500",
                lat=26.4123,
                lon=80.2456
            ),
            Waypoint(
                id="wp2",
                label="Waypoint: Beach Sector 2",
                coords="26.4789° N, 80.3678° E",
                alt="115m AGL",
                color="bg-purple-500",
                lat=26.4789,
                lon=80.3678
            ),
            Waypoint(
                id="wp3",
                label="Waypoint: Harbor Zone",
                coords="26.5234° N, 80.4123° E",
                alt="130m AGL",
                color="bg-purple-500",
                lat=26.5234,
                lon=80.4123
            ),
            Waypoint(
                id="end",
                label="End: Coastal Station",
                coords="26.3456° N, 80.1234° E",
                alt="100m AGL",
                color="bg-red-500",
                lat=26.3456,
                lon=80.1234
            )
        ],
        "created_by": "admin",
        "notes": "Extended coastal patrol with harbor monitoring",
        "vehicle_id": "UAV-004",
        "operator_id": "OP-104"
    },
    {
        "mission_name": "Central Region Survey Echo",
        "mission_type": "Domestic Operations",
        "corridor": Corridor(
            value="central",
            label="Central Regional Corridor",
            color="yellow",
            description="Domestic operations zone"
        ),
        "mission_stats": MissionStats(
            total_distance=42.1,
            flight_time=22.8,
            battery_usage=57.0
        ),
        "waypoints": [
            Waypoint(
                id="start",
                label="Start: City Center Station",
                coords="26.8467° N, 80.9462° E",
                alt="80m AGL",
                color="bg-green-500",
                lat=26.8467,
                lon=80.9462
            ),
            Waypoint(
                id="wp1",
                label="North District",
                coords="26.8789° N, 80.9678° E",
                alt="90m AGL",
                color="bg-yellow-500",
                lat=26.8789,
                lon=80.9678
            ),
            Waypoint(
                id="wp2",
                label="East District",
                coords="26.8654° N, 80.9891° E",
                alt="85m AGL",
                color="bg-yellow-500",
                lat=26.8654,
                lon=80.9891
            ),
            Waypoint(
                id="wp3",
                label="South District",
                coords="26.8234° N, 80.9567° E",
                alt="88m AGL",
                color="bg-yellow-500",
                lat=26.8234,
                lon=80.9567
            ),
            Waypoint(
                id="end",
                label="End: City Center Station",
                coords="26.8467° N, 80.9462° E",
                alt="80m AGL",
                color="bg-red-500",
                lat=26.8467,
                lon=80.9462
            )
        ],
        "created_by": "admin",
        "notes": "Central region surveillance covering all major districts",
        "vehicle_id": "UAV-005",
        "operator_id": "OP-105"
    }
]

def load_default_missions():
    """Load default missions into the database"""
    print("=" * 70)
    print("  Loading Default Missions - Jarbits Mission Management System")
    print("=" * 70)
    
    # Initialize database first
    print("\n📊 Initializing database...")
    try:
        init_db()
        print("✅ Database initialized")
    except Exception as e:
        print(f"⚠️  Database already initialized: {e}")
    
    # Get database session
    db = SessionLocal()
    
    try:
        loaded_count = 0
        skipped_count = 0
        
        print(f"\n🚀 Loading {len(DEFAULT_MISSIONS)} default missions...\n")
        
        for i, mission_data in enumerate(DEFAULT_MISSIONS, 1):
            mission_name = mission_data["mission_name"]
            
            try:
                # Check if mission already exists
                from db_models import MissionDB
                existing = db.query(MissionDB).filter(
                    MissionDB.mission_name == mission_name
                ).first()
                
                if existing:
                    print(f"⏭️  [{i}] Skipped: '{mission_name}' (already exists)")
                    skipped_count += 1
                    continue
                
                # Create mission
                mission_create = MissionCreate(**mission_data)
                created_mission = create_mission(db, mission_create)
                
                print(f"✅ [{i}] Loaded: '{mission_name}'")
                print(f"    • ID: {created_mission.id}")
                print(f"    • Type: {created_mission.mission_type}")
                print(f"    • Corridor: {created_mission.corridor_value} ({created_mission.corridor_label})")
                print(f"    • Distance: {created_mission.total_distance} km")
                print(f"    • Waypoints: {len(created_mission.waypoints)}")
                print(f"    • Vehicle: {created_mission.vehicle_id}")
                
                loaded_count += 1
                
            except Exception as e:
                print(f"❌ [{i}] Error loading '{mission_name}': {e}")
                continue
        
        # Summary
        print("\n" + "=" * 70)
        print("  Summary")
        print("=" * 70)
        print(f"✅ Successfully loaded: {loaded_count} missions")
        print(f"⏭️  Skipped (existing): {skipped_count} missions")
        print(f"📊 Total in database: {loaded_count + skipped_count} missions")
        
        if loaded_count > 0:
            print("\n🎉 Default missions loaded successfully!")
            print("\n📋 Missions by Corridor:")
            print("  • Northern Border (blue): Northern Border Patrol Alpha")
            print("  • Western Border (orange): Western Border Recon Bravo")
            print("  • Eastern Border (green): Eastern Surveillance Charlie")
            print("  • Southern Coastal (purple): Coastal Monitoring Delta")
            print("  • Central Regional (yellow): Central Region Survey Echo")
            print("\nNext steps:")
            print("  1. Start your API: uvicorn main:app --reload")
            print("  2. Visit: http://localhost:8000/api/missions")
            print("  3. Or check: http://localhost:8000/docs")
        
        print("=" * 70 + "\n")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    import sys
    import os
    
    # Check if we're in the right directory
    if not os.path.exists('database.py'):
        print("\n❌ Error: database.py not found")
        print("Please run this script from your project directory:")
        print("  cd /home/digambar/shahaji/jgcs/api")
        print("  python load_default_missions.py")
        sys.exit(1)
    
    load_default_missions()