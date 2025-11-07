# main.py
# FastAPI Main Application - Mission Management System

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from typing import List, Optional
from datetime import datetime
import uvicorn

from models import Mission, MissionCreate, MissionUpdate, MissionResponse, PaginatedMissionResponse
from database import get_db, init_db
from sqlalchemy.orm import Session
import crud

# Initialize FastAPI app
app = FastAPI(
    title="Jarbits Mission Management API",
    description="REST API for UAV mission planning and management",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS Configuration - Allow Next.js frontend to access API
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        # Add your production URLs here
        # "https://yourdomain.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup event - Initialize database
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database tables on startup"""
    init_db()
    print("✅ Database initialized successfully")
    yield
    # Shutdown code here
    # your shutdown logic

# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint - API health check"""
    return {
        "message": "Jarbits Mission Management API",
        "version": "1.0.0",
        "status": "running",
        "timestamp": datetime.utcnow().isoformat()
    }

# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }

# ==================== MISSION ENDPOINTS ====================

@app.post("/api/missions", response_model=MissionResponse, status_code=201, tags=["Missions"])
async def create_mission(
    mission: MissionCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new mission
    
    - **mission_name**: Name of the mission (required)
    - **mission_type**: Type of mission (optional)
    - **corridor**: Corridor information (optional)
    - **mission_stats**: Mission statistics (distance, time, battery)
    - **waypoints**: List of waypoints (required)
    - **created_by**: Username of creator (optional)
    - **notes**: Additional notes (optional)
    - **vehicle_id**: Assigned vehicle ID (optional)
    - **operator_id**: Assigned operator ID (optional)
    """
    try:
        db_mission = crud.create_mission(db, mission)
        return db_mission
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create mission: {str(e)}")

@app.get("/api/missions", response_model=PaginatedMissionResponse, tags=["Missions"])
async def get_missions(
    status: Optional[str] = Query(None, description="Filter by mission status"),
    corridor: Optional[str] = Query(None, description="Filter by corridor value"),
    mission_type: Optional[str] = Query(None, description="Filter by mission type"),
    created_by: Optional[str] = Query(None, description="Filter by creator"),
    limit: int = Query(50, ge=1, le=100, description="Number of results to return"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    db: Session = Depends(get_db)
):
    """
    Retrieve all missions with optional filters and pagination
    
    Query Parameters:
    - **status**: Filter by status (draft/active/completed/cancelled)
    - **corridor**: Filter by corridor value (northern/western/eastern/southern/central)
    - **mission_type**: Filter by mission type
    - **created_by**: Filter by creator username
    - **limit**: Maximum number of results (1-100, default: 50)
    - **offset**: Number of results to skip (default: 0)
    """
    try:
        filters = {
            "status": status,
            "corridor_value": corridor,
            "mission_type": mission_type,
            "created_by": created_by
        }
        
        missions = crud.get_missions(db, filters, limit, offset)
        total_count = crud.get_missions_count(db, filters)
        
        return {
            "success": True,
            "missions": missions,
            "pagination": {
                "total": total_count,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total_count
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve missions: {str(e)}")

@app.get("/api/missions/{mission_id}", response_model=MissionResponse, tags=["Missions"])
async def get_mission(
    mission_id: int,
    db: Session = Depends(get_db)
):
    """
    Retrieve a single mission by ID
    
    - **mission_id**: The ID of the mission to retrieve
    """
    db_mission = crud.get_mission(db, mission_id)
    if db_mission is None:
        raise HTTPException(status_code=404, detail="Mission not found")
    return db_mission

@app.put("/api/missions/{mission_id}", response_model=MissionResponse, tags=["Missions"])
async def update_mission(
    mission_id: int,
    mission_update: MissionUpdate,
    db: Session = Depends(get_db)
):
    """
    Update an existing mission
    
    - **mission_id**: The ID of the mission to update
    - All fields are optional - only provided fields will be updated
    """
    try:
        db_mission = crud.update_mission(db, mission_id, mission_update)
        if db_mission is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        return db_mission
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update mission: {str(e)}")

@app.delete("/api/missions/{mission_id}", tags=["Missions"])
async def delete_mission(
    mission_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete a mission by ID
    
    - **mission_id**: The ID of the mission to delete
    """
    success = crud.delete_mission(db, mission_id)
    if not success:
        raise HTTPException(status_code=404, detail="Mission not found")
    
    return {
        "success": True,
        "message": "Mission deleted successfully",
        "mission_id": mission_id
    }

# ==================== STATISTICS ENDPOINTS ====================

@app.get("/api/missions/stats/summary", tags=["Statistics"])
async def get_mission_statistics(
    db: Session = Depends(get_db)
):
    """
    Get overall mission statistics
    
    Returns counts by status, corridor, and other aggregated data
    """
    try:
        stats = crud.get_mission_statistics(db)
        return {
            "success": True,
            "statistics": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve statistics: {str(e)}")

@app.get("/api/missions/stats/by-status", tags=["Statistics"])
async def get_missions_by_status(
    db: Session = Depends(get_db)
):
    """
    Get mission counts grouped by status
    """
    try:
        stats = crud.get_missions_by_status(db)
        return {
            "success": True,
            "statistics": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve status statistics: {str(e)}")

@app.get("/api/missions/stats/by-corridor", tags=["Statistics"])
async def get_missions_by_corridor(
    db: Session = Depends(get_db)
):
    """
    Get mission counts grouped by corridor
    """
    try:
        stats = crud.get_missions_by_corridor(db)
        return {
            "success": True,
            "statistics": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to retrieve corridor statistics: {str(e)}")

# ==================== SEARCH ENDPOINTS ====================

@app.get("/api/missions/search", response_model=List[MissionResponse], tags=["Search"])
async def search_missions(
    q: str = Query(..., min_length=1, description="Search query"),
    db: Session = Depends(get_db)
):
    """
    Search missions by name or notes
    
    - **q**: Search query string (searches in mission_name and notes)
    """
    try:
        missions = crud.search_missions(db, q)
        return missions
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to search missions: {str(e)}")

# Run the application
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,  # Enable auto-reload for development
        log_level="info"
    )