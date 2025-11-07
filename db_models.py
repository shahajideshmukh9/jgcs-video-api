# db_models.py
# SQLAlchemy Database Models

from sqlalchemy import Column, Integer, String, Float, Text, DateTime, JSON
from sqlalchemy.sql import func
from database import Base

class MissionDB(Base):
    """
    SQLAlchemy model for missions table
    """
    __tablename__ = "missions"

    # Primary key
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    
    # Mission basic information
    mission_name = Column(String(255), nullable=False, index=True)
    mission_type = Column(String(100), nullable=True)
    
    # Corridor information
    corridor_value = Column(String(100), nullable=True, index=True)
    corridor_label = Column(String(255), nullable=True)
    corridor_color = Column(String(50), nullable=True)
    corridor_description = Column(Text, nullable=True)
    
    # Mission statistics
    total_distance = Column(Float, nullable=True, default=0.0)
    flight_time = Column(Float, nullable=True, default=0.0)
    battery_usage = Column(Float, nullable=True, default=0.0)
    
    # Waypoints stored as JSON
    waypoints = Column(JSON, nullable=False)
    
    # Mission metadata
    status = Column(String(50), nullable=False, default='draft', index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by = Column(String(255), nullable=True)
    
    # Additional fields
    notes = Column(Text, nullable=True)
    vehicle_id = Column(String(100), nullable=True)
    operator_id = Column(String(100), nullable=True)

    def __repr__(self):
        return f"<Mission(id={self.id}, name='{self.mission_name}', status='{self.status}')>"

    def to_dict(self):
        """Convert model to dictionary"""
        return {
            "id": self.id,
            "mission_name": self.mission_name,
            "mission_type": self.mission_type,
            "corridor_value": self.corridor_value,
            "corridor_label": self.corridor_label,
            "corridor_color": self.corridor_color,
            "corridor_description": self.corridor_description,
            "total_distance": self.total_distance,
            "flight_time": self.flight_time,
            "battery_usage": self.battery_usage,
            "waypoints": self.waypoints,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "created_by": self.created_by,
            "notes": self.notes,
            "vehicle_id": self.vehicle_id,
            "operator_id": self.operator_id
        }