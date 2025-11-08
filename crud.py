# crud.py
# CRUD Operations for Mission Management

from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from typing import List, Dict, Optional
from db_models import MissionDB
from models import MissionCreate, MissionUpdate
import json

# ==================== CREATE OPERATIONS ====================

def create_mission(db: Session, mission: MissionCreate) -> MissionDB:
    """
    Create a new mission in the database
    
    Args:
        db: Database session
        mission: Mission data to create
        
    Returns:
        Created mission object
        
    Raises:
        ValueError: If validation fails
    """
    # Validate required fields
    if not mission.mission_name or not mission.mission_name.strip():
        raise ValueError("Mission name is required")
    
    if not mission.waypoints or len(mission.waypoints) == 0:
        raise ValueError("At least one waypoint is required")
    
    # Convert waypoints to JSON-serializable format
    waypoints_data = [waypoint.dict() for waypoint in mission.waypoints]
    
    # Extract corridor information
    corridor_value = None
    corridor_label = None
    corridor_color = None
    corridor_description = None
    
    if mission.corridor:
        corridor_value = mission.corridor.value
        corridor_label = mission.corridor.label
        corridor_color = mission.corridor.color
        corridor_description = mission.corridor.description
    
    # Extract mission stats
    total_distance = 0.0
    flight_time = 0.0
    battery_usage = 0.0
    
    if mission.mission_stats:
        total_distance = mission.mission_stats.total_distance
        flight_time = mission.mission_stats.flight_time
        battery_usage = mission.mission_stats.battery_usage
    
    # Create database object
    db_mission = MissionDB(
        mission_name=mission.mission_name,
        mission_type=mission.mission_type,
        corridor_value=corridor_value,
        corridor_label=corridor_label,
        corridor_color=corridor_color,
        corridor_description=corridor_description,
        total_distance=total_distance,
        flight_time=flight_time,
        battery_usage=battery_usage,
        waypoints=waypoints_data,
        status='draft',  # Default status
        created_by=mission.created_by,
        notes=mission.notes,
        vehicle_id=mission.vehicle_id,
        operator_id=mission.operator_id
    )
    
    # Add to database
    db.add(db_mission)
    db.commit()
    db.refresh(db_mission)
    
    return db_mission

# ==================== READ OPERATIONS ====================

def get_mission(db: Session, mission_id: int) -> Optional[MissionDB]:
    """
    Get a single mission by ID
    
    Args:
        db: Database session
        mission_id: ID of the mission to retrieve
        
    Returns:
        Mission object or None if not found
    """
    return db.query(MissionDB).filter(MissionDB.id == mission_id).first()

def get_missions(
    db: Session,
    filters: Dict[str, Optional[str]],
    limit: int = 50,
    offset: int = 0
) -> List[MissionDB]:
    """
    Get missions with optional filters and pagination
    
    Args:
        db: Database session
        filters: Dictionary of filter criteria
        limit: Maximum number of results
        offset: Number of results to skip
        
    Returns:
        List of mission objects
    """
    query = db.query(MissionDB)
    
    # Apply filters
    if filters.get('status'):
        query = query.filter(MissionDB.status == filters['status'])
    
    if filters.get('corridor_value'):
        query = query.filter(MissionDB.corridor_value == filters['corridor_value'])
    
    if filters.get('mission_type'):
        query = query.filter(MissionDB.mission_type == filters['mission_type'])
    
    if filters.get('created_by'):
        query = query.filter(MissionDB.created_by == filters['created_by'])
    
    # Order by created_at descending (newest first)
    query = query.order_by(MissionDB.created_at.desc())
    
    # Apply pagination
    query = query.limit(limit).offset(offset)
    
    return query.all()

def get_missions_count(
    db: Session,
    filters: Dict[str, Optional[str]]
) -> int:
    """
    Get total count of missions matching filters
    
    Args:
        db: Database session
        filters: Dictionary of filter criteria
        
    Returns:
        Total count of missions
    """
    query = db.query(func.count(MissionDB.id))
    
    # Apply same filters as get_missions
    if filters.get('status'):
        query = query.filter(MissionDB.status == filters['status'])
    
    if filters.get('corridor_value'):
        query = query.filter(MissionDB.corridor_value == filters['corridor_value'])
    
    if filters.get('mission_type'):
        query = query.filter(MissionDB.mission_type == filters['mission_type'])
    
    if filters.get('created_by'):
        query = query.filter(MissionDB.created_by == filters['created_by'])
    
    return query.scalar()

def search_missions(db: Session, search_query: str) -> List[MissionDB]:
    """
    Search missions by name or notes
    
    Args:
        db: Database session
        search_query: Search string
        
    Returns:
        List of matching missions
    """
    search_pattern = f"%{search_query}%"
    
    query = db.query(MissionDB).filter(
        or_(
            MissionDB.mission_name.ilike(search_pattern),
            MissionDB.notes.ilike(search_pattern)
        )
    ).order_by(MissionDB.created_at.desc())
    
    return query.all()

# ==================== UPDATE OPERATIONS ====================

def update_mission(
    db: Session,
    mission_id: int,
    mission_update: MissionUpdate
) -> Optional[MissionDB]:
    """
    Update an existing mission
    
    Args:
        db: Database session
        mission_id: ID of the mission to update
        mission_update: Updated mission data
        
    Returns:
        Updated mission object or None if not found
        
    Raises:
        ValueError: If validation fails
    """
    db_mission = get_mission(db, mission_id)
    
    if db_mission is None:
        return None
    
    # Update fields if provided
    update_data = mission_update.dict(exclude_unset=True)
    
    # Handle corridor update
    if 'corridor' in update_data and update_data['corridor']:
        corridor = update_data.pop('corridor')
        db_mission.corridor_value = corridor.get('value')
        db_mission.corridor_label = corridor.get('label')
        db_mission.corridor_color = corridor.get('color')
        db_mission.corridor_description = corridor.get('description')
    
    # Handle mission_stats update
    if 'mission_stats' in update_data and update_data['mission_stats']:
        stats = update_data.pop('mission_stats')
        db_mission.total_distance = stats.get('total_distance', db_mission.total_distance)
        db_mission.flight_time = stats.get('flight_time', db_mission.flight_time)
        db_mission.battery_usage = stats.get('battery_usage', db_mission.battery_usage)
    
    # Handle waypoints update
    if 'waypoints' in update_data and update_data['waypoints']:
        waypoints = update_data.pop('waypoints')
        if isinstance(waypoints, list) and len(waypoints) > 0:
            # Convert Pydantic models to dicts
            waypoints_data = [w.dict() if hasattr(w, 'dict') else w for w in waypoints]
            db_mission.waypoints = waypoints_data
        else:
            raise ValueError("At least one waypoint is required")
    
    # Update remaining fields
    for key, value in update_data.items():
        if hasattr(db_mission, key) and value is not None:
            setattr(db_mission, key, value)
    
    db.commit()
    db.refresh(db_mission)
    
    return db_mission

# ==================== DELETE OPERATIONS ====================

def delete_mission(db: Session, mission_id: int) -> bool:
    """
    Delete a mission by ID
    
    Args:
        db: Database session
        mission_id: ID of the mission to delete
        
    Returns:
        True if deleted, False if not found
    """
    db_mission = get_mission(db, mission_id)
    
    if db_mission is None:
        return False
    
    db.delete(db_mission)
    db.commit()
    
    return True

# ==================== STATISTICS OPERATIONS ====================

def get_mission_statistics(db: Session) -> Dict:
    """
    Get overall mission statistics
    
    Args:
        db: Database session
        
    Returns:
        Dictionary with statistics
    """
    total_missions = db.query(func.count(MissionDB.id)).scalar()
    
    # Count by status
    status_counts = db.query(
        MissionDB.status,
        func.count(MissionDB.id)
    ).group_by(MissionDB.status).all()
    
    # Average distance and flight time
    avg_distance = db.query(func.avg(MissionDB.total_distance)).scalar() or 0
    avg_flight_time = db.query(func.avg(MissionDB.flight_time)).scalar() or 0
    
    # Total distance
    total_distance = db.query(func.sum(MissionDB.total_distance)).scalar() or 0
    
    return {
        "total_missions": total_missions,
        "by_status": {status: count for status, count in status_counts},
        "average_distance_km": round(avg_distance, 2),
        "average_flight_time_min": round(avg_flight_time, 2),
        "total_distance_km": round(total_distance, 2)
    }

def get_missions_by_status(db: Session) -> Dict:
    """
    Get mission counts grouped by status
    
    Args:
        db: Database session
        
    Returns:
        Dictionary with status counts
    """
    status_counts = db.query(
        MissionDB.status,
        func.count(MissionDB.id)
    ).group_by(MissionDB.status).all()
    
    return {status: count for status, count in status_counts}

def get_missions_by_corridor(db: Session) -> Dict:
    """
    Get mission counts grouped by corridor
    
    Args:
        db: Database session
        
    Returns:
        Dictionary with corridor counts
    """
    corridor_counts = db.query(
        MissionDB.corridor_label,
        func.count(MissionDB.id)
    ).filter(
        MissionDB.corridor_label.isnot(None)
    ).group_by(MissionDB.corridor_label).all()
    
    return {corridor or "No Corridor": count for corridor, count in corridor_counts}

def update_mission_status( db: Session, mission_id: int, new_status: str) -> Optional[MissionDB]:
    """
    Update mission status with timestamp tracking
    
    Args:
        db: Database session
        mission_id: ID of the mission to update
        new_status: New status ('active', 'paused', 'completed', etc.)
        
    Returns:
        Updated mission object or None if not found
        
    Raises:
        ValueError: If status transition is invalid
    """
    db_mission = db.query(MissionDB).filter(MissionDB.id == mission_id).first()
    
    if db_mission is None:
        return None
    
    # Validate status
    valid_statuses = ['draft', 'pending', 'active', 'in_progress', 'paused', 
                    'completed', 'failed', 'cancelled']
    if new_status.lower() not in valid_statuses:
        raise ValueError(
            f"Invalid status '{new_status}'. Must be one of: {', '.join(valid_statuses)}"
        )
    
    # Check if status transition is valid
    current_status = db_mission.status.lower() if db_mission.status else 'draft'
    
    # Define valid transitions
    valid_transitions: Dict[str, list] = {
        'draft': ['pending', 'cancelled'],
        'pending': ['active', 'in_progress', 'cancelled'],
        'active': ['paused', 'completed', 'failed'],
        'in_progress': ['paused', 'completed', 'failed'],
        'paused': ['active', 'in_progress', 'cancelled', 'failed'],
        'completed': [],  # Cannot change from completed
        'failed': [],     # Cannot change from failed
        'cancelled': []   # Cannot change from cancelled
    }
    
    if new_status.lower() not in valid_transitions.get(current_status, []):
        raise ValueError(
            f"Cannot transition from '{current_status}' to '{new_status}'"
        )
    
    # Update status
    old_status = db_mission.status
    db_mission.status = new_status
    db_mission.updated_at = datetime.utcnow()
    
    # Update timestamps based on new status
    if new_status.lower() in ['active', 'in_progress']:
        # Starting mission
        if not db_mission.started_at:
            db_mission.started_at = datetime.utcnow()
    
    elif new_status.lower() == 'paused':
        # Pausing mission
        db_mission.paused_at = datetime.utcnow()
    
    elif new_status.lower() == 'completed':
        # Completing mission
        if not db_mission.completed_at:
            db_mission.completed_at = datetime.utcnow()
    
    # Commit changes
    db.commit()
    db.refresh(db_mission)
    
    print(f"✅ Mission {mission_id} status: {old_status} → {new_status}")
    
    return db_mission