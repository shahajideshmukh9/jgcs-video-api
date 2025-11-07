# models.py
# Pydantic Models for Request/Response Validation

from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any
from datetime import datetime

# ==================== WAYPOINT MODELS ====================

class Waypoint(BaseModel):
    """Waypoint model"""
    id: str = Field(..., description="Waypoint ID (e.g., 'start', 'stop1', 'end')")
    label: str = Field(..., description="Waypoint label (e.g., 'Start: Mohanlalganj')")
    coords: str = Field(..., description="Coordinates (e.g., '26.7465° N, 80.8769° E')")
    alt: str = Field(..., description="Altitude (e.g., '120m AGL')")
    color: str = Field(..., description="Color class (e.g., 'bg-green-500')")
    lat: float = Field(..., description="Latitude")
    lon: float = Field(..., description="Longitude")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "start",
                "label": "Start: Mohanlalganj",
                "coords": "26.7465° N, 80.8769° E",
                "alt": "120m AGL",
                "color": "bg-green-500",
                "lat": 26.7465,
                "lon": 80.8769
            }
        }

# ==================== CORRIDOR MODELS ====================

class Corridor(BaseModel):
    """Corridor information model"""
    value: str = Field(..., description="Corridor value identifier")
    label: str = Field(..., description="Corridor display label")
    color: str = Field(..., description="Corridor color")
    description: str = Field(..., description="Corridor description")

    class Config:
        json_schema_extra = {
            "example": {
                "value": "northern",
                "label": "Northern Border Corridor",
                "color": "blue",
                "description": "India-Nepal border surveillance"
            }
        }

# ==================== MISSION STATS MODELS ====================

class MissionStats(BaseModel):
    """Mission statistics model"""
    total_distance: float = Field(0.0, ge=0, description="Total distance in km")
    flight_time: float = Field(0.0, ge=0, description="Flight time in minutes")
    battery_usage: float = Field(0.0, ge=0, le=100, description="Battery usage percentage")

    class Config:
        json_schema_extra = {
            "example": {
                "total_distance": 87.65,
                "flight_time": 35.1,
                "battery_usage": 87.65
            }
        }

# ==================== MISSION MODELS ====================

class MissionBase(BaseModel):
    """Base mission model with common fields"""
    mission_name: Optional[str] = Field(None, max_length=255, description="Mission name")
    mission_type: Optional[str] = Field(None, max_length=100, description="Mission type")
    corridor: Optional[Corridor] = Field(None, description="Corridor information")
    mission_stats: Optional[MissionStats] = Field(None, description="Mission statistics")
    waypoints: Optional[List[Waypoint]] = Field(None, description="List of waypoints")
    created_by: Optional[str] = Field(None, max_length=255, description="Creator username")
    notes: Optional[str] = Field(None, description="Additional notes")
    vehicle_id: Optional[str] = Field(None, max_length=100, description="Assigned vehicle ID")
    operator_id: Optional[str] = Field(None, max_length=100, description="Assigned operator ID")
    status: Optional[str] = Field(None, max_length=50, description="Mission status")

class MissionCreate(BaseModel):
    """Model for creating a new mission"""
    mission_name: str = Field(..., min_length=1, max_length=255, description="Mission name (required)")
    mission_type: Optional[str] = Field(None, max_length=100, description="Mission type")
    corridor: Optional[Corridor] = Field(None, description="Corridor information")
    mission_stats: Optional[MissionStats] = Field(None, description="Mission statistics")
    waypoints: List[Waypoint] = Field(..., min_items=1, description="List of waypoints (required, min 1)")
    created_by: Optional[str] = Field(None, max_length=255, description="Creator username")
    notes: Optional[str] = Field(None, description="Additional notes")
    vehicle_id: Optional[str] = Field(None, max_length=100, description="Assigned vehicle ID")
    operator_id: Optional[str] = Field(None, max_length=100, description="Assigned operator ID")

    @validator('waypoints')
    def validate_waypoints(cls, v):
        if not v or len(v) == 0:
            raise ValueError('At least one waypoint is required')
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "mission_name": "Border Patrol Alpha",
                "mission_type": "Route Planning",
                "corridor": {
                    "value": "northern",
                    "label": "Northern Border Corridor",
                    "color": "blue",
                    "description": "India-Nepal border surveillance"
                },
                "mission_stats": {
                    "total_distance": 87.65,
                    "flight_time": 35.1,
                    "battery_usage": 87.65
                },
                "waypoints": [
                    {
                        "id": "start",
                        "label": "Start: Mohanlalganj",
                        "coords": "26.7465° N, 80.8769° E",
                        "alt": "120m AGL",
                        "color": "bg-green-500",
                        "lat": 26.7465,
                        "lon": 80.8769
                    }
                ],
                "created_by": "john_doe",
                "notes": "Standard patrol route",
                "vehicle_id": "UAV-X1-Alpha",
                "operator_id": "OP-001"
            }
        }

class MissionUpdate(BaseModel):
    """Model for updating an existing mission (all fields optional)"""
    mission_name: Optional[str] = Field(None, min_length=1, max_length=255)
    mission_type: Optional[str] = Field(None, max_length=100)
    corridor: Optional[Corridor] = None
    mission_stats: Optional[MissionStats] = None
    waypoints: Optional[List[Waypoint]] = None
    status: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None
    vehicle_id: Optional[str] = Field(None, max_length=100)
    operator_id: Optional[str] = Field(None, max_length=100)

    class Config:
        json_schema_extra = {
            "example": {
                "status": "active",
                "vehicle_id": "UAV-C2-Beta",
                "notes": "Updated mission notes"
            }
        }

class MissionResponse(BaseModel):
    """Model for mission response"""
    id: int
    mission_name: str
    mission_type: Optional[str]
    corridor_value: Optional[str]
    corridor_label: Optional[str]
    corridor_color: Optional[str]
    corridor_description: Optional[str]
    total_distance: Optional[float]
    flight_time: Optional[float]
    battery_usage: Optional[float]
    waypoints: List[Dict[str, Any]]
    status: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str]
    notes: Optional[str]
    vehicle_id: Optional[str]
    operator_id: Optional[str]

    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "id": 1,
                "mission_name": "Border Patrol Alpha",
                "mission_type": "Route Planning",
                "corridor_value": "northern",
                "corridor_label": "Northern Border Corridor",
                "corridor_color": "blue",
                "corridor_description": "India-Nepal border surveillance",
                "total_distance": 87.65,
                "flight_time": 35.1,
                "battery_usage": 87.65,
                "waypoints": [],
                "status": "draft",
                "created_at": "2025-11-07T10:30:00",
                "updated_at": "2025-11-07T10:30:00",
                "created_by": "john_doe",
                "notes": "Standard patrol route",
                "vehicle_id": "UAV-X1-Alpha",
                "operator_id": "OP-001"
            }
        }

# ==================== PAGINATION MODELS ====================

class PaginationInfo(BaseModel):
    """Pagination information model"""
    total: int = Field(..., description="Total number of items")
    limit: int = Field(..., description="Number of items per page")
    offset: int = Field(..., description="Number of items skipped")
    has_more: bool = Field(..., description="Whether more items exist")

class PaginatedMissionResponse(BaseModel):
    """Model for paginated mission response"""
    success: bool = True
    missions: List[MissionResponse]
    pagination: PaginationInfo

    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "missions": [],
                "pagination": {
                    "total": 127,
                    "limit": 50,
                    "offset": 0,
                    "has_more": True
                }
            }
        }

# ==================== OTHER MODELS ====================

class Mission(MissionResponse):
    """Complete mission model (alias for MissionResponse)"""
    pass

class SuccessResponse(BaseModel):
    """Generic success response"""
    success: bool = True
    message: str

class ErrorResponse(BaseModel):
    """Generic error response"""
    detail: str