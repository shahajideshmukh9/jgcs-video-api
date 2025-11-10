#!/usr/bin/env python3

# ═══════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════════════════════════

from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import List, Optional, Dict, Any, Tuple, Union
from contextlib import asynccontextmanager
from enum import IntEnum, Enum
from collections import deque
from datetime import datetime, timedelta
from dataclasses import dataclass
import logging
import asyncio
import math
import json
import uuid
import time
import threading
import redis

from pymavlink import mavutil


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGING CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Redis connection
try:
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    redis_client.ping()
    logger.info("✓ Redis connected")
except Exception as e:
    logger.error(f"✗ Redis connection failed: {e}")
    redis_client = None

# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def sanitize_float_values(data: Any) -> Any:
    """
    Recursively sanitize float values in data structures, converting NaN and Inf to None.
    This prevents JSON serialization errors.
    
    Args:
        data: Input data (dict, list, or primitive)
        
    Returns:
        Sanitized data with NaN/Inf replaced by None
    """
    if isinstance(data, dict):
        return {k: sanitize_float_values(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_float_values(item) for item in data]
    elif isinstancsanitize_float_valuese(data, float):
        if math.isnan(data) or math.isinf(data):
            return None
        return data
    return data

# ==================== Models ====================

class ClientType(str, Enum):
    QT_DESKTOP = "qt_desktop"
    QGC = "qgc"
    MOBILE = "mobile"
    WEB = "web"

class ConnectionRequest(BaseModel):
    """Request model for client connection"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "client_type": "qt_desktop",
                "client_version": "1.0.0",
                "device_info": {
                    "os": "Linux",
                    "hostname": "field-station-01",
                    "ip": "192.168.1.100"
                }
            }
        }
    )
    
    client_id: Optional[str] = Field(None, description="Unique client identifier (generated if not provided)")
    client_type: ClientType = Field(..., description="Type of client connecting")
    client_version: str = Field(..., description="Client application version")
    device_info: Optional[Dict[str, Any]] = Field(None, description="Device information (OS, hardware, etc.)")

class ConnectionResponse(BaseModel):
    """Response model for successful connection"""
    success: bool
    client_id: str
    session_id: str
    connected_at: datetime
    server_version: str
    server_time: datetime
    heartbeat_interval: int = Field(30, description="Seconds between heartbeats")
    message: str


class HeartbeatRequest(BaseModel):
    """Request model for heartbeat/ping"""
    client_id: str
    session_id: str


class HeartbeatResponse(BaseModel):
    """Response model for heartbeat"""
    success: bool
    server_time: datetime
    session_valid: bool
    message: str


class DisconnectRequest(BaseModel):
    """Request model for disconnection"""
    client_id: str
    session_id: str
    reason: Optional[str] = Field(None, description="Reason for disconnection")


class DisconDisconnectResponsenectResponse(BaseModel):
    """Response model for disconnection"""
    success: bool
    message: str
    disconnected_at: datetime


class ConnectionStatus(BaseModel):
    """Connection status information"""
    connected: bool
    client_id: Optional[str] = None
    session_id: Optional[str] = None
    connected_at: Optional[datetime] = None
    last_heartbeat: Optional[datetime] = None
    uptime_seconds: Optional[int] = None

# ==================== In-Memory Session Store ====================
# In production, use Redis or similar for distributed sessions

class SessionManager:
    """Manages active client sessions"""
    
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
    
    def create_session(self, client_id: str, connection_data: ConnectionRequest) -> str:
        """Create a new session for a client"""
        session_id = str(uuid.uuid4())
        
        self.sessions[session_id] = {
            "client_id": client_id,
            "client_type": connection_data.client_type,
            "client_version": connection_data.client_version,
            "device_info": connection_data.device_info,
            "connected_at": datetime.utcnow(),
            "last_heartbeat": datetime.utcnow(),
            "is_active": True
        }
        
        logger.info(f"Created session {session_id} for client {client_id}")
        return session_id
    
    def validate_session(self, client_id: str, session_id: str) -> bool:
        """Validate if session is active and belongs to client"""
        session = self.sessions.get(session_id)
        
        if not session:
            return False
        
        if session["client_id"] != client_id:
            return False
        
        if not session["is_active"]:
            return False
        
        # Check if session has timed out (no heartbeat for 5 minutes)
        if datetime.utcnow() - session["last_heartbeat"] > timedelta(minutes=5):
            session["is_active"] = False
            logger.warning(f"Session {session_id} timed out")
            return False
        
        return True
    
    def update_heartbeat(self, session_id: str):
        """Update last heartbeat time for session"""
        if session_id in self.sessions:
            self.sessions[session_id]["last_heartbeat"] = datetime.utcnow()
    
    def end_session(self, session_id: str):
        """End a session"""
        if session_id in self.sessions:
            self.sessions[session_id]["is_active"] = False
            self.sessions[session_id]["disconnected_at"] = datetime.utcnow()
            logger.info(f"Ended session {session_id}")
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data"""
        return self.sessions.get(session_id)
    
    def cleanup_inactive_sessions(self):
        """Remove inactive sessions older than 1 hour"""
        current_time = datetime.utcnow()
        sessions_to_remove = []
        
        for session_id, session in self.sessions.items():
            if not session["is_active"]:
                if "disconnected_at" in session:
                    if current_time - session["disconnected_at"] > timedelta(hours=1):
                        sessions_to_remove.append(session_id)
        
        for session_id in sessions_to_remove:
            del self.sessions[session_id]
            logger.info(f"Cleaned up session {session_id}")


# Global session manager instance
session_manager = SessionManager()

# ═══════════════════════════════════════════════════════════════════════════════
# GAZEBO SIMULATOR CONTROLLER CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class SimulatorType(Enum):
    """Supported simulator types"""
    PX4_GAZEBO = "px4_gazebo"
    ARDUPILOT_GAZEBO = "ardupilot_gazebo"


@dataclass
class SimulatorConfig:
    """Configuration for Gazebo simulator connection"""
    connection_url: str = "udp:127.0.0.1:14540"  # Default PX4 SITL
    vehicle_model: str = "iris"
    simulator_type: SimulatorType = SimulatorType.PX4_GAZEBO
    timeout_seconds: int = 60
    source_system: int = 255  # GCS system ID
    source_component: int = 0  # GCS component ID


class GazeboSimulatorController:
    """
    Fixed controller with proper PX4 SITL mode handling
    """
    
    def __init__(self, config):
        self.config = config
        self.mav_connection = None
        self.logger = logging.getLogger(__name__)
        self._is_connected = False
        self._mission_active = False
        self.target_system = 1
        self.target_component = 1
        
        # Telemetry cache
        self._position = None
        self._attitude = None
        self._velocity = None
        self._battery = None
        self._gps = None
        self._heartbeat = None
        self._mission_current = 0
        self._mission_count = 0
        
        # OFFBOARD mode support
        self._offboard_thread = None
        self._offboard_active = False
        self._target_position = None
    
    def connect(self) -> bool:
        """Connect to Gazebo SITL simulator"""
        try:
            self.logger.info(f"Connecting to simulator at {self.config.connection_url}")
            
            self.mav_connection = mavutil.mavlink_connection(
                self.config.connection_url,
                source_system=self.config.source_system,
                source_component=self.config.source_component
            )
            
            self.logger.info("Waiting for heartbeat...")
            heartbeat = self.mav_connection.wait_heartbeat(timeout=self.config.timeout_seconds)
            
            if heartbeat:
                self.target_system = self.mav_connection.target_system
                self.target_component = self.mav_connection.target_component
                self._is_connected = True
                self.logger.info(f"✓ Connected to system {self.target_system}")
                
                # Start GCS heartbeat
                self._start_gcs_heartbeat()
                
                return True
            else:
                self.logger.error("No heartbeat received")
                return False
                
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self._is_connected = False
            return False
    
    def _start_gcs_heartbeat(self):
        """Start sending GCS heartbeats"""
        def send_heartbeat():
            while self._is_connected:
                try:
                    self.mav_connection.mav.heartbeat_send(
                        mavutil.mavlink.MAV_TYPE_GCS,
                        mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                        0, 0, 0
                    )
                except Exception as e:
                    self.logger.debug(f"Heartbeat error: {e}")
                time.sleep(1)
        
        heartbeat_thread = threading.Thread(target=send_heartbeat, daemon=True)
        heartbeat_thread.start()
        self.logger.info("✓ GCS heartbeats started")
    
    # ========================================================================
    # IMPROVED ARM/DISARM/TAKEOFF METHODS
    # ========================================================================
    
    def set_mode_offboard(self) -> bool:
        """
        Set vehicle to OFFBOARD mode
        This is required before arming in PX4
        """
        try:
            self.logger.info("Setting mode to OFFBOARD...")
            
            # PX4 OFFBOARD mode: main=6, sub=0
            custom_mode = (6 << 16) | 0
            
            self.mav_connection.mav.set_mode_send(
                self.target_system,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                custom_mode
            )
            
            # Wait for mode change confirmation
            start_time = time.time()
            while (time.time() - start_time) < 5:
                msg = self.mav_connection.recv_match(
                    type='HEARTBEAT',
                    blocking=True,
                    timeout=1
                )
                
                if msg:
                    curr_mode = msg.custom_mode
                    main = (curr_mode >> 16) & 0xFF
                    
                    if main == 6:  # OFFBOARD
                        self.logger.info("✓ Mode set to OFFBOARD")
                        return True
            
            self.logger.warning("Mode change to OFFBOARD not confirmed")
            return False
            
        except Exception as e:
            self.logger.error(f"Failed to set OFFBOARD mode: {e}")
            return False
    
    def set_mode_posctl(self) -> bool:
        """
        Set vehicle to POSITION CONTROL mode
        Alternative to OFFBOARD for arming
        """
        try:
            self.logger.info("Setting mode to POSCTL...")
            
            # PX4 POSCTL mode: main=3, sub=0
            custom_mode = (3 << 16) | 0
            
            self.mav_connection.mav.set_mode_send(
                self.target_system,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                custom_mode
            )
            
            # Wait for confirmation
            start_time = time.time()
            while (time.time() - start_time) < 5:
                msg = self.mav_connection.recv_match(
                    type='HEARTBEAT',
                    blocking=True,
                    timeout=1
                )
                
                if msg:
                    curr_mode = msg.custom_mode
                    main = (curr_mode >> 16) & 0xFF
                    
                    if main == 3:  # POSCTL
                        self.logger.info("✓ Mode set to POSCTL")
                        return True
            
            self.logger.warning("Mode change to POSCTL not confirmed")
            return False
            
        except Exception as e:
            self.logger.error(f"Failed to set POSCTL mode: {e}")
            return False
    
    def _start_offboard_setpoints(self):
        """
        Start sending regular position setpoints for OFFBOARD mode
        PX4 requires setpoints at 2Hz minimum before arming
        """
        def send_setpoints():
            self.logger.info("Starting OFFBOARD setpoint stream...")
            
            while self._offboard_active:
                try:
                    # Get current position for setpoint
                    if self._position:
                        lat = self._position.lat
                        lon = self._position.lon
                        alt = self._position.relative_alt / 1000.0
                    else:
                        # Use home position or default
                        lat = 0
                        lon = 0
                        alt = 0
                    
                    # If we have a target position, use it
                    if self._target_position:
                        lat = int(self._target_position['lat'] * 1e7)
                        lon = int(self._target_position['lon'] * 1e7)
                        alt = self._target_position['alt']
                    
                    # Send position setpoint
                    self.mav_connection.mav.set_position_target_global_int_send(
                        0,  # time_boot_ms
                        self.target_system,
                        self.target_component,
                        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
                        0b0000111111111000,  # type_mask (position only)
                        lat,
                        lon,
                        alt,
                        0, 0, 0,  # vx, vy, vz
                        0, 0, 0,  # afx, afy, afz
                        0, 0  # yaw, yaw_rate
                    )
                    
                except Exception as e:
                    self.logger.debug(f"Setpoint send error: {e}")
                
                time.sleep(0.5)  # 2 Hz
            
            self.logger.info("Stopped OFFBOARD setpoint stream")
        
        self._offboard_active = True
        self._offboard_thread = threading.Thread(target=send_setpoints, daemon=True)
        self._offboard_thread.start()
    
    def _stop_offboard_setpoints(self):
        """Stop sending OFFBOARD setpoints"""
        self._offboard_active = False
        if self._offboard_thread:
            self._offboard_thread.join(timeout=2)
    
    def arm_vehicle(self, force: bool = False, use_offboard: bool = True) -> bool:
        """
        ARM VEHICLE - Fixed version with proper mode handling
        
        Args:
            force: Force arm even if pre-arm checks fail
            use_offboard: Use OFFBOARD mode (recommended), otherwise POSCTL
            
        Returns:
            True if armed successfully
        """
        if not self._is_connected:
            self.logger.error("Not connected")
            return False
        
        try:
            self.logger.info("=" * 60)
            self.logger.info("ARMING SEQUENCE STARTING")
            self.logger.info("=" * 60)
            
            # Step 1: Wait for position
            self.logger.info("Step 1: Checking GPS position...")
            if not self.wait_for_position(timeout=10):
                self.logger.error("❌ No GPS position available")
                if not force:
                    return False
            else:
                self.logger.info("✓ GPS position acquired")
            
            # Step 2: Set appropriate flight mode
            if use_offboard:
                self.logger.info("Step 2: Setting OFFBOARD mode...")
                if not self.set_mode_offboard():
                    self.logger.warning("⚠ Failed to set OFFBOARD mode")
                    if not force:
                        return False
                
                # Step 3: Start sending setpoints (required for OFFBOARD)
                self.logger.info("Step 3: Starting OFFBOARD setpoint stream...")
                self._start_offboard_setpoints()
                time.sleep(2)  # Let setpoints stabilize
            else:
                self.logger.info("Step 2: Setting POSCTL mode...")
                if not self.set_mode_posctl():
                    self.logger.warning("⚠ Failed to set POSCTL mode")
                    if not force:
                        return False
            
            # Step 4: Send ARM command
            self.logger.info("Step 4: Sending ARM command...")
            
            self.mav_connection.mav.command_long_send(
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0,  # confirmation
                1,  # arm
                21196 if force else 0,  # force parameter
                0, 0, 0, 0, 0
            )
            
            # Step 5: Wait for ARM acknowledgment
            self.logger.info("Step 5: Waiting for ARM confirmation...")
            
            start_time = time.time()
            while (time.time() - start_time) < 10:
                msg = self.mav_connection.recv_match(
                    type='COMMAND_ACK',
                    blocking=True,
                    timeout=1
                )
                
                if msg and msg.command == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
                    if msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                        self.logger.info("=" * 60)
                        self.logger.info("✅ VEHICLE ARMED SUCCESSFULLY")
                        self.logger.info("=" * 60)
                        return True
                    else:
                        result_names = {
                            0: "ACCEPTED",
                            1: "TEMPORARILY_REJECTED", 
                            2: "DENIED",
                            3: "UNSUPPORTED",
                            4: "FAILED",
                            5: "IN_PROGRESS"
                        }
                        result = result_names.get(msg.result, f"UNKNOWN({msg.result})")
                        self.logger.error(f"❌ ARM rejected: {result}")
                        
                        # Stop setpoints if failed
                        if use_offboard:
                            self._stop_offboard_setpoints()
                        
                        return False
            
            self.logger.error("❌ ARM timeout - no acknowledgment received")
            
            # Stop setpoints if failed
            if use_offboard:
                self._stop_offboard_setpoints()
            
            return False
            
        except Exception as e:
            self.logger.error(f"❌ ARM failed with exception: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            
            # Clean up on error
            if use_offboard:
                self._stop_offboard_setpoints()
            
            return False

    def disarm_vehicle(self, force=False, retry_count=3):
        """Disarm with acknowledgment"""
        
        for attempt in range(retry_count):
            self.logger.info(f"Disarming attempt {attempt + 1}/{retry_count}...")
            
            # Send DISARM command
            self.mav_connection.mav.command_long_send(
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                0,  # confirmation
                0,  # param1: 0 = disarm
                21196 if force else 0,  # param2: force disarm
                0, 0, 0, 0, 0
            )
            
            # *** ADD THIS: Wait for acknowledgment ***
            self.logger.info("Waiting for disarm acknowledgment...")
            success, result = self.wait_for_command_ack(
                mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
                timeout=5
            )
            
            if success:
                self.logger.info("✓ Vehicle disarmed successfully")
                return True
            else:
                if result == mavutil.mavlink.MAV_RESULT_DENIED:
                    self.logger.error("Disarm denied - vehicle may still be flying")
                    return False
                if attempt < retry_count - 1:
                    time.sleep(1)
        
        return False
    
    def wait_for_command_ack(self, command, timeout=5):
        """
        Wait for COMMAND_ACK message for a specific command
        
        THIS IS THE CRITICAL FIX - Add this method to your controller class
        
        Args:
            command: MAVLink command ID (e.g., mavutil.mavlink.MAV_CMD_NAV_TAKEOFF)
            timeout: Maximum time to wait in seconds
            
        Returns:
            tuple: (success: bool, ack_result: int or None)
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            msg = self.mav_connection.recv_match(
                type='COMMAND_ACK',
                blocking=True,
                timeout=0.5
            )
            
            if msg and msg.command == command:
                result = msg.result
                
                if result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                    self.logger.info(f"✓ Command {command} acknowledged (ACCEPTED)")
                    return True, result
                elif result == mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED:
                    self.logger.warning(f"⚠ Command {command} temporarily rejected")
                    return False, result
                elif result == mavutil.mavlink.MAV_RESULT_DENIED:
                    self.logger.error(f"❌ Command {command} denied")
                    return False, result
                elif result == mavutil.mavlink.MAV_RESULT_UNSUPPORTED:
                    self.logger.error(f"❌ Command {command} unsupported")
                    return False, result
                elif result == mavutil.mavlink.MAV_RESULT_FAILED:
                    self.logger.error(f"❌ Command {command} failed")
                    return False, result
                else:
                    self.logger.warning(f"⚠ Command {command} unknown result: {result}")
                    return False, result
        
        self.logger.warning(f"⏱ Timeout waiting for acknowledgment of command {command}")
        return False, None


    # ============================================================================
    # STEP 2: Update your existing takeoff() method
    # ============================================================================

    def takeoff(self, altitude: float) -> bool:
        """
        Execute takeoff with proper mode handling
        
        Args:
            altitude: Target altitude in meters
            
        Returns:
            True if takeoff successful
        """
        if not self._is_connected:
            self.logger.error("Not connected to vehicle")
            return False
        
        try:
            self.logger.info(f"=" * 60)
            self.logger.info(f"TAKEOFF SEQUENCE - Target: {altitude}m")
            self.logger.info(f"=" * 60)
            
            # Step 1: Stop OFFBOARD setpoints if active
            if self._offboard_active:
                self.logger.info("Step 1: Stopping OFFBOARD setpoints...")
                self._stop_offboard_setpoints()
                time.sleep(0.5)
            
            # Step 2: Switch to GUIDED mode (required for takeoff command)
            self.logger.info("Step 2: Switching to GUIDED mode...")
            if not self.set_mode_guided():
                self.logger.warning("⚠ Failed to set GUIDED mode, trying anyway...")
            
            time.sleep(1)  # Give mode change time to settle
            
            # Step 3: Send takeoff command
            self.logger.info(f"Step 3: Sending TAKEOFF command to {altitude}m...")
            
            self.mav_connection.mav.command_long_send(
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
                0,  # confirmation
                0,  # param1: pitch (unused)
                0,  # param2: empty
                0,  # param3: empty
                float('nan'),  # param4: yaw angle (NaN = current)
                0,  # param5: latitude (0 = current)
                0,  # param6: longitude (0 = current)
                altitude  # param7: altitude
            )
            
            # Step 4: Wait for command acknowledgment
            self.logger.info("Step 4: Waiting for TAKEOFF acknowledgment...")
            
            start_time = time.time()
            while (time.time() - start_time) < 10:
                msg = self.mav_connection.recv_match(
                    type='COMMAND_ACK',
                    blocking=True,
                    timeout=1.0
                )
                
                if msg and msg.command == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF:
                    if msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                        self.logger.info("✓ TAKEOFF command accepted")
                        break
                    else:
                        self.logger.error(f"TAKEOFF command rejected: {msg.result}")
                        return False
            
            # Step 5: Monitor altitude climb
            self.logger.info("Step 5: Monitoring altitude climb...")
            return self._monitor_altitude_climb(altitude, timeout=240)
            
        except Exception as e:
            self.logger.error(f"Takeoff failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False


    def set_mode_guided(self) -> bool:
        """
        Set vehicle to GUIDED mode (required for takeoff in PX4)
        """
        try:
            self.logger.info("Setting mode to GUIDED...")
            
            # For PX4: GUIDED mode is represented as AUTO.LOITER
            # Main mode = 4 (AUTO), Sub mode = 3 (LOITER)
            custom_mode = (4 << 16) | 3
            
            self.mav_connection.mav.set_mode_send(
                self.target_system,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                custom_mode
            )
            
            # Wait for mode change confirmation
            start_time = time.time()
            while (time.time() - start_time) < 5:
                msg = self.mav_connection.recv_match(
                    type='HEARTBEAT',
                    blocking=True,
                    timeout=1
                )
                
                if msg:
                    curr_mode = msg.custom_mode
                    main = (curr_mode >> 16) & 0xFF
                    
                    if main == 4:  # AUTO mode family
                        self.logger.info("✓ Mode set to GUIDED (AUTO.LOITER)")
                        return True
            
            self.logger.warning("Mode change to GUIDED not confirmed")
            return False
            
        except Exception as e:
            self.logger.error(f"Failed to set GUIDED mode: {e}")
            return False


    def _monitor_altitude_climb(self, target_altitude: float, timeout: int = 240) -> bool:
        """
        Monitor altitude climb during takeoff
        
        Args:
            target_altitude: Target altitude in meters
            timeout: Maximum time to wait (seconds)
            
        Returns:
            bool: True if altitude reached
        """
        import time
        
        start_time = time.time()
        last_log_time = 0
        
        while time.time() - start_time < timeout:
            msg = self.mav_connection.recv_match(
                type='GLOBAL_POSITION_INT',
                blocking=True,
                timeout=1
            )
            
            if msg:
                current_alt = msg.relative_alt / 1000.0  # Convert from mm to m
                current_time = time.time()
                
                # Log every 2 seconds
                if current_time - last_log_time >= 2:
                    progress = (current_alt / target_altitude) * 100
                    self.logger.info(
                        f"  Climbing: {current_alt:.1f}m / {target_altitude}m "
                        f"({progress:.0f}%)"
                    )
                    last_log_time = current_time
                
                # Check if we've reached target (with 90% tolerance)
                if current_alt >= target_altitude * 0.9:
                    self.logger.info(f"=" * 60)
                    self.logger.info(f"✅ TAKEOFF COMPLETE - Altitude: {current_alt:.1f}m")
                    self.logger.info(f"=" * 60)
                    return True
        
        self.logger.warning(f"⚠ Altitude climb timeout after {timeout}s")
        return False
    
    def arm_and_takeoff(self, altitude: float, force_arm: bool = False, 
                        wait_ready: bool = True, use_offboard: bool = True) -> bool:
        """
        COMBINED ARM AND TAKEOFF - Fixed version
        
        Args:
            altitude: Takeoff altitude in meters
            force_arm: Force arm even if checks fail
            wait_ready: Wait for pre-arm checks
            use_offboard: Use OFFBOARD mode
            
        Returns:
            True if both arm and takeoff successful
        """
        if not self._is_connected:
            self.logger.error("Not connected")
            return False
        
        try:
            self.logger.info("=" * 60)
            self.logger.info(f"ARM AND TAKEOFF SEQUENCE - Target: {altitude}m")
            self.logger.info("=" * 60)
            
            # Step 1: Wait for position
            if not self.wait_for_position(timeout=10):
                self.logger.error("No GPS position")
                return False
            
            # Step 2: Wait until ready (if requested)
            if wait_ready:
                if not self.wait_until_ready_to_arm(timeout=30):
                    if not force_arm:
                        self.logger.error("Pre-arm checks failed")
                        return False
            
            # Step 3: ARM
            if not self.arm_vehicle(force=force_arm, use_offboard=use_offboard):
                self.logger.error("Failed to arm")
                return False
            
            # Step 4: Wait a bit after arming
            self.logger.info("Waiting 2 seconds after arm...")
            time.sleep(2)
            
            # Step 5: TAKEOFF
            if not self.takeoff(altitude):
                self.logger.error("Failed to takeoff")
                return False
            
            # Step 6: Monitor altitude
            self.logger.info(f"Monitoring altitude climb to {altitude}m...")
            start_time = time.time()
            
            for i in range(60):  # Monitor for up to 60 seconds
                msg = self.mav_connection.recv_match(
                    type='GLOBAL_POSITION_INT',
                    blocking=True,
                    timeout=1
                )
                
                if msg:
                    current_alt = msg.relative_alt / 1000.0
                    
                    # Check if reached target (80% of target altitude)
                    if current_alt >= altitude * 0.8:
                        self.logger.info("=" * 60)
                        self.logger.info(f"✅ TAKEOFF COMPLETE - Altitude: {current_alt:.1f}m")
                        self.logger.info("=" * 60)
                        return True
                    
                    # Log progress every 5 seconds
                    if i % 5 == 0:
                        progress = (current_alt / altitude) * 100
                        self.logger.info(f"  Climbing: {current_alt:.1f}m / {altitude}m ({progress:.0f}%)")
            
            self.logger.warning("Did not reach full altitude, but continuing...")
            return True
            
        except Exception as e:
            self.logger.error(f"Arm and takeoff error: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    # ========================================================================
    # HELPER METHODS (Keep existing implementations)
    # ========================================================================
    
    def wait_for_position(self, timeout: int = 30) -> bool:
        """Wait for valid GPS position"""
        self.logger.info("Waiting for GPS position...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            msg = self.mav_connection.recv_match(
                type='GLOBAL_POSITION_INT',
                blocking=True,
                timeout=1
            )
            
            if msg:
                self._position = msg
                self.logger.info(
                    f"✓ GPS: Lat={msg.lat/1e7:.6f}, "
                    f"Lon={msg.lon/1e7:.6f}, Alt={msg.relative_alt/1000:.1f}m"
                )
                return True
        
        self.logger.error("GPS timeout")
        return False
    
    def upload_mission(self, waypoints: List[Dict[str, Any]]) -> bool:
        """Upload mission waypoints using MAVLink protocol and store in Redis"""
        if not self._is_connected:
            self.logger.error("Not connected to simulator")
            return False
        
        try:
            num_waypoints = len(waypoints)
            self.logger.info(f"Uploading mission with {num_waypoints} waypoints...")

            # Clear any pending messages first
            while self.mav_connection.recv_match(blocking=False):
                pass

            # Send mission count
            self.mav_connection.mav.mission_count_send(
                self.target_system,
                self.target_component,
                num_waypoints,
                mavutil.mavlink.MAV_MISSION_TYPE_MISSION
            )
            
            self.logger.info("Sent MISSION_COUNT, waiting for requests...")
            
            # Wait for mission request and upload each waypoint
            for seq in range(num_waypoints):
                # Wait for MISSION_REQUEST with extended timeout and retries
                max_retries = 3
                retry_count = 0
                msg = None
                
                while retry_count < max_retries:
                    msg = self.mav_connection.recv_match(
                        type=['MISSION_REQUEST', 'MISSION_REQUEST_INT'],
                        blocking=True,
                        timeout=10  # Increased timeout to 10 seconds
                    )
                    
                    if msg:
                        break
                    
                    retry_count += 1
                    if retry_count < max_retries:
                        self.logger.warning(
                            f"Timeout waiting for mission request {seq}, "
                            f"retry {retry_count}/{max_retries}"
                        )
                        # Resend mission count
                        self.mav_connection.mav.mission_count_send(
                            self.target_system,
                            self.target_component,
                            num_waypoints,
                            mavutil.mavlink.MAV_MISSION_TYPE_MISSION
                        )
                        time.sleep(0.5)
                
                if not msg:
                    self.logger.error(
                        f"Failed to receive mission request {seq} after {max_retries} retries"
                    )
                    return False
                
                if msg.seq != seq:
                    self.logger.warning(
                        f"Sequence mismatch: expected {seq}, got {msg.seq}. "
                        f"Adjusting to requested sequence."
                    )
                    seq = msg.seq
                
                # Send waypoint
                wp = waypoints[seq]
                
                # Determine command type based on sequence
                if seq == 0:
                    command = mavutil.mavlink.MAV_CMD_NAV_TAKEOFF
                else:
                    command = mavutil.mavlink.MAV_CMD_NAV_WAYPOINT
                
                # Send mission item
                self.mav_connection.mav.mission_item_send(
                    self.target_system,
                    self.target_component,
                    seq,
                    mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                    command,
                    0,  # current (0 = not current waypoint)
                    1,  # autocontinue
                    0,  # param1 (hold time for waypoint)
                    5.0,  # param2 (acceptance radius in meters) - CHANGED from 0 to 5.0
                    0,  # param3
                    float('nan'),  # param4 (yaw angle)
                    float(wp['lat']),
                    float(wp['lon']),
                    float(wp['alt'])
                )
                
                self.logger.info(
                    f"  ✓ Sent waypoint {seq}: "
                    f"({wp['lat']:.6f}, {wp['lon']:.6f}, {wp['alt']}m)"
                )
            
            # Wait for mission ACK
            self.logger.info("All waypoints sent, waiting for ACK...")
            msg = self.mav_connection.recv_match(
                type='MISSION_ACK',
                blocking=True,
                timeout=10  # Increased timeout
            )
            
            if msg and msg.type == mavutil.mavlink.MAV_MISSION_ACCEPTED:
                self._mission_count = num_waypoints
                self.logger.info(
                    f"✅ Mission uploaded successfully ({num_waypoints} waypoints)"
                )
                
                # # Store mission in Redis
                # if self.redis_client:
                #     try:
                #         mission_data = {
                #             'waypoints': json.dumps(waypoints),
                #             'count': num_waypoints,
                #             'uploaded_at': datetime.now().isoformat(),
                #             'name': f'Mission {datetime.now().strftime("%Y%m%d_%H%M%S")}',
                #             'description': f'{num_waypoints} waypoint mission',
                #             'status': 'uploaded',
                #             'created_at': datetime.now().isoformat()
                #         }
                #         self.redis_client.hmset('current_mission', mission_data)
                #         self.logger.info("✓ Mission stored in Redis")
                #     except Exception as e:
                #         self.logger.warning(f"Failed to store mission in Redis: {e}")
                
                return True
            else:
                if msg:
                    ack_types = {
                        0: "ACCEPTED",
                        1: "ERROR",
                        2: "UNSUPPORTED_FRAME",
                        3: "UNSUPPORTED",
                        4: "NO_SPACE",
                        5: "INVALID",
                        6: "INVALID_PARAM1",
                        7: "INVALID_PARAM2",
                        8: "INVALID_PARAM3",
                        9: "INVALID_PARAM4",
                        10: "INVALID_PARAM5_X",
                        11: "INVALID_PARAM6_Y",
                        12: "INVALID_PARAM7_Z",
                        13: "INVALID_SEQUENCE",
                        14: "CANCELLED"
                    }
                    ack_type = ack_types.get(msg.type, f"UNKNOWN({msg.type})")
                    self.logger.error(f"Mission upload rejected: {ack_type}")
                else:
                    self.logger.error("No mission acknowledgment received")
                return False
                
        except Exception as e:
            self.logger.error(f"Error uploading mission: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def wait_until_ready_to_arm(self, timeout: int = 60) -> bool:
        """Wait until vehicle is ready to arm"""
        self.logger.info("Checking pre-arm conditions...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            msg = self.mav_connection.recv_match(
                type='SYS_STATUS',
                blocking=True,
                timeout=1
            )
            
            if msg:
                sensors_health = msg.onboard_control_sensors_health
                sensors_present = msg.onboard_control_sensors_present
                sensors_enabled = msg.onboard_control_sensors_enabled
                
                # Check critical sensors
                critical_sensors = [
                    mavutil.mavlink.MAV_SYS_STATUS_SENSOR_GPS,
                    mavutil.mavlink.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE,
                    mavutil.mavlink.MAV_SYS_STATUS_SENSOR_3D_ACCEL,
                    mavutil.mavlink.MAV_SYS_STATUS_SENSOR_3D_GYRO,
                    mavutil.mavlink.MAV_SYS_STATUS_SENSOR_3D_MAG,
                ]
                
                all_healthy = True
                for sensor in critical_sensors:
                    if (sensors_present & sensor) and (sensors_enabled & sensor):
                        if not (sensors_health & sensor):
                            all_healthy = False
                            break
                
                if all_healthy:
                    self.logger.info("✓ Pre-arm checks passed")
                    return True
            
            time.sleep(1)
        
        self.logger.warning("Pre-arm check timeout")
        return False
    
    def get_telemetry(self) -> Dict[str, Any]:
        """Get current telemetry data"""
        if not self._is_connected:
            return {}
        
        telemetry_data = {}
        
        try:
            self._update_telemetry()
            
            if self._position:
                telemetry_data["position"] = {
                    "latitude": self._position.lat / 1e7,
                    "longitude": self._position.lon / 1e7,
                    "altitude": self._position.relative_alt / 1000.0,
                    "absolute_altitude": self._position.alt / 1000.0
                }
                
                telemetry_data["velocity"] = {
                    "north": self._position.vx / 100.0,
                    "east": self._position.vy / 100.0,
                    "down": self._position.vz / 100.0
                }
            
            if self._attitude:
                telemetry_data["attitude"] = {
                    "roll": math.degrees(self._attitude.roll),
                    "pitch": math.degrees(self._attitude.pitch),
                    "yaw": math.degrees(self._attitude.yaw)
                }
            
            if self._battery:
                telemetry_data["battery"] = {
                    "voltage": self._battery.voltages[0] / 1000.0 if self._battery.voltages else 0,
                    "remaining": self._battery.battery_remaining
                }
            
            if self._gps:
                telemetry_data["gps"] = {
                    "num_satellites": self._gps.satellites_visible,
                    "fix_type": self._gps.fix_type
                }
            
            if self._heartbeat:
                telemetry_data["flight_mode"] = self._get_mode_name(self._heartbeat.custom_mode)
                
        except Exception as e:
            self.logger.error(f"Error getting telemetry: {e}")
        
        return telemetry_data

    def _update_telemetry(self):
        """Update telemetry cache from MAVLink messages"""
        for _ in range(50):
            msg = self.mav_connection.recv_match(blocking=False)
            if not msg:
                break
            
            msg_type = msg.get_type()
            
            if msg_type == 'GLOBAL_POSITION_INT':
                self._position = msg
            elif msg_type == 'ATTITUDE':
                self._attitude = msg
            elif msg_type == 'BATTERY_STATUS':
                self._battery = msg
            elif msg_type == 'GPS_RAW_INT':
                self._gps = msg
            elif msg_type == 'HEARTBEAT':
                self._heartbeat = msg

    def _get_mode_name(self, custom_mode: int) -> str:
        """Get mode name from custom_mode"""
        try:
            mode_mapping = self.mav_connection.mode_mapping()
            
            for name, mode_data in mode_mapping.items():
                if isinstance(mode_data, tuple):
                    if len(mode_data) == 3:
                        _, main, sub = mode_data
                        mode_id = (main << 16) | sub
                        if mode_id == custom_mode:
                            return name
                else:
                    if mode_data == custom_mode:
                        return name
            
            main = (custom_mode >> 16) & 0xFF
            mode_names = {
                1: "MANUAL", 2: "ALTCTL", 3: "POSCTL", 4: "AUTO",
                5: "ACRO", 6: "OFFBOARD", 7: "STABILIZED"
            }
            return mode_names.get(main, f"UNKNOWN({custom_mode})")
            
        except:
            return f"MODE_{custom_mode}"


    def land(self, precision: bool = False, timeout: float = 120.0) -> bool:
        if not self.mav_connection:
            self.logger.error("No MAVLink connection")
            return False
        
        try:
            self.logger.info(f"Commanding land at current position (precision={precision})...")
            
            # Send MAV_CMD_NAV_LAND command
            self.mav_connection.mav.command_long_send(
                self.mav_connection.target_system,
                self.mav_connection.target_component,
                mavutil.mavlink.MAV_CMD_NAV_LAND,
                0,  # confirmation
                0,  # param1: abort altitude (0 = use default)
                1 if precision else 0,  # param2: precision land mode
                0,  # param3: empty
                float('nan'),  # param4: yaw angle (NaN = use current)
                0,  # param5: latitude (0 = current position)
                0,  # param6: longitude (0 = current position)
                0   # param7: altitude (0 = ground level)
            )
            
            # Wait for command acknowledgment
            self.logger.info("Waiting for LAND command acknowledgment...")
            start_time = time.time()
            ack_received = False
            
            while (time.time() - start_time) < 10.0:  # 10 second timeout for ACK
                msg = self.mav_connection.recv_match(
                    type='COMMAND_ACK',
                    blocking=True,
                    timeout=1.0
                )
                
                if msg and msg.command == mavutil.mavlink.MAV_CMD_NAV_LAND:
                    if msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                        self.logger.info("✓ LAND command accepted")
                        ack_received = True
                        break
                    else:
                        self.logger.error(f"LAND command rejected: {msg.result}")
                        return False
            
            if not ack_received:
                self.logger.warning("⚠ No ACK received for LAND command (proceeding anyway)")
            
            # ===================================================================
            # INTEGRATED LANDING MONITOR (replaces _wait_for_landing)
            # ===================================================================
            self.logger.info(f"Monitoring landing progress (timeout: {timeout}s)...")
            
            landing_start_time = time.time()
            last_alt = None
            stable_count = 0
            
            while (time.time() - landing_start_time) < timeout:
                try:
                    # Check heartbeat for disarmed state
                    heartbeat = self.mav_connection.recv_match(
                        type='HEARTBEAT',
                        blocking=False
                    )
                    
                    if heartbeat:
                        armed = heartbeat.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
                        if not armed:
                            self.logger.info("✓ Vehicle disarmed - landing complete")
                            return True
                    
                    # Check altitude and velocity
                    alt_msg = self.mav_connection.recv_match(
                        type='GLOBAL_POSITION_INT',
                        blocking=False
                    )
                    
                    if alt_msg:
                        current_alt = alt_msg.relative_alt / 1000.0  # Convert mm to m
                        vz = alt_msg.vz / 100.0  # Convert cm/s to m/s
                        
                        # Log progress every 5 seconds
                        elapsed = time.time() - landing_start_time
                        if last_alt is None or int(elapsed) % 5 == 0 and elapsed > 0.5:
                            self.logger.info(f"  Altitude: {current_alt:.1f}m, Descent rate: {abs(vz):.1f}m/s")
                        
                        last_alt = current_alt
                        
                        # Check if landed (low altitude and low velocity)
                        if current_alt < 0.5 and abs(vz) < 0.2:
                            stable_count += 1
                            if stable_count >= 5:  # Stable for 5 iterations (~5 seconds)
                                self.logger.info("✓ Vehicle landed (low altitude & velocity)")
                                return True
                        else:
                            stable_count = 0
                    
                    time.sleep(1.0)
                    
                except Exception as e:
                    self.logger.debug(f"Error in landing monitor: {e}")
                    time.sleep(1.0)
            
            self.logger.error(f"❌ Landing timeout after {timeout}s")
            return False
            
        except Exception as e:
            self.logger.error(f"Land command failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False


    def emergency_land(self, timeout: float = 60.0) -> bool:
        
        if not self.mav_connection:
            self.logger.error("No MAVLink connection")
            return False
        
        try:
            self.logger.warning("⚠ EMERGENCY LAND - Initiating immediate descent")
            
            # Switch to LAND mode (PX4: main_mode=4, sub_mode=6)
            # PX4 custom mode calculation: (main_mode << 16) | sub_mode
            custom_mode = (4 << 16) | 6
            
            self.mav_connection.mav.set_mode_send(
                self.mav_connection.target_system,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                custom_mode
            )
            
            # Verify mode change
            time.sleep(1.0)
            
            heartbeat = self.mav_connection.recv_match(
                type='HEARTBEAT',
                blocking=True,
                timeout=2.0
            )
            
            if heartbeat:
                mode = mavutil.mode_string_v10(heartbeat)
                self.logger.info(f"✓ Mode changed to: {mode}")
            
            self.logger.info("✓ Switched to LAND mode - monitoring descent")
            
            # ===================================================================
            # INTEGRATED LANDING MONITOR
            # ===================================================================
            self.logger.info(f"Monitoring landing (timeout: {timeout}s)...")
            
            start_time = time.time()
            last_alt = None
            stable_count = 0
            
            while (time.time() - start_time) < timeout:
                try:
                    # Check disarmed
                    heartbeat = self.mav_connection.recv_match(
                        type='HEARTBEAT',
                        blocking=False
                    )
                    
                    if heartbeat:
                        armed = heartbeat.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
                        if not armed:
                            self.logger.info("✓ Vehicle disarmed - emergency landing complete")
                            return True
                    
                    # Check altitude
                    alt_msg = self.mav_connection.recv_match(
                        type='GLOBAL_POSITION_INT',
                        blocking=False
                    )
                    
                    if alt_msg:
                        current_alt = alt_msg.relative_alt / 1000.0
                        vz = alt_msg.vz / 100.0
                        
                        elapsed = time.time() - start_time
                        if last_alt is None or int(elapsed) % 5 == 0 and elapsed > 0.5:
                            self.logger.info(f"  Alt: {current_alt:.1f}m, Rate: {abs(vz):.1f}m/s")
                        
                        last_alt = current_alt
                        
                        if current_alt < 0.5 and abs(vz) < 0.2:
                            stable_count += 1
                            if stable_count >= 3:  # Emergency = faster detection
                                self.logger.info("✓ Emergency landing complete")
                                return True
                        else:
                            stable_count = 0
                    
                    time.sleep(1.0)
                    
                except Exception as e:
                    self.logger.debug(f"Error in landing monitor: {e}")
                    time.sleep(1.0)
            
            self.logger.error(f"❌ Emergency landing timeout after {timeout}s")
            return False
            
        except Exception as e:
            self.logger.error(f"Emergency land failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False


    def get_landing_state(self) -> dict:
        """
        Get current landing state (STANDALONE VERSION)
        
        Returns detailed landing information.
        
        Returns:
            dict: Landing state with keys:
                - is_landing: bool
                - altitude: float (meters)
                - descent_rate: float (m/s)
                - flight_mode: str
                - armed: bool
        """
        landing_state = {
            'is_landing': False,
            'altitude': 0.0,
            'descent_rate': 0.0,
            'flight_mode': 'UNKNOWN',
            'armed': False
        }
        
        try:
            # Get heartbeat
            heartbeat = self.mav_connection.recv_match(
                type='HEARTBEAT',
                blocking=True,
                timeout=1.0
            )
            
            if heartbeat:
                landing_state['armed'] = bool(
                    heartbeat.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
                )
                
                # Check if in LAND mode
                mode = mavutil.mode_string_v10(heartbeat)
                landing_state['flight_mode'] = mode
                landing_state['is_landing'] = 'LAND' in mode.upper()
            
            # Get position
            pos_msg = self.mav_connection.recv_match(
                type='GLOBAL_POSITION_INT',
                blocking=True,
                timeout=1.0
            )
            
            if pos_msg:
                landing_state['altitude'] = pos_msg.relative_alt / 1000.0  # mm to m
                landing_state['descent_rate'] = pos_msg.vz / 100.0  # cm/s to m/s
        
        except Exception as e:
            self.logger.debug(f"Error getting landing state: {e}")
        
        return landing_state


    def return_to_launch(self, timeout: float = 30.0) -> bool:
        """
        Command vehicle to return to launch position
        
        Args:
            timeout: Maximum time to wait for RTL confirmation (seconds)
            
        Returns:
            bool: True if RTL command acknowledged
        """
        if not self.mav_connection:
            self.logger.error("No MAVLink connection")
            return False
        
        try:
            self.logger.info("Commanding Return to Launch (RTL)...")
            
            # Send RTL command
            self.mav_connection.mav.command_long_send(
                self.mav_connection.target_system,
                self.mav_connection.target_component,
                mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
                0,  # confirmation
                0, 0, 0, 0, 0, 0, 0  # unused parameters
            )
            
            # Wait for command acknowledgment with timeout
            import time
            start_time = time.time()
            
            while (time.time() - start_time) < timeout:
                msg = self.mav_connection.recv_match(
                    type='COMMAND_ACK',
                    blocking=True,
                    timeout=1.0  # Short timeout per iteration
                )
                
                if msg:
                    if msg.command == mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH:
                        if msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                            self.logger.info("✓ RTL command accepted")
                            return True
                        else:
                            self.logger.warning(f"RTL command rejected: {msg.result}")
                            return False
                
                # Check if we're already in RTL mode (command might have worked without ACK)
                if self._check_flight_mode('RTL'):
                    self.logger.info("✓ Vehicle entered RTL mode")
                    return True
            
            # Timeout - but check mode one more time
            if self._check_flight_mode('RTL'):
                self.logger.warning("⚠ RTL timeout but vehicle is in RTL mode")
                return True
            
            self.logger.error(f"❌ RTL command timeout after {timeout}s")
            return False
            
        except Exception as e:
            self.logger.error(f"RTL command failed: {e}")
            return False

    def start_mission(self) -> bool:
        """Start the uploaded mission - COMPLETE VERSION with fallbacks"""
        if not self._is_connected:
            return False
        
        try:
            self.logger.info("Starting mission...")
            
            # Step 1: Set current mission item to 0
            self.logger.info("Setting current mission item to 0...")
            self.mav_connection.mav.mission_set_current_send(
                self.target_system,
                self.target_component,
                0
            )
            
            time.sleep(0.5)
            
            # Check for confirmation
            msg = self.mav_connection.recv_match(
                type='MISSION_CURRENT',
                blocking=True,
                timeout=2
            )
            
            if msg:
                self.logger.info(f"✓ Current mission item: {msg.seq}")
            else:
                self.logger.warning("No MISSION_CURRENT confirmation")
            
            # Step 2: Try MAV_CMD_MISSION_START (PX4 specific)
            self.logger.info("Sending MAV_CMD_MISSION_START command...")
            
            self.mav_connection.mav.command_long_send(
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_CMD_MISSION_START,
                0,  # confirmation
                0,  # param1: first mission item (0 for current)
                0,  # param2: last mission item (0 for last)
                0, 0, 0, 0, 0  # unused
            )
            
            # Wait for ACK
            ack = self.mav_connection.recv_match(
                type='COMMAND_ACK',
                blocking=True,
                timeout=3
            )
            
            if ack and ack.command == mavutil.mavlink.MAV_CMD_MISSION_START:
                result_names = {
                    0: "ACCEPTED",
                    1: "TEMPORARILY_REJECTED",
                    2: "DENIED",
                    3: "UNSUPPORTED",
                    4: "FAILED"
                }
                result = result_names.get(ack.result, f"UNKNOWN({ack.result})")
                self.logger.info(f"MAV_CMD_MISSION_START result: {result}")
                
                if ack.result == 0:  # ACCEPTED
                    self.logger.info("✓ Mission start command accepted!")
                    time.sleep(1)
                    
                    # Verify we're in mission mode
                    msg = self.mav_connection.recv_match(type='HEARTBEAT', blocking=True, timeout=2)
                    if msg:
                        curr = msg.custom_mode
                        main = (curr >> 16) & 0xFF
                        sub = curr & 0xFFFF
                        self.logger.info(f"Mode after start: main={main}, sub={sub}")
                        
                        if main == 4:  # In AUTO mode (mission should be running)
                            self._mission_active = True
                            return True
                        else:
                            self.logger.warning(f"Not in AUTO mode: main={main}")
                            # Still might work, so set active anyway
                            self._mission_active = True
                            return True
                    
                    self._mission_active = True
                    return True
                
                elif ack.result == 3:  # UNSUPPORTED
                    self.logger.warning("MAV_CMD_MISSION_START not supported, trying mode switch...")
                    # Fall through to mode switching method
                
                else:
                    self.logger.error(f"Mission start command failed: {result}")
                    # Fall through to try mode switching
            
            # Step 3: Fall back to direct mode switching
            self.logger.info("Trying direct mode switch to AUTO.MISSION...")
            
            # For PX4: main=4 (AUTO), sub=4 (MISSION) encoded as custom_mode
            custom_mode = (4 << 16) | 4  # AUTO.MISSION
            
            self.mav_connection.mav.set_mode_send(
                self.target_system,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                custom_mode
            )
            
            time.sleep(1)
            
            # Check if it worked
            msg = self.mav_connection.recv_match(type='HEARTBEAT', blocking=True, timeout=2)
            if msg:
                curr = msg.custom_mode
                main = (curr >> 16) & 0xFF
                sub = curr & 0xFFFF
                
                self.logger.info(f"Final mode: main={main}, sub={sub}")
                
                if main == 4:  # At least in AUTO mode
                    self._mission_active = True
                    self.logger.info("✓ In AUTO mode - mission should start")
                    return True
            
            # All methods failed
            self.logger.error("Failed to start mission with all methods!")
            self.logger.error("Manual workaround: In PX4 console, type: commander mode auto:mission")
            return False
            
        except Exception as e:
            self.logger.error(f"Failed to start mission: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return False
    
    def stop_mission(self, hold_position: bool = True) -> bool:
        """
        Stop/pause the current mission
        
        Args:
            hold_position: If True, switch to LOITER mode to hold position
        
        Returns:
            bool: True if successful, False otherwise
        """
        if not self._is_connected:
            self.logger.error("Not connected to simulator")
            return False
        
        try:
            self.logger.info("Stopping mission execution...")
            
            # First, check if vehicle is armed
            heartbeat = self.mav_connection.recv_match(
                type='HEARTBEAT',
                blocking=True,
                timeout=2
            )
            
            if not heartbeat:
                self.logger.warning("No heartbeat received, attempting mode change anyway...")
            elif not (heartbeat.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
                self.logger.warning("Vehicle is not armed, cannot stop mission")
                # Return True anyway since mission is effectively stopped
                return True
            
            # Try to pause mission first (safer than mode change)
            self.mav_connection.mav.command_long_send(
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_CMD_DO_PAUSE_CONTINUE,
                0,  # confirmation
                0,  # 0 = pause, 1 = continue
                0, 0, 0, 0, 0, 0
            )
            
            # Wait for acknowledgment
            msg = self.mav_connection.recv_match(
                type='COMMAND_ACK',
                blocking=True,
                timeout=3
            )
            
            if msg and msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                self.logger.info("✓ Mission paused successfully")
                return True
            
            # If pause doesn't work, try mode change to HOLD
            self.logger.info("Pause command not accepted, trying mode change...")
            
            # Use HOLD mode (mode 4) which is more universal
            self.mav_connection.mav.command_long_send(
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_MODE,
                0,  # confirmation
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                4,  # HOLD mode (was 5 LOITER - try 4 HOLD instead)
                0, 0, 0, 0, 0
            )
            
            msg = self.mav_connection.recv_match(
                type='COMMAND_ACK',
                blocking=True,
                timeout=5
            )
            
            if not msg:
                self.logger.error("No acknowledgment for mode change")
                return False
            
            if msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                self.logger.info("✓ Mission stopped successfully (mode changed to HOLD)")
                return True
            elif msg.result == mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED:
                self.logger.warning("Mode change temporarily rejected - vehicle may not be in flight")
                # If we're on the ground, that's fine - mission is stopped
                return True
            elif msg.result == mavutil.mavlink.MAV_RESULT_DENIED:
                self.logger.error("Mode change denied by vehicle")
                # Try alternate approach: just clear the mission
                return self._emergency_stop_mission()
            else:
                self.logger.error(f"Mode change rejected with code: {msg.result}")
                return False
            
        except Exception as e:
            self.logger.error(f"Failed to stop mission: {str(e)}")
            return False

    def _emergency_stop_mission(self) -> bool:
        """
        Emergency mission stop - clears current mission
        Used when mode changes are rejected
        """
        try:
            self.logger.warning("Using emergency stop - clearing mission")
            
            # Send mission count of 0 to clear mission
            self.mav_connection.mav.mission_count_send(
                self.target_system,
                self.target_component,
                0,  # count = 0 clears mission
                mavutil.mavlink.MAV_MISSION_TYPE_MISSION
            )
            
            msg = self.mav_connection.recv_match(
                type='MISSION_ACK',
                blocking=True,
                timeout=5
            )
            
            if msg and msg.type == mavutil.mavlink.MAV_MISSION_ACCEPTED:
                self.logger.info("✓ Mission cleared as emergency stop")
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Emergency stop failed: {e}")
            return False

    def pause_mission(self) -> bool:
        """Pause the current mission (same as stop_mission)"""
        return self.stop_mission()

    def resume_mission(self) -> bool:
        """Resume a paused mission"""
        if not self._is_connected:
            self.logger.error("Not connected to simulator")
            return False
        
        try:
            self.logger.info("Resuming mission execution...")
            
            # Set mode back to AUTO
            self.mav_connection.mav.command_long_send(
                self.target_system,
                self.target_component,
                mavutil.mavlink.MAV_CMD_DO_SET_MODE,
                0,
                mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                4,  # AUTO mode
                0, 0, 0, 0, 0
            )
            
            msg = self.mav_connection.recv_match(
                type='COMMAND_ACK',
                blocking=True,
                timeout=5
            )
            
            if msg and msg.result == mavutil.mavlink.MAV_RESULT_ACCEPTED:
                self.logger.info("✓ Mission resumed successfully")
                
                if hasattr(self, 'redis_client') and self.redis_client:
                    self.redis_client.hset(
                        f'mission:{self.target_system}:state',
                        'status',
                        'EXECUTING'
                    )
                
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"Failed to resume mission: {str(e)}")
            return False

    def disconnect(self):
        """Disconnect from simulator"""
        self._is_connected = False
        self._mission_active = False
        
        # Stop OFFBOARD setpoints
        if self._offboard_active:
            self._stop_offboard_setpoints()
        
        if self.mav_connection:
            self.mav_connection.close()
        
        self.logger.info("Disconnected")


# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════════════

sim_controller: Optional[GazeboSimulatorController] = None

drone_state = {
    "connected": False,
    "armed": False,
    "flying": False,
    "current_position": {"lat": 0.0, "lon": 0.0, "alt": 0.0},
    "home_position": {"lat": 0.0, "lon": 0.0, "alt": 0.0},
    "battery_level": 100,
    "flight_mode": "UNKNOWN",
    "mission_active": False,
    "mission_current": 0,
    "mission_count": 0,
    "last_update": None,
    "sensors": {
        "gps": {"status": "UNKNOWN", "satellites": 0, "hdop": 0.0, "vdop": 0.0},
        "camera": {"status": "UNKNOWN", "recording": False, "photo_count": 0}
    },
    "vehicle_info": {
        "type": "quadcopter",
        "autopilot": "PX4",
        "version": "unknown",
        "system_id": 1,
        "component_id": 1
    }
}

# Storage
geofences: List[Dict[str, Any]] = []
parameters_cache: Dict[str, float] = {}
mission_templates: Dict[str, Dict[str, Any]] = {}
workflows: Dict[str, Dict[str, Any]] = {}
event_history = deque(maxlen=1000)

# ═══════════════════════════════════════════════════════════════════════════════
# ENUMERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

class GPSFixType(IntEnum):
    """GPS Fix Types"""
    NO_GPS = 0
    NO_FIX = 1
    FIX_2D = 2
    FIX_3D = 3
    DGPS = 4
    RTK_FLOAT = 5
    RTK_FIXED = 6

class CameraMode(IntEnum):
    """Camera Operating Modes"""
    IMAGE = 0
    VIDEO = 1
    IMAGE_SURVEY = 2

class GeofenceType(str, Enum):
    """Geofence Types"""
    INCLUSION = "inclusion"
    EXCLUSION = "exclusion"

class GeofenceShape(str, Enum):
    """Geofence Shapes"""
    CIRCLE = "circle"
    POLYGON = "polygon"
    CYLINDER = "cylinder"

class MissionTemplateType(str, Enum):
    """Mission Template Types"""
    SURVEY = "survey"
    CORRIDOR = "corridor"
    STRUCTURE_SCAN = "structure_scan"
    PERIMETER = "perimeter"
    GRID_SEARCH = "grid_search"

class WorkflowStatus(str, Enum):
    """Workflow Execution Status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class EventType(str, Enum):
    """Event Types"""
    SYSTEM = "system"
    VEHICLE = "vehicle"
    MISSION = "mission"
    GEOFENCE = "geofence"
    WORKFLOW = "workflow"

# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS - BASIC
# ═══════════════════════════════════════════════════════════════════════════════

class ApiResponse(BaseModel):
    """Standard API Response"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

class MissionControlResponse(BaseModel):
    """Standardized Mission Control Response for Qt Client"""
    success: bool
    command: str
    status: str
    message: str
    data: Optional[Dict[str, Any]] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    vehicle_id: str = "vehicle_1"
    error_code: Optional[int] = None

class MissionControlCommand(BaseModel):
    """Base model for mission control commands"""
    vehicle_id: Optional[str] = Field(default="vehicle_1", description="Target vehicle ID")
    timeout_seconds: Optional[int] = Field(default=30, description="Command timeout")
    force: Optional[bool] = Field(default=False, description="Force execution")

class ArmCommand(MissionControlCommand):
    """Arm vehicle command"""
    pass

class DisarmCommand(MissionControlCommand):
    """Disarm vehicle command"""
    pass

class TakeoffCommand(MissionControlCommand):
    """Takeoff command"""
    altitude_meters: float = Field(default=10.0, ge=5.0, le=120.0, description="Takeoff altitude in meters")

class LandCommand(MissionControlCommand):
    """Land command"""
    emergency: Optional[bool] = Field(default=False, description="Emergency landing")

class RTLCommand(MissionControlCommand):
    """Return to Launch command"""
    pass

class StartMissionCommand(MissionControlCommand):
    """Start mission execution command"""
    mission_id: Optional[str] = Field(default=None, description="Mission ID to execute")
    resume: Optional[bool] = Field(default=False, description="Resume from last waypoint")

class StopMissionCommand(MissionControlCommand):
    """Stop mission execution command"""
    hold_position: Optional[bool] = Field(default=True, description="Hold position after stop")

class SensorConfig(BaseModel):
    """Sensor Configuration"""
    enable_camera: bool = Field(default=True)
    enable_gps_logging: bool = Field(default=True)
    camera_mode: CameraMode = Field(default=CameraMode.IMAGE)
    photo_interval: float = Field(default=5.0)
    video_quality: int = Field(default=1)
    gps_accuracy_required: float = Field(default=3.0)

class Waypoint(BaseModel):
    """Mission Waypoint"""
    model_config = ConfigDict(extra="allow")
    
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    alt: float = Field(default=10, ge=0)
    sensor_actions: Optional[Dict[str, Any]] = None
    camera_params: Optional[Dict[str, Any]] = None
    gps_params: Optional[Dict[str, Any]] = None

class SensorStatus(BaseModel):
    """Sensor Status Information"""
    gps_status: str
    gps_satellites: int
    gps_hdop: float
    gps_accuracy: float
    camera_status: str
    camera_recording: bool
    camera_photos_taken: int
    last_sensor_update: Optional[str]

class VehicleInfo(BaseModel):
    """Vehicle Information"""
    type: str
    autopilot: str
    version: str
    system_id: int
    component_id: int
    capabilities: List[str]

class DroneStatus(BaseModel):
    """Complete Drone Status"""
    connected: bool
    armed: bool
    flying: bool
    current_position: Dict[str, float]
    home_position: Dict[str, float]
    battery_level: int
    flight_mode: str
    mission_active: bool
    mission_current: int
    mission_count: int
    sensor_status: SensorStatus
    vehicle_info: Dict[str, Any]
    last_update: Optional[str]
    message: str

class TelemetryData(BaseModel):
    """Telemetry Data"""
    position: Optional[Dict[str, float]] = None
    velocity: Optional[Dict[str, float]] = None
    attitude: Optional[Dict[str, float]] = None
    battery: Optional[Dict[str, float]] = None
    gps: Optional[Dict[str, int]] = None
    flight_mode: Optional[str] = None

# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS - REQUESTS
# ═══════════════════════════════════════════════════════════════════════════════

class MissionRequest(BaseModel):
    """Basic Mission Request"""
    waypoints: List[Waypoint] = Field(..., min_length=1)
    mission_type: Optional[str] = Field(default="custom")
    sensor_config: Optional[SensorConfig] = Field(default_factory=SensorConfig)
    
    @field_validator('waypoints')
    @classmethod
    def validate_waypoints(cls, v):
        if not v:
            raise ValueError('Waypoints list cannot be empty')
        return v

class OrbitMissionRequest(BaseModel):
    """Orbit Mission Request"""
    center_lat: float
    center_lon: float
    altitude: float = Field(default=20)
    radius: float = Field(default=0.0003)
    num_points: int = Field(default=16)
    clockwise: bool = Field(default=True)
    pattern_type: str = Field(default="circular")
    sensor_config: Optional[SensorConfig] = Field(default_factory=SensorConfig)

class SurveyMissionRequest(BaseModel):
    """Area Survey Mission Request"""
    name: str
    polygon: List[Tuple[float, float]] = Field(..., description="Area polygon vertices (lat, lon)")
    altitude: float = Field(default=50, description="Survey altitude in meters")
    grid_spacing: float = Field(default=30, description="Distance between survey lines in meters")
    overlap: float = Field(default=0.7, description="Image overlap ratio (0-1)")
    camera_angle: float = Field(default=90, description="Camera angle in degrees")

class CorridorMissionRequest(BaseModel):
    """Corridor Inspection Mission Request"""
    name: str
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    altitude: float = Field(default=50)
    width: float = Field(default=100, description="Corridor width in meters")
    segments: int = Field(default=5, description="Number of segments")

class StructureScanRequest(BaseModel):
    """3D Structure Scan Mission Request"""
    name: str
    center_lat: float
    center_lon: float
    altitude_min: float = Field(default=20)
    altitude_max: float = Field(default=80)
    radius: float = Field(default=50, description="Scan radius in meters")
    orbits: int = Field(default=3, description="Number of orbits")
    points_per_orbit: int = Field(default=16)

class PerimeterMissionRequest(BaseModel):
    """Perimeter Patrol Mission Request"""
    name: str
    polygon: List[Tuple[float, float]]
    altitude: float = Field(default=30)
    spacing: float = Field(default=20, description="Spacing between waypoints")

class MissionValidationRequest(BaseModel):
    """Mission Validation Request"""
    mission_id: Optional[str] = None
    waypoints: Optional[List[Waypoint]] = None

class ArmRequest(BaseModel):
    """Arm Request"""
    force: bool = Field(default=False)
    retry_count: int = Field(default=3)

class TakeoffRequest(BaseModel):
    """Takeoff Request"""
    altitude: float = Field(default=10.0)

class ArmAndTakeoffRequest(BaseModel):
    """Combined Arm and Takeoff Request"""
    altitude: float = Field(default=10.0)
    force_arm: bool = Field(default=False)
    wait_ready: bool = Field(default=True)

class ModeRequest(BaseModel):
    """Flight Mode Change Request"""
    mode: str = Field(..., description="Flight mode")

class SpeedRequest(BaseModel):
    """Speed Change Request"""
    speed: float = Field(..., description="Speed in m/s", gt=0, le=20)

class GotoPositionRequest(BaseModel):
    """Go-to Position Request"""
    lat: float
    lon: float
    alt: float
    speed: Optional[float] = Field(default=5.0)

class SetHomeRequest(BaseModel):
    """Set Home Position Request"""
    lat: float
    lon: float
    alt: float

class ParameterSet(BaseModel):
    """Parameter Set Request"""
    name: str
    value: float

class ParameterGet(BaseModel):
    """Parameter Get Request"""
    name: str

class GeofenceCircle(BaseModel):
    """Circular Geofence"""
    name: str
    type: str = Field(default=GeofenceType.EXCLUSION.value)
    center_lat: float
    center_lon: float
    radius: float = Field(..., description="Radius in meters")
    min_altitude: float = Field(default=0)
    max_altitude: float = Field(default=120)
    enabled: bool = Field(default=True)

class GeofencePolygon(BaseModel):
    """Polygon Geofence"""
    name: str
    type: str = Field(default=GeofenceType.EXCLUSION.value)
    vertices: List[Tuple[float, float]] = Field(..., min_length=3)
    min_altitude: float = Field(default=0)
    max_altitude: float = Field(default=120)
    enabled: bool = Field(default=True)

class GeofenceCylinder(BaseModel):
    """Cylindrical Geofence"""
    name: str
    type: str = Field(default=GeofenceType.EXCLUSION.value)
    center_lat: float
    center_lon: float
    radius: float
    min_altitude: float = Field(default=0)
    max_altitude: float = Field(default=120)
    enabled: bool = Field(default=True)

class WorkflowCreateRequest(BaseModel):
    """Workflow Creation Request"""
    name: str
    steps: List[Dict[str, Any]]
    context: Optional[Dict[str, Any]] = None

class SkyrouteXMissionUpload(BaseModel):
    """Mission upload request compatible with skyroutex-client"""
    mission_id: str
    vehicle_id: str
    connection_string: Optional[str] = Field(default="udp://127.0.0.1:14540")
    waypoints: List[Dict[str, float]] = Field(..., min_length=1)
    parameters: Optional[Dict[str, Any]] = Field(default_factory=dict)

# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def update_drone_state_from_telemetry():
    """Update drone state from simulator telemetry"""
    global sim_controller, drone_state
    
    if not sim_controller or not sim_controller._is_connected:
        return
    
    try:
        telemetry = sim_controller.get_telemetry()
        
        if telemetry:
            if "position" in telemetry:
                pos = telemetry["position"]
                drone_state["current_position"] = {
                    "lat": pos.get("latitude", 0.0),
                    "lon": pos.get("longitude", 0.0),
                    "alt": pos.get("altitude", 0.0)
                }
            
            if "battery" in telemetry:
                drone_state["battery_level"] = telemetry["battery"].get("remaining", 100)
            
            if "gps" in telemetry:
                gps = telemetry["gps"]
                fix_type = gps.get("fix_type", 0)
                drone_state["sensors"]["gps"] = {
                    "status": "FIXED" if fix_type >= 3 else "NO_FIX",
                    "satellites": gps.get("num_satellites", 0),
                    "hdop": 1.0,
                    "vdop": 1.0
                }
            
            # Decode flight mode properly
            if "flight_mode" in telemetry:
                raw_mode = telemetry["flight_mode"]
                # Check if it's a numeric mode that needs decoding
                if isinstance(raw_mode, (int, float)):
                    drone_state["flight_mode"] = decode_px4_mode(int(raw_mode))
                else:
                    drone_state["flight_mode"] = raw_mode
            
            if sim_controller._mission_active:
                drone_state["mission_active"] = True
                drone_state["mission_current"] = sim_controller._mission_current
                drone_state["mission_count"] = sim_controller._mission_count
            
            if sim_controller._heartbeat:
                hb = sim_controller._heartbeat
                autopilot_map = {12: "PX4", 3: "ArduPilot", 0: "Generic"}
                drone_state["vehicle_info"]["autopilot"] = autopilot_map.get(hb.autopilot, "Unknown")
            
            drone_state["last_update"] = datetime.now().isoformat()
            
    except Exception as e:
        logger.error(f"Error updating drone state: {e}")

def decode_px4_mode(custom_mode: int) -> str:
    """Decode PX4 custom mode to human-readable string"""
    main_mode = (custom_mode >> 16) & 0xFF
    sub_mode = custom_mode & 0xFFFF
    
    mode_map = {
        1: {0: "MANUAL"},
        2: {0: "ALTCTL"},
        3: {0: "POSCTL"},
        4: {1: "AUTO.READY", 2: "AUTO.TAKEOFF", 3: "AUTO.LOITER", 4: "AUTO.MISSION", 
           5: "AUTO.RTL", 6: "AUTO.LAND", 7: "AUTO.RTGS", 8: "AUTO.FOLLOW", 9: "AUTO.PRECLAND"},
        5: {0: "ACRO"},
        6: {0: "OFFBOARD"},
        7: {0: "STABILIZED"},
        8: {0: "RATTITUDE"}
    }
    
    if main_mode in mode_map and sub_mode in mode_map[main_mode]:
        return mode_map[main_mode][sub_mode]
    
    return f"UNKNOWN(main={main_mode}, sub={sub_mode})"

def create_event(event_type: str, source: str, data: Dict[str, Any]):
    """Create event entry"""
    event = {
        "id": str(uuid.uuid4()),
        "type": event_type,
        "source": source,
        "timestamp": datetime.now().isoformat(),
        "data": data
    }
    event_history.append(event)
    return event

def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two GPS coordinates in meters"""
    R = 6371000  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi/2)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    
    return R * c

def point_in_polygon(lat: float, lon: float, polygon: List[Tuple[float, float]]) -> bool:
    """Check if point is inside polygon"""
    n = len(polygon)
    inside = False
    
    j = n - 1
    for i in range(n):
        if ((polygon[i][1] > lon) != (polygon[j][1] > lon)) and \
           (lat < (polygon[j][0] - polygon[i][0]) * (lon - polygon[i][1]) / 
            (polygon[j][1] - polygon[i][1]) + polygon[i][0]):
            inside = not inside
        j = i
    
    return inside

def check_geofence_violation(lat: float, lon: float, alt: float) -> Tuple[bool, str]:
    """Check if position violates any geofence"""
    for fence in geofences:
        if not fence.get("enabled", True):
            continue
        
        fence_type = fence["type"]
        
        if fence["shape"] == "circle":
            dist = haversine_distance(lat, lon, fence["center_lat"], fence["center_lon"])
            if dist <= fence["radius"] and fence["min_altitude"] <= alt <= fence["max_altitude"]:
                if fence_type == "exclusion":
                    return True, f"Inside exclusion zone: {fence['name']}"
        
        elif fence["shape"] == "polygon":
            if point_in_polygon(lat, lon, fence["vertices"]) and \
               fence["min_altitude"] <= alt <= fence["max_altitude"]:
                if fence_type == "exclusion":
                    return True, f"Inside exclusion zone: {fence['name']}"
        
        elif fence["shape"] == "cylinder":
            dist = haversine_distance(lat, lon, fence["center_lat"], fence["center_lon"])
            if dist <= fence["radius"] and fence["min_altitude"] <= alt <= fence["max_altitude"]:
                if fence_type == "exclusion":
                    return True, f"Inside exclusion zone: {fence['name']}"
    
    return False, "Safe"

def generate_orbit_waypoints(center_lat: float, center_lon: float, altitude: float, 
                             radius: float, num_points: int, clockwise: bool = True,
                             pattern_type: str = "circular") -> List[Dict[str, float]]:
    """Generate orbit mission waypoints"""
    waypoints = []
    
    if pattern_type == "circular":
        for i in range(num_points):
            angle = 2 * math.pi * i / num_points
            if clockwise:
                angle = -angle
            
            lat = center_lat + radius * math.sin(angle)
            lon = center_lon + radius * math.cos(angle) / math.cos(math.radians(center_lat))
            waypoints.append({"lat": lat, "lon": lon, "alt": altitude})
    
    elif pattern_type == "spiral":
        for i in range(num_points):
            angle = 2 * math.pi * i / num_points
            current_radius = radius * (1 + i / num_points)
            lat = center_lat + current_radius * math.sin(angle)
            lon = center_lon + current_radius * math.cos(angle) / math.cos(math.radians(center_lat))
            alt = altitude + (10 * i / num_points)
            waypoints.append({"lat": lat, "lon": lon, "alt": alt})
    
    return waypoints

def generate_survey_waypoints(polygon: List[Tuple[float, float]], altitude: float, 
                              grid_spacing: float, overlap: float) -> List[Dict[str, float]]:
    """Generate survey mission waypoints"""
    waypoints = []
    lats = [p[0] for p in polygon]
    lons = [p[1] for p in polygon]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    
    lat_spacing = grid_spacing / 111000
    current_lat = min_lat
    direction = 1
    
    while current_lat <= max_lat:
        if direction == 1:
            waypoints.append({"lat": current_lat, "lon": min_lon, "alt": altitude})
            waypoints.append({"lat": current_lat, "lon": max_lon, "alt": altitude})
        else:
            waypoints.append({"lat": current_lat, "lon": max_lon, "alt": altitude})
            waypoints.append({"lat": current_lat, "lon": min_lon, "alt": altitude})
        current_lat += lat_spacing
        direction *= -1
    
    return waypoints

def generate_corridor_waypoints(start_lat: float, start_lon: float, end_lat: float, 
                                end_lon: float, altitude: float, width: float, 
                                segments: int) -> List[Dict[str, float]]:
    """Generate corridor mission waypoints"""
    waypoints = []
    for i in range(segments + 1):
        t = i / segments
        lat = start_lat + t * (end_lat - start_lat)
        lon = start_lon + t * (end_lon - start_lon)
        waypoints.append({"lat": lat, "lon": lon, "alt": altitude})
    return waypoints

def generate_structure_scan_waypoints(center_lat: float, center_lon: float, 
                                     altitude_min: float, altitude_max: float,
                                     radius: float, orbits: int, 
                                     points_per_orbit: int) -> List[Dict[str, float]]:
    """Generate structure scan waypoints"""
    waypoints = []
    radius_deg = radius / 111000
    
    for orbit in range(orbits):
        alt = altitude_min + (altitude_max - altitude_min) * (orbit / max(orbits - 1, 1))
        for point in range(points_per_orbit):
            angle = 2 * math.pi * point / points_per_orbit
            lat = center_lat + radius_deg * math.sin(angle)
            lon = center_lon + radius_deg * math.cos(angle) / math.cos(math.radians(center_lat))
            waypoints.append({"lat": lat, "lon": lon, "alt": alt})
    
    return waypoints

def validate_mission_feasibility(waypoints: List[Dict[str, float]], 
                                vehicle_capabilities: Dict[str, Any]) -> Dict[str, Any]:
    """Validate mission against vehicle capabilities"""
    issues = []
    warnings = []
    
    total_distance = 0
    for i in range(len(waypoints) - 1):
        wp1, wp2 = waypoints[i], waypoints[i + 1]
        distance = haversine_distance(wp1["lat"], wp1["lon"], wp2["lat"], wp2["lon"])
        total_distance += distance
    
    max_alt = max(wp["alt"] for wp in waypoints)
    if max_alt > vehicle_capabilities.get("max_altitude", 120):
        issues.append(f"Mission altitude {max_alt}m exceeds vehicle limit")
    
    max_range = vehicle_capabilities.get("max_range", 5000)
    if total_distance > max_range:
        issues.append(f"Mission distance {total_distance:.0f}m exceeds vehicle range")
    
    cruise_speed = vehicle_capabilities.get("cruise_speed", 10)
    estimated_time = total_distance / cruise_speed if cruise_speed > 0 else 0
    
    endurance = vehicle_capabilities.get("endurance", 1800)
    if estimated_time > endurance:
        warnings.append(f"Mission time {estimated_time:.0f}s may exceed endurance")
    
    required_battery = (estimated_time / endurance) * 100 if endurance > 0 else 0
    
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
        "total_distance": round(total_distance, 2),
        "estimated_time": round(estimated_time, 2),
        "required_battery": round(min(required_battery, 100), 2)
    }

# ═══════════════════════════════════════════════════════════════════════════════
# FASTAPI APPLICATION SETUP
# ═══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager"""
    global sim_controller
    
    logger.info("═" * 80)
    logger.info("🚁 Starting Combined Drone Control API v5.0.0")
    logger.info("═" * 80)
    
    config = SimulatorConfig(
        connection_url="udp:127.0.0.1:14540",
        vehicle_model="iris",
        simulator_type=SimulatorType.PX4_GAZEBO,
        timeout_seconds=60
    )
    
    sim_controller = GazeboSimulatorController(config)
    
    try:
        if sim_controller.connect():
            drone_state["connected"] = True
            logger.info("✅ Connected to PX4 SITL")
            await update_drone_state_from_telemetry()
        else:
            logger.warning("⚠️  Not connected to PX4 SITL (will retry on demand)")
    except Exception as e:
        logger.warning(f"⚠️  Initial connection failed: {e}")
    
    logger.info("🌐 API Server Ready")
    logger.info("📚 Documentation: http://127.0.0.1:7000/docs")
    logger.info("═" * 80)
    
    yield
    
    logger.info("🛑 Shutting down Drone Control API...")
    if sim_controller:
        sim_controller.disconnect()

app = FastAPI(
    title="Combined Drone Control API with Gazebo Integration",
    description="Complete REST API with PyMAVLink, Telemetry Collection, and Advanced Mission Planning",
    version="5.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_model=ApiResponse)
async def root():
    """Root endpoint - API health check"""
    return ApiResponse(
        success=True,
        message="Combined Drone Control API v5.0.0 is operational",
        data={
            "version": "5.0.0",
            "connected": drone_state["connected"],
            "documentation": "/docs",
            "status_endpoint": "/status",
            "total_endpoints": "75+"
        }
    )

@app.get("/api/info", response_model=ApiResponse)
async def api_info():
    """Complete API information and capabilities"""
    return ApiResponse(
        success=True,
        message="API Information",
        data={
            "version": "5.0.0",
            "name": "Combined Drone Control API",
            "description": "Complete REST API with Gazebo/PX4 integration, telemetry collection, and advanced mission planning",
            "features": [
                "PX4/Gazebo SITL Integration with PyMAVLink",
                "Real-time Telemetry Collection with Redis",
                "Advanced Mission Planning",
                "Survey/Corridor/Structure Scanning",
                "Geofencing System",
                "Parameter Management",
                "Workflow Orchestration",
                "Event System",
                "Real-time WebSocket Telemetry"
            ]
        }
    )

# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class MissionControlRequest(BaseModel):
    """Base request for mission control operations"""
    force_start: Optional[bool] = Field(False, description="Force start even if conditions not met")
    force_stop: Optional[bool] = Field(False, description="Force stop immediately")

class VehicleArmRequest(BaseModel):
    """Request to arm the vehicle"""
    mission_id: str = Field(..., description="Mission ID")
    force_arm: Optional[bool] = Field(False, description="Force arm even if not ready")


class VehicleDisarmRequest(BaseModel):
    """Request to disarm the vehicle"""
    mission_id: str = Field(..., description="Mission ID")


class TakeoffRequest(BaseModel):
    """Request for takeoff"""
    mission_id: str = Field(..., description="Mission ID")
    altitude: float = Field(..., ge=1.0, le=100.0, description="Takeoff altitude in meters")


class LandRequest(BaseModel):
    """Request for landing"""
    mission_id: str = Field(..., description="Mission ID")

class RTLRequest(BaseModel):
    """Request for return to launch"""
    mission_id: str = Field(..., description="Mission ID")
class ApiResponse(BaseModel):
    """Standard API response"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

# ============================================================================
# MISSION CONTROL ENDPOINTS
# ============================================================================

@app.post("/api/v1/missions/{mission_id}/start", response_model=ApiResponse, tags=["Mission Control"])
async def start_mission(mission_id: str, request: MissionControlRequest = MissionControlRequest()):
    """
    🚀 START MISSION
    
    Starts execution of the uploaded mission.
    
    **Prerequisites:**
    - Mission must be uploaded to the vehicle
    - Vehicle must be armed
    - Vehicle should be airborne (or will takeoff)
    
    **Path Parameters:**
    - mission_id: Mission identifier
    
    **Request Body:**
    - force_start: Force start even if mission already active (optional)
    
    **Returns:**
    - Success response with mission status
    
    **Example:**
    ```bash
    curl -X POST http://127.0.0.1:7000/api/v1/missions/TEST-MISSION-001/start \
      -H "Content-Type: application/json" \
      -d '{"force_start": false}'
    ```
    """
    global sim_controller, drone_state
    
    # Check if connected
    if not sim_controller or not sim_controller._is_connected:
        logger.error(f"START MISSION failed: Not connected to simulator")
        raise HTTPException(
            status_code=503,
            detail="Not connected to simulator. Ensure PX4 SITL is running."
        )
    
    # Check if armed (optional - some missions auto-arm)
    if not drone_state["armed"]:
        logger.warning(f"Starting mission {mission_id} but vehicle not armed")
        # Don't fail - just warn
        # Some workflows may arm as part of mission start
    
    # Check if mission already active
    if drone_state["mission_active"] and not request.force_start:
        logger.error(f"START MISSION failed: Mission already active")
        raise HTTPException(
            status_code=400,
            detail="Mission already active. Use force_start=true to override, or stop current mission first."
        )
    
    # Check if mission is uploaded
    if drone_state["mission_count"] == 0:
        logger.error(f"START MISSION failed: No mission uploaded")
        raise HTTPException(
            status_code=400,
            detail="No mission uploaded. Upload a mission first using /api/v1/missions/upload-to-px4/{mission_id}"
        )
    
    try:
        logger.info(f"🚀 Starting mission: {mission_id}")
        logger.info(f"   Mission waypoint count: {drone_state['mission_count']}")
        logger.info(f"   Vehicle armed: {drone_state['armed']}")
        logger.info(f"   Vehicle flying: {drone_state['flying']}")
        
        # Start the mission (no parameters - uses already uploaded mission)
        success = sim_controller.start_mission()
        
        if success:
            drone_state["mission_active"] = True
            
            # Create event
            create_event(
                EventType.MISSION.value,
                "mission_control",
                {
                    "command": "start_mission",
                    "mission_id": mission_id,
                    "waypoint_count": drone_state["mission_count"],
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            logger.info(f"✅ Mission {mission_id} started successfully")
            
            return ApiResponse(
                success=True,
                message=f"Mission {mission_id} started successfully",
                data={
                    "mission_id": mission_id,
                    "status": "executing",
                    "mission_active": True,
                    "waypoint_count": drone_state["mission_count"],
                    "current_waypoint": drone_state["mission_current"],
                    "timestamp": datetime.now().isoformat()
                }
            )
        else:
            logger.error(f"START MISSION failed: start_mission() returned False")
            raise HTTPException(
                status_code=500,
                detail="Failed to start mission. Check PX4 console for errors. Ensure vehicle is in correct mode."
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ START MISSION error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Mission start failed: {str(e)}"
        )


@app.post("/api/v1/missions/{mission_id}/stop", response_model=ApiResponse, tags=["Mission Control"])
async def stop_mission(mission_id: str, request: MissionControlRequest = MissionControlRequest()):
    """
    ⏸️ STOP MISSION
    
    Stops the currently executing mission.
    Vehicle will hold position (LOITER mode).
    
    **Path Parameters:**
    - mission_id: Mission identifier
    
    **Request Body:**
    - force_stop: Force stop immediately (optional)
    
    **Returns:**
    - Success response with mission status
    
    **Example:**
    ```bash
    curl -X POST http://127.0.0.1:7000/api/v1/missions/TEST-MISSION-001/stop \
      -H "Content-Type: application/json" \
      -d '{"force_stop": false}'
    ```
    """
    global sim_controller, drone_state
    
    # Check if connected
    if not sim_controller or not sim_controller._is_connected:
        logger.error(f"STOP MISSION failed: Not connected to simulator")
        raise HTTPException(
            status_code=503,
            detail="Not connected to simulator"
        )
    
    # Check if mission is active
    if not drone_state["mission_active"] and not request.force_stop:
        logger.warning(f"STOP MISSION: No mission currently active")
        return ApiResponse(
            success=True,
            message="No mission currently active",
            data={
                "mission_id": mission_id,
                "status": "inactive",
                "mission_active": False
            }
        )
    
    try:
        logger.info(f"⏸️ Stopping mission: {mission_id}")
        
        # Stop the mission (switches to LOITER/HOLD mode)
        success = sim_controller.stop_mission(hold_position=True)
        
        if success:
            drone_state["mission_active"] = False
            
            # Create event
            create_event(
                EventType.MISSION.value,
                "mission_control",
                {
                    "command": "stop_mission",
                    "mission_id": mission_id,
                    "last_waypoint": drone_state["mission_current"],
                    "total_waypoints": drone_state["mission_count"],
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            logger.info(f"✅ Mission {mission_id} stopped successfully")
            
            return ApiResponse(
                success=True,
                message=f"Mission {mission_id} stopped successfully",
                data={
                    "mission_id": mission_id,
                    "status": "stopped",
                    "mission_active": False,
                    "last_waypoint": drone_state["mission_current"],
                    "total_waypoints": drone_state["mission_count"],
                    "timestamp": datetime.now().isoformat()
                }
            )
        else:
            logger.error(f"STOP MISSION failed: stop_mission() returned False")
            raise HTTPException(
                status_code=500,
                detail="Failed to stop mission. Check PX4 console for errors."
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ STOP MISSION error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Mission stop failed: {str(e)}"
        )


# ============================================================================
# ADDITIONAL HELPER: Mission Status Endpoint
# ============================================================================

@app.get("/api/v1/missions/{mission_id}/status", response_model=ApiResponse, tags=["Mission Control"])
async def get_mission_status(mission_id: str):
    """
    📊 GET MISSION STATUS
    
    Get current status of a mission.
    
    **Path Parameters:**
    - mission_id: Mission identifier
    
    **Returns:**
    - Mission status information
    """
    global sim_controller, drone_state
    
    if not sim_controller or not sim_controller._is_connected:
        raise HTTPException(
            status_code=503,
            detail="Not connected to simulator"
        )
    
    try:
        await update_drone_state_from_telemetry()
        
        return ApiResponse(
            success=True,
            message="Mission status",
            data={
                "mission_id": mission_id,
                "mission_active": drone_state["mission_active"],
                "current_waypoint": drone_state["mission_current"],
                "total_waypoints": drone_state["mission_count"],
                "progress_percent": (drone_state["mission_current"] / drone_state["mission_count"] * 100) 
                                   if drone_state["mission_count"] > 0 else 0,
                "vehicle_armed": drone_state["armed"],
                "vehicle_flying": drone_state["flying"],
                "flight_mode": drone_state["flight_mode"],
                "timestamp": datetime.now().isoformat()
            }
        )
    
    except Exception as e:
        logger.error(f"Error getting mission status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get mission status: {str(e)}"
        )


# ============================================================================
# VEHICLE COMMAND ENDPOINTS (CORRECTED VERSION)
# ============================================================================

@app.post("/api/v1/vehicle/arm", response_model=ApiResponse, tags=["Vehicle Commands"])
async def arm_vehicle(request: VehicleArmRequest):
    """
    🔒 ARM VEHICLE
    
    Arms the vehicle motors, preparing it for flight.
    
    **Prerequisites:**
    - Connected to simulator
    - GPS lock acquired
    - Pre-flight checks passed (unless force_arm=true)
    
    **Request Body:**
    - mission_id: Mission identifier
    - force_arm: Force arming even if pre-arm checks fail (use with caution)
    
    **Returns:**
    - Success response with armed status
    
    **Example:**
    ```json
    {
        "mission_id": "MISSION-001",
        "force_arm": false
    }
    ```
    """
    global sim_controller, drone_state
    
    # Check if connected
    if not sim_controller or not sim_controller._is_connected:
        logger.error("ARM failed: Not connected to simulator")
        raise HTTPException(
            status_code=503, 
            detail="Not connected to simulator. Ensure PX4 SITL is running."
        )
    
    # Check if already armed
    if drone_state["armed"] and not request.force_arm:
        logger.info(f"Vehicle already armed for mission {request.mission_id}")
        return ApiResponse(
            success=True,
            message="Vehicle already armed",
            data={
                "armed": True, 
                "mission_id": request.mission_id
            }
        )
    
    try:
        logger.info(f"🔒 Arming vehicle for mission {request.mission_id} (force={request.force_arm})")
        
        # Wait for position lock
        if not sim_controller.wait_for_position(timeout=10):
            logger.error("ARM failed: No GPS position available")
            raise HTTPException(
                status_code=400, 
                detail="GPS position not available. Check GPS fix."
            )
        
        # Wait for pre-arm checks unless forcing
        if not request.force_arm:
            logger.info("Waiting for pre-arm checks...")
            if not sim_controller.wait_until_ready_to_arm(timeout=30):
                logger.warning("Pre-arm checks not passed, but continuing with force_arm")
                if not request.force_arm:
                    raise HTTPException(
                        status_code=400,
                        detail="Pre-arm checks failed. Use force_arm=true to override."
                    )
        
        # Arm the vehicle
        success = sim_controller.arm_vehicle(force=request.force_arm)
        
        if success:
            drone_state["armed"] = True
            
            # Create event
            create_event(
                EventType.VEHICLE.value,
                "vehicle_command",
                {
                    "command": "arm",
                    "mission_id": request.mission_id,
                    "force": request.force_arm,
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            logger.info(f"✅ Vehicle armed successfully for mission {request.mission_id}")
            
            return ApiResponse(
                success=True,
                message="Vehicle armed successfully",
                data={
                    "armed": True,
                    "mission_id": request.mission_id,
                    "force_used": request.force_arm,
                    "timestamp": datetime.now().isoformat()
                }
            )
        else:
            logger.error("ARM command failed")
            raise HTTPException(
                status_code=500, 
                detail="Failed to arm vehicle. Check PX4 console for pre-arm check failures."
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Arm vehicle error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Arm command failed: {str(e)}"
        )


@app.post("/api/v1/vehicle/disarm", response_model=ApiResponse, tags=["Vehicle Commands"])
async def disarm_vehicle(request: VehicleDisarmRequest):
    """
    🔓 DISARM VEHICLE
    
    Disarms the vehicle motors. Vehicle must be on the ground.
    
    **Prerequisites:**
    - Vehicle must be landed (not flying)
    
    **Request Body:**
    - mission_id: Mission identifier
    
    **Returns:**
    - Success response with disarmed status
    
    **Example:**
    ```json
    {
        "mission_id": "MISSION-001"
    }
    ```
    """
    global sim_controller, drone_state
    
    # Check if connected
    if not sim_controller or not sim_controller._is_connected:
        logger.error("DISARM failed: Not connected to simulator")
        raise HTTPException(
            status_code=503, 
            detail="Not connected to simulator"
        )
    
    # Check if flying
    if drone_state["flying"]:
        logger.error("DISARM failed: Vehicle is flying")
        raise HTTPException(
            status_code=400, 
            detail="Cannot disarm while flying. Land the vehicle first."
        )
    
    # Check if already disarmed
    if not drone_state["armed"]:
        logger.info(f"Vehicle already disarmed for mission {request.mission_id}")
        return ApiResponse(
            success=True,
            message="Vehicle already disarmed",
            data={
                "armed": False, 
                "mission_id": request.mission_id
            }
        )
    
    try:
        logger.info(f"🔓 Disarming vehicle for mission {request.mission_id}")
        
        success = sim_controller.disarm_vehicle(force=False)
        
        if success:
            drone_state["armed"] = False
            
            # Create event
            create_event(
                EventType.VEHICLE.value,
                "vehicle_command",
                {
                    "command": "disarm",
                    "mission_id": request.mission_id,
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            logger.info(f"✅ Vehicle disarmed successfully for mission {request.mission_id}")
            
            return ApiResponse(
                success=True,
                message="Vehicle disarmed successfully",
                data={
                    "armed": False,
                    "mission_id": request.mission_id,
                    "timestamp": datetime.now().isoformat()
                }
            )
        else:
            logger.error("DISARM command failed")
            raise HTTPException(
                status_code=500, 
                detail="Failed to disarm vehicle"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Disarm vehicle error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Disarm command failed: {str(e)}"
        )


@app.post("/api/v1/vehicle/takeoff", response_model=ApiResponse, tags=["Vehicle Commands"])
async def takeoff_vehicle(request: TakeoffRequest):
    """
    🚁 TAKEOFF VEHICLE
    """
    global sim_controller, drone_state
    
    if not sim_controller or not sim_controller._is_connected:
        raise HTTPException(status_code=503, detail="Not connected to simulator")
    
    # Check if armed
    await update_drone_state_from_telemetry()
    
    if not drone_state["armed"]:
        raise HTTPException(
            status_code=400,
            detail="Vehicle must be armed before takeoff. Arm the vehicle first."
        )
    
    try:
        logger.info(f"🚁 Initiating takeoff to {request.altitude}m for mission {request.mission_id}")
        
        # Call the fixed takeoff method
        success = sim_controller.takeoff(request.altitude)
        
        if success:
            drone_state["flying"] = True
            
            return ApiResponse(
                success=True,
                message=f"Takeoff successful - reached {request.altitude}m",
                data={
                    "altitude": request.altitude,
                    "mission_id": request.mission_id,
                    "flying": True,
                    "timestamp": datetime.now().isoformat()
                }
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Takeoff failed - altitude not reached. Check PX4 console for errors."
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Takeoff error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Takeoff failed: {str(e)}")


@app.post("/api/v1/vehicle/land", response_model=ApiResponse, tags=["Vehicle Commands"])
async def land_vehicle(request: LandRequest):
    """
    🛬 LAND
    
    Commands the vehicle to land at the current position.
    
    **Request Body:**
    - mission_id: Mission identifier
    
    **Returns:**
    - Success response with landing status
    
    **Example:**
    ```json
    {
        "mission_id": "MISSION-001"
    }
    ```
    """
    global sim_controller, drone_state
    
    # Check if connected
    if not sim_controller or not sim_controller._is_connected:
        logger.error("LAND failed: Not connected to simulator")
        raise HTTPException(
            status_code=503, 
            detail="Not connected to simulator"
        )
    
    # Check if already landed
    if not drone_state["flying"]:
        logger.info(f"Vehicle already on ground for mission {request.mission_id}")
        return ApiResponse(
            success=True,
            message="Vehicle already on ground",
            data={
                "flying": False, 
                "mission_id": request.mission_id
            }
        )
    
    try:
        logger.info(f"🛬 Initiating landing for mission {request.mission_id}")
        
        success = sim_controller.land()
        
        if success:
            drone_state["flying"] = False
            drone_state["mission_active"] = False
            
            # Create event
            create_event(
                EventType.VEHICLE.value,
                "vehicle_command",
                {
                    "command": "land",
                    "mission_id": request.mission_id,
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            logger.info(f"✅ Landing command sent for mission {request.mission_id}")
            
            return ApiResponse(
                success=True,
                message="Landing initiated",
                data={
                    "mission_id": request.mission_id,
                    "landing": True,
                    "flying": False,
                    "mission_active": False,
                    "timestamp": datetime.now().isoformat()
                }
            )
        else:
            logger.error("LAND command failed")
            raise HTTPException(
                status_code=500, 
                detail="Failed to initiate landing"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Land error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Land command failed: {str(e)}"
        )


@app.post("/api/v1/vehicle/rtl", response_model=ApiResponse, tags=["Vehicle Commands"])
async def return_to_launch_vehicle(request: RTLRequest):
    """
    🏠 RETURN TO LAUNCH (RTL)
    
    Commands the vehicle to return to the launch position and land.
    
    **The vehicle will:**
    1. Climb to RTL altitude (if below)
    2. Return to home position
    3. Land at home
    
    **Prerequisites:**
    - Vehicle must be flying
    
    **Request Body:**
    - mission_id: Mission identifier
    
    **Returns:**
    - Success response with RTL status
    
    **Example:**
    ```json
    {
        "mission_id": "MISSION-001"
    }
    ```
    """
    global sim_controller, drone_state
    
    # Check if connected
    if not sim_controller or not sim_controller._is_connected:
        logger.error("RTL failed: Not connected to simulator")
        raise HTTPException(
            status_code=503, 
            detail="Not connected to simulator"
        )
    
    # Check if flying
    if not drone_state["flying"]:
        logger.error("RTL failed: Vehicle not flying")
        raise HTTPException(
            status_code=400, 
            detail="Vehicle must be flying to execute RTL. Takeoff first."
        )
    
    try:
        logger.info(f"🏠 Initiating Return to Launch for mission {request.mission_id}")
        
        success = sim_controller.return_to_launch(timeout=30.0)
        
        if success:
            drone_state["mission_active"] = False
            
            # Create event
            create_event(
                EventType.VEHICLE.value,
                "vehicle_command",
                {
                    "command": "rtl",
                    "mission_id": request.mission_id,
                    "timestamp": datetime.now().isoformat()
                }
            )
            
            logger.info(f"✅ RTL command sent - returning to home position")
            
            return ApiResponse(
                success=True,
                message="Return to launch initiated",
                data={
                    "mission_id": request.mission_id,
                    "rtl": True,
                    "mission_active": False,
                    "home_position": drone_state["home_position"],
                    "timestamp": datetime.now().isoformat()
                }
            )
        else:
            logger.error("RTL command failed")
            raise HTTPException(
                status_code=500, 
                detail="Failed to initiate RTL"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ RTL error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"RTL command failed: {str(e)}"
        )


# ============================================================================
# END OF VEHICLE COMMAND ENDPOINTS
# ============================================================================

# Connection & Status endpoints
@app.post("/connect", response_model=ApiResponse)
async def connect():
    """Connect to Gazebo simulator"""
    global sim_controller, drone_state
    
    if not sim_controller:
        raise HTTPException(status_code=500, detail="Simulator not initialized")
    
    try:
        if sim_controller.connect():
            drone_state["connected"] = True
            
            if sim_controller.wait_for_position(timeout=10):
                await update_drone_state_from_telemetry()
                return ApiResponse(
                    success=True,
                    message="Connected to PX4 SITL with GPS position",
                    data={
                        "vehicle": drone_state["vehicle_info"],
                        "position": drone_state["current_position"]
                    }
                )
            else:
                return ApiResponse(success=True, message="Connected to PX4 SITL (waiting for GPS)")
        else:
            raise HTTPException(status_code=500, detail="Connection failed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Vehicle Information
@app.get("/vehicle/info", response_model=ApiResponse)
async def get_vehicle_info():
    """Get vehicle information"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    await update_drone_state_from_telemetry()
    
    capabilities = ["guided_mode", "auto_mode", "stabilize_mode", "custom_modes"]
    
    vehicle_info = VehicleInfo(
        type=drone_state["vehicle_info"]["type"],
        autopilot=drone_state["vehicle_info"]["autopilot"],
        version=drone_state["vehicle_info"].get("version", "1.0"),
        system_id=drone_state["vehicle_info"]["system_id"],
        component_id=drone_state["vehicle_info"]["component_id"],
        capabilities=capabilities
    )
    
    return ApiResponse(success=True, message="Vehicle info", data=vehicle_info.dict())

@app.get("/vehicle/capabilities", response_model=ApiResponse)
async def get_vehicle_capabilities():
    """Get vehicle capabilities"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    caps = {
        "can_arm": True,
        "can_takeoff": True,
        "can_land": True,
        "can_rtl": True,
        "supports_missions": True,
        "supports_guided_mode": True,
        "supports_geofencing": True,
        "max_altitude": 120,
        "max_speed": 20,
        "max_range": 1000,
        "flight_modes": ["MANUAL", "ALTCTL", "POSCTL", "AUTO", "MISSION", "RTL", "LOITER", "OFFBOARD"]
    }
    
    return ApiResponse(success=True, message="Capabilities", data=caps)

# Mission endpoints - Basic
@app.post("/mission/upload", response_model=ApiResponse)
async def upload_mission(mission: MissionRequest):
    """Upload mission waypoints to drone"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected to simulator")
    
    try:
        # Convert Pydantic models to dict format
        waypoints = [
            {"lat": wp.lat, "lon": wp.lon, "alt": wp.alt}
            for wp in mission.waypoints
        ]
        
        logger.info(f"📤 Uploading {len(waypoints)} waypoints to PX4")
        
        if sim_controller.upload_mission(waypoints):
            drone_state["mission_count"] = len(waypoints)
            create_event(
                EventType.MISSION.value,
                "api",
                {"action": "upload", "waypoint_count": len(waypoints)}
            )
            return ApiResponse(
                success=True,
                message=f"Mission uploaded successfully ({len(waypoints)} waypoints)",
                data={"waypoint_count": len(waypoints)}
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Mission upload failed - check PX4 connection"
            )
    except Exception as e:
        logger.error(f"Mission upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/mission/orbit", response_model=ApiResponse)
async def create_orbit_mission(request: OrbitMissionRequest):
    """Create circular orbit mission"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    try:
        waypoints = generate_orbit_waypoints(
            request.center_lat, request.center_lon, request.altitude,
            request.radius, request.num_points, request.clockwise, request.pattern_type
        )
        
        mission_id = f"ORBIT-{uuid.uuid4().hex[:8].upper()}"
        mission_templates[mission_id] = {
            "id": mission_id,
            "name": f"Orbit at ({request.center_lat:.6f}, {request.center_lon:.6f})",
            "type": "orbit",
            "waypoints": waypoints,
            "metadata": {"center": {"lat": request.center_lat, "lon": request.center_lon}, "radius": request.radius},
            "created_at": datetime.now().isoformat()
        }
        
        return ApiResponse(
            success=True,
            message=f"Orbit mission created",
            data={"mission_id": mission_id, "waypoint_count": len(waypoints), "waypoints": waypoints}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mission endpoints - Advanced
@app.post("/mission/survey", response_model=ApiResponse)
async def create_survey_mission(request: SurveyMissionRequest):
    """Create area survey mission"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    try:
        waypoints = generate_survey_waypoints(request.polygon, request.altitude, request.grid_spacing, request.overlap)
        
        mission_id = f"SURVEY-{uuid.uuid4().hex[:8].upper()}"
        mission_templates[mission_id] = {
            "id": mission_id,
            "name": request.name,
            "type": MissionTemplateType.SURVEY.value,
            "waypoints": waypoints,
            "metadata": {"polygon": request.polygon, "grid_spacing": request.grid_spacing},
            "created_at": datetime.now().isoformat()
        }
        
        create_event(EventType.MISSION.value, "mission_planner", {"mission_id": mission_id, "type": "survey"})
        
        return ApiResponse(success=True, message=f"Survey created: {request.name}", data={"mission_id": mission_id, "waypoint_count": len(waypoints), "waypoints": waypoints})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/mission/corridor", response_model=ApiResponse)
async def create_corridor_mission(request: CorridorMissionRequest):
    """Create corridor inspection mission"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    try:
        waypoints = generate_corridor_waypoints(
            request.start_lat, request.start_lon, request.end_lat, request.end_lon,
            request.altitude, request.width, request.segments
        )
        
        mission_id = f"CORRIDOR-{uuid.uuid4().hex[:8].upper()}"
        mission_templates[mission_id] = {
            "id": mission_id,
            "name": request.name,
            "type": MissionTemplateType.CORRIDOR.value,
            "waypoints": waypoints,
            "metadata": {"start": {"lat": request.start_lat, "lon": request.start_lon}, "end": {"lat": request.end_lat, "lon": request.end_lon}},
            "created_at": datetime.now().isoformat()
        }
        
        create_event(EventType.MISSION.value, "mission_planner", {"mission_id": mission_id, "type": "corridor"})
        
        return ApiResponse(success=True, message=f"Corridor created: {request.name}", data={"mission_id": mission_id, "waypoint_count": len(waypoints), "waypoints": waypoints})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/mission/structure_scan", response_model=ApiResponse)
async def create_structure_scan(request: StructureScanRequest):
    """Create 3D structure scan mission"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    try:
        waypoints = generate_structure_scan_waypoints(
            request.center_lat, request.center_lon, request.altitude_min, request.altitude_max,
            request.radius, request.orbits, request.points_per_orbit
        )
        
        mission_id = f"STRUCT-{uuid.uuid4().hex[:8].upper()}"
        mission_templates[mission_id] = {
            "id": mission_id,
            "name": request.name,
            "type": MissionTemplateType.STRUCTURE_SCAN.value,
            "waypoints": waypoints,
            "metadata": {"center": {"lat": request.center_lat, "lon": request.center_lon}, "radius": request.radius},
            "created_at": datetime.now().isoformat()
        }
        
        create_event(EventType.MISSION.value, "mission_planner", {"mission_id": mission_id, "type": "structure_scan"})
        
        return ApiResponse(success=True, message=f"Structure scan created: {request.name}", data={"mission_id": mission_id, "waypoint_count": len(waypoints), "waypoints": waypoints})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/mission/perimeter", response_model=ApiResponse)
async def create_perimeter_mission(request: PerimeterMissionRequest):
    """Create perimeter patrol mission"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    try:
        waypoints = [{"lat": lat, "lon": lon, "alt": request.altitude} for lat, lon in request.polygon]
        if waypoints:
            waypoints.append(waypoints[0])
        
        mission_id = f"PERIMETER-{uuid.uuid4().hex[:8].upper()}"
        mission_templates[mission_id] = {
            "id": mission_id,
            "name": request.name,
            "type": MissionTemplateType.PERIMETER.value,
            "waypoints": waypoints,
            "metadata": {"polygon": request.polygon},
            "created_at": datetime.now().isoformat()
        }
        
        return ApiResponse(success=True, message=f"Perimeter created: {request.name}", data={"mission_id": mission_id, "waypoint_count": len(waypoints), "waypoints": waypoints})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/mission/templates", response_model=ApiResponse)
async def list_mission_templates():
    """List all mission templates"""
    templates = [{"id": t["id"], "name": t["name"], "type": t["type"], "waypoint_count": len(t["waypoints"]), "created_at": t["created_at"]} for t in mission_templates.values()]
    return ApiResponse(success=True, message=f"Found {len(templates)} templates", data={"templates": templates})

@app.get("/mission/template/{mission_id}", response_model=ApiResponse)
async def get_mission_template(mission_id: str):
    """Get mission template details"""
    template = mission_templates.get(mission_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return ApiResponse(success=True, message="Mission template", data=template)

@app.post("/mission/validate", response_model=ApiResponse)
async def validate_mission_endpoint(request: MissionValidationRequest):
    """Validate mission feasibility"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    try:
        if request.mission_id:
            template = mission_templates.get(request.mission_id)
            if not template:
                raise HTTPException(status_code=404, detail="Mission not found")
            waypoints = template["waypoints"]
        elif request.waypoints:
            waypoints = [{"lat": wp.lat, "lon": wp.lon, "alt": wp.alt} for wp in request.waypoints]
        else:
            raise HTTPException(status_code=400, detail="Provide mission_id or waypoints")
        
        vehicle_caps = {"max_altitude": 120, "max_range": 5000, "cruise_speed": 10, "endurance": 1800}
        validation = validate_mission_feasibility(waypoints, vehicle_caps)
        
        return ApiResponse(success=validation["valid"], message="Validation complete", data=validation)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/mission/upload_template/{mission_id}", response_model=ApiResponse)
async def upload_mission_from_template(mission_id: str):
    """Upload mission from template to drone"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    template = mission_templates.get(mission_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    try:
        waypoints = template["waypoints"]
        
        if sim_controller.upload_mission(waypoints):
            drone_state["mission_count"] = len(waypoints)
            create_event(EventType.MISSION.value, "executor", {"mission_id": mission_id, "action": "uploaded"})
            return ApiResponse(success=True, message=f"Uploaded: {template['name']}", data={"mission_id": mission_id, "waypoint_count": len(waypoints)})
        raise HTTPException(status_code=500, detail="Upload failed")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Geofencing endpoints
@app.post("/geofence/circle", response_model=ApiResponse)
async def create_circle_geofence(geofence: GeofenceCircle):
    """Create circular geofence"""
    fence_dict = geofence.dict()
    fence_dict["shape"] = "circle"
    fence_dict["id"] = len(geofences)
    geofences.append(fence_dict)
    return ApiResponse(success=True, message=f"Created: {geofence.name}", data={"id": fence_dict["id"]})

@app.post("/geofence/polygon", response_model=ApiResponse)
async def create_polygon_geofence(geofence: GeofencePolygon):
    """Create polygon geofence"""
    fence_dict = geofence.dict()
    fence_dict["shape"] = "polygon"
    fence_dict["id"] = len(geofences)
    geofences.append(fence_dict)
    return ApiResponse(success=True, message=f"Created: {geofence.name}", data={"id": fence_dict["id"]})

@app.post("/geofence/cylinder", response_model=ApiResponse)
async def create_cylinder_geofence(geofence: GeofenceCylinder):
    """Create cylindrical geofence"""
    fence_dict = geofence.dict()
    fence_dict["shape"] = "cylinder"
    fence_dict["id"] = len(geofences)
    geofences.append(fence_dict)
    return ApiResponse(success=True, message=f"Created: {geofence.name}", data={"id": fence_dict["id"]})

@app.get("/geofence/list", response_model=ApiResponse)
async def list_geofences():
    """List all geofences"""
    return ApiResponse(success=True, message=f"{len(geofences)} geofences", data={"geofences": geofences})

@app.delete("/geofence/{fence_id}", response_model=ApiResponse)
async def delete_geofence(fence_id: int):
    """Delete geofence"""
    for i, fence in enumerate(geofences):
        if fence["id"] == fence_id:
            deleted = geofences.pop(i)
            return ApiResponse(success=True, message=f"Deleted: {deleted['name']}")
    raise HTTPException(status_code=404, detail="Not found")

@app.put("/geofence/{fence_id}/enable", response_model=ApiResponse)
async def enable_geofence(fence_id: int, enabled: bool = True):
    """Enable/disable geofence"""
    for fence in geofences:
        if fence["id"] == fence_id:
            fence["enabled"] = enabled
            return ApiResponse(success=True, message=f"{'Enabled' if enabled else 'Disabled'}")
    raise HTTPException(status_code=404, detail="Not found")

@app.post("/geofence/check", response_model=ApiResponse)
async def check_geofence(lat: float, lon: float, alt: float):
    """Check position against geofences"""
    violated, msg = check_geofence_violation(lat, lon, alt)
    return ApiResponse(success=not violated, message=msg if violated else "Safe", data={"violated": violated})

@app.delete("/geofence/clear", response_model=ApiResponse)
async def clear_geofences():
    """Clear all geofences"""
    global geofences
    count = len(geofences)
    geofences = []
    return ApiResponse(success=True, message=f"Cleared {count} geofences")

# Parameters endpoints
@app.post("/parameter/set", response_model=ApiResponse)
async def set_parameter(param: ParameterSet):
    """Set parameter"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    try:
        sim_controller.mav_connection.mav.param_set_send(
            sim_controller.target_system, sim_controller.target_component,
            param.name.encode('utf-8'), param.value, mavutil.mavlink.MAV_PARAM_TYPE_REAL32
        )
        parameters_cache[param.name] = param.value
        return ApiResponse(success=True, message=f"Set {param.name}={param.value}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/parameter/get", response_model=ApiResponse)
async def get_parameter(param: ParameterGet):
    """Get parameter"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    
    try:
        sim_controller.mav_connection.mav.param_request_read_send(
            sim_controller.target_system, sim_controller.target_component,
            param.name.encode('utf-8'), -1
        )
        msg = sim_controller.mav_connection.recv_match(type='PARAM_VALUE', blocking=True, timeout=5)
        if msg:
            return ApiResponse(success=True, message=param.name, data={"value": msg.param_value})
        raise HTTPException(status_code=404, detail="Not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/parameter/list", response_model=ApiResponse)
async def list_parameters():
    """List cached parameters"""
    return ApiResponse(success=True, message=f"{len(parameters_cache)} parameters", data=parameters_cache)

# Telemetry & Camera
@app.get("/telemetry", response_model=TelemetryData)
async def get_telemetry():
    """Get full telemetry"""
    if not drone_state["connected"]:
        raise HTTPException(status_code=400, detail="Not connected")
    return TelemetryData(**sim_controller.get_telemetry())

@app.post("/camera/take_photo", response_model=ApiResponse)
async def take_photo():
    """Take photo"""
    drone_state["sensors"]["camera"]["photo_count"] += 1
    return ApiResponse(success=True, message="Photo captured", data={"count": drone_state["sensors"]["camera"]["photo_count"]})

@app.post("/camera/start_video", response_model=ApiResponse)
async def start_video(quality: int = 1):
    """Start video"""
    drone_state["sensors"]["camera"]["recording"] = True
    return ApiResponse(success=True, message="Video started")

@app.post("/camera/stop_video", response_model=ApiResponse)
async def stop_video():
    """Stop video"""
    drone_state["sensors"]["camera"]["recording"] = False
    return ApiResponse(success=True, message="Video stopped")

# WebSocket Real-time Streaming
@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    """Real-time telemetry WebSocket"""
    await websocket.accept()
    logger.info("WebSocket client connected")
    
    try:
        while True:
            if drone_state["connected"]:
                await update_drone_state_from_telemetry()
                pos = drone_state["current_position"]
                violated, msg = check_geofence_violation(pos["lat"], pos["lon"], pos["alt"])
                
                data = {
                    "timestamp": datetime.now().isoformat(),
                    "drone_state": drone_state,
                    "telemetry": sim_controller.get_telemetry() if sim_controller else {},
                    "geofence_violation": violated,
                    "geofence_message": msg
                }
                await websocket.send_json(data)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")

# SkyrouteX-Client Compatible Endpoints
@app.post("/api/v1/missions/upload-to-px4/{mission_id}", response_model=ApiResponse)
async def upload_mission_to_px4(mission_id: str, mission: SkyrouteXMissionUpload):
    """
    Upload mission to PX4 Gazebo - Compatible with skyroutex-client
    
    This endpoint matches the format expected by the QT client:
    POST /api/v1/missions/upload-to-px4/{mission_id}
    """
    if not sim_controller or not sim_controller._is_connected:
        # Try to connect if not connected
        if not sim_controller:
            raise HTTPException(
                status_code=503, 
                detail="Simulator controller not initialized"
            )
        
        # Try to connect with provided connection string
        connection_url = mission.connection_string or "udp:127.0.0.1:14540"
        
        # Update config if different
        if sim_controller.config.connection_url != connection_url:
            sim_controller.config.connection_url = connection_url
        
        if not sim_controller.connect():
            raise HTTPException(
                status_code=503,
                detail="Not connected to PX4. Ensure Gazebo SITL is running."
            )
    
    try:
        logger.info(f"📤 Uploading mission {mission_id} to PX4 Gazebo")
        logger.info(f"   Vehicle: {mission.vehicle_id}")
        logger.info(f"   Waypoints: {len(mission.waypoints)}")
        logger.info(f"   Connection: {mission.connection_string}")
        
        print("waypoints", mission.waypoints)
        
        # Validate waypoints format
        waypoints = []
        for i, wp in enumerate(mission.waypoints):
            if 'latitude' in wp and 'longitude' in wp:
                # Format: {'latitude': ..., 'longitude': ..., 'altitude': ...}
                waypoints.append({
                    'lat': float(wp['latitude']),
                    'lon': float(wp['longitude']),
                    'alt': float(wp.get('altitude', wp.get('alt', 10)))
                })
            elif 'lat' in wp and 'lon' in wp:
                # Format: {'lat': ..., 'lon': ..., 'alt': ...}
                waypoints.append({
                    'lat': float(wp['lat']),
                    'lon': float(wp['lon']),
                    'alt': float(wp.get('alt', 10))
                })
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid waypoint format at index {i}: {wp}"
                )
        
        if not waypoints:
            raise HTTPException(
                status_code=400,
                detail="No valid waypoints provided"
            )
        
        # Upload mission to PX4 via PyMAVLink
        if sim_controller.upload_mission(waypoints):
            # Update drone state
            drone_state["mission_count"] = len(waypoints)
            
            # Create event
            create_event(
                EventType.MISSION.value,
                "skyroutex_client",
                {
                    "mission_id": mission_id,
                    "vehicle_id": mission.vehicle_id,
                    "action": "upload",
                    "waypoint_count": len(waypoints)
                }
            )
            
            logger.info(f"✅ Mission {mission_id} uploaded successfully to PX4 Gazebo")
            
            return ApiResponse(
                success=True,
                message=f"Mission uploaded to PX4 successfully",
                data={
                    "mission_id": mission_id,
                    "vehicle_id": mission.vehicle_id,
                    "waypoint_count": len(waypoints),
                    "status": "uploaded",
                    "details": f"Mission with {len(waypoints)} waypoints uploaded to PX4 Gazebo"
                }
            )
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to upload mission to PX4. Check PX4 console for errors."
            )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Mission upload error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Mission upload failed: {str(e)}"
        )

@app.get("/api/v1/missions/{mission_id}", response_model=ApiResponse)
async def get_mission_from_redis(mission_id: str):
    """
    Get mission details from Redis storage
    Compatible with skyroutex-client mission retrieval
    """
    if not sim_controller or not sim_controller.redis_client:
        raise HTTPException(
            status_code=503,
            detail="Redis storage not available"
        )
    
    try:
        mission_data = sim_controller.redis_client.hgetall(f'mission:{mission_id}')
        
        if not mission_data:
            raise HTTPException(
                status_code=404,
                detail=f"Mission {mission_id} not found"
            )
        
        # Parse waypoints back from JSON
        waypoints = json.loads(mission_data['waypoints'])
        
        return ApiResponse(
            success=True,
            message="Mission retrieved",
            data={
                "mission_id": mission_data['mission_id'],
                "vehicle_id": mission_data['vehicle_id'],
                "waypoints": waypoints,
                "waypoint_count": int(mission_data['count']),
                "uploaded_at": mission_data['uploaded_at'],
                "source": mission_data.get('source', 'unknown')
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Mission upload error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Mission upload failed: {str(e)}"
        )
    
    # ==================== In-Memory Session Store ====================

# Redis Mission & Telemetry APIs
@app.get("/api/health")
async def api_health():
    """Global health check"""
    active_sessions = 0
    if session_manager:
        active_sessions = len([
            s for s in session_manager.sessions.values() 
            if s["is_active"]
        ])
    
    return {
        "status": "healthy",
        "api_version": "v1",
        "active_connections": active_sessions
    }

@app.get("/api/status")
@app.get("/status", response_model=ApiResponse, tags=["Status"])
async def get_status():
    """
    📊 GET DRONE STATUS
    
    Returns comprehensive drone status including armed state, connection status,
    position, battery, and mission information.
    """
    global sim_controller, drone_state
    
    try:
        # Update state from telemetry before returning
        if sim_controller and sim_controller._is_connected:
            await update_drone_state_from_telemetry()
        
        # Return the complete drone state
        return ApiResponse(
            success=True,
            message="Drone status retrieved successfully",
            data={
                "connected": drone_state["connected"],
                "armed": drone_state["armed"],
                "flying": drone_state["flying"],
                "current_position": drone_state["current_position"],
                "home_position": drone_state["home_position"],
                "battery_level": drone_state["battery_level"],
                "flight_mode": drone_state["flight_mode"],
                "mission_active": drone_state["mission_active"],
                "mission_current": drone_state["mission_current"],
                "mission_count": drone_state["mission_count"],
                "last_update": drone_state.get("last_update", datetime.now().isoformat()),
                "message": "Status OK" if drone_state["connected"] else "Not connected"
            }
        )
    
    except Exception as e:
        logger.error(f"Error getting status: {e}", exc_info=True)
        return ApiResponse(
            success=False,
            message=f"Error retrieving status: {str(e)}",
            data={
                "connected": False,
                "armed": False,
                "flying": False,
                "current_position": {"lat": 0, "lon": 0, "alt": 0},
                "home_position": {"lat": 0, "lon": 0, "alt": 0},
                "battery_level": 0,
                "flight_mode": "UNKNOWN",
                "mission_active": False,
                "mission_current": 0,
                "mission_count": 0,
                "last_update": datetime.now().isoformat(),
                "message": "Status error"
            }
        )

@app.get("/api/missions")
async def get_missions():
    """Get latest 50 missions with details"""
    try:
        all_keys = []
        cursor = 0
        
        # Scan for all mission keys
        while True:
            cursor, keys = redis_client.scan(cursor=cursor, match="mavlink:missions:*", count=100)
            all_keys.extend(keys)
            if cursor == 0:
                break
        
        missions = []
        for key in all_keys:
            try:
                key_type = redis_client.type(key)
                
                # Initialize mission object
                mission = {
                    'id': key.split(':')[-1] if ':' in key else key,
                    'key': key,
                    'type': key_type,
                    'vehicle_id': 'unknown',
                    'status': 'unknown',
                    'waypoint_count': 0,
                    'created_at': None,
                    'timestamp': 0,
                    'waypoints': []
                }
                
                # Handle different Redis data types
                if key_type == 'string':
                    # Mission data stored as JSON string
                    try:
                        data_str = redis_client.get(key)
                        data = json.loads(data_str)
                        
                        # Extract mission_id
                        if 'mission_id' in data:
                            mission['id'] = data['mission_id']
                        
                        # Extract vehicle_id from mission_id (format: TEST-MISSION-001_UAV-001_timestamp)
                        if 'mission_id' in data and '_' in data['mission_id']:
                            parts = data['mission_id'].split('_')
                            if len(parts) >= 2:
                                mission['vehicle_id'] = parts[1]
                        
                        # Extract status
                        mission['status'] = data.get('status', 'unknown')
                        
                        # Extract waypoints
                        if 'waypoints' in data:
                            waypoints = data['waypoints']
                            mission['waypoint_count'] = len(waypoints)
                            
                            # Convert waypoints to dashboard format
                            formatted_waypoints = []
                            for i, wp in enumerate(waypoints):
                                formatted_wp = {
                                    'latitude': wp.get('lat', 0),
                                    'longitude': wp.get('lon', 0),
                                    'altitude': wp.get('alt', 0),
                                    'action': 'takeoff' if i == 0 else ('land' if i == len(waypoints)-1 else 'waypoint')
                                }
                                formatted_waypoints.append(formatted_wp)
                            
                            mission['waypoints'] = formatted_waypoints
                        
                        # Extract timestamp
                        if 'uploaded_at' in data:
                            mission['created_at'] = data['uploaded_at']
                            try:
                                dt = datetime.fromisoformat(data['uploaded_at'])
                                mission['timestamp'] = dt.timestamp()
                            except:
                                mission['timestamp'] = 0
                        
                        # Extract waypoint count from data if not already set
                        if 'waypoint_count' in data:
                            mission['waypoint_count'] = data['waypoint_count']
                            
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse JSON for key {key}: {e}")
                        continue
                
                elif key_type == 'hash':
                    # Original format - mission data as hash
                    data = redis_client.hgetall(key)
                    mission['vehicle_id'] = data.get('vehicle_id', 'unknown')
                    mission['status'] = data.get('status', 'unknown')
                    mission['created_at'] = data.get('created_at', None)
                    
                    # Parse timestamp for sorting
                    if mission['created_at']:
                        try:
                            dt = datetime.fromisoformat(mission['created_at'])
                            mission['timestamp'] = dt.timestamp()
                        except:
                            mission['timestamp'] = 0
                    
                    if 'waypoints' in data:
                        try:
                            waypoints = json.loads(data['waypoints'])
                            mission['waypoint_count'] = len(waypoints)
                            mission['waypoints'] = waypoints
                        except:
                            pass
                
                # Only add missions with waypoints
                if mission['waypoint_count'] > 0:
                    missions.append(mission)
                    
            except Exception as e:
                logger.error(f"Error processing mission {key}: {e}")
                continue
        
        # Sort by timestamp (newest first) and get latest 50
        missions.sort(key=lambda x: x['timestamp'], reverse=True)
        latest_missions = missions[:50]
        
        logger.info(f"Found {len(latest_missions)} missions with waypoints")
        
        return JSONResponse({
            'status': 'success',
            'missions': latest_missions,
            'count': len(latest_missions),
            'total': len(missions)
        })
        
    except Exception as e:
        logger.error(f"Error getting missions: {e}")
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        })

@app.get("/api/missions/{mission_id}")
async def get_mission_details(mission_id: str):
    """Get detailed mission data"""
    try:
        # Find mission key
        all_keys = []
        cursor = 0
        
        # Search patterns
        search_patterns = [
            f"mavlink:missions:{mission_id}",
            f"mavlink:missions:*{mission_id}*",
            f"*{mission_id}*"
        ]
        
        for pattern in search_patterns:
            cursor = 0
            pattern_keys = []
            
            while True:
                cursor, keys = redis_client.scan(cursor=cursor, match=pattern, count=100)
                pattern_keys.extend(keys)
                if cursor == 0:
                    break
            
            if pattern_keys:
                all_keys = pattern_keys
                logger.info(f"Found {len(all_keys)} keys matching pattern: {pattern}")
                break
        
        if not all_keys:
            raise HTTPException(status_code=404, detail=f"Mission not found: {mission_id}")
        
        # Use first matching key
        key = all_keys[0]
        
        # Decode key if bytes
        if isinstance(key, bytes):
            key = key.decode('utf-8')
        
        key_type = redis_client.type(key)
        
        # Decode key_type if bytes
        if isinstance(key_type, bytes):
            key_type = key_type.decode('utf-8')
        
        logger.info(f"Processing mission key: {key}, type: {key_type}")
        
        mission_data = {
            'id': mission_id,
            'key': key,
            'type': key_type,
            'waypoints': [],
            'metadata': {}
        }
        
        if key_type == 'string':
            # Mission stored as JSON string
            try:
                data_str = redis_client.get(key)
                
                # Decode if bytes
                if isinstance(data_str, bytes):
                    data_str = data_str.decode('utf-8')
                
                data = json.loads(data_str)
                
                mission_data['metadata'] = data
                
                # Extract and format waypoints
                if 'waypoints' in data:
                    waypoints = data['waypoints']
                    formatted_waypoints = []
                    
                    for i, wp in enumerate(waypoints):
                        if isinstance(wp, dict):
                            formatted_wp = {
                                'latitude': float(wp.get('lat') or wp.get('latitude', 0)),
                                'longitude': float(wp.get('lon') or wp.get('longitude', 0)),
                                'altitude': float(wp.get('alt') or wp.get('altitude', 0)),
                                'action': wp.get('action', 'takeoff' if i == 0 else ('land' if i == len(waypoints)-1 else 'waypoint'))
                            }
                            formatted_waypoints.append(formatted_wp)
                    
                    mission_data['waypoints'] = formatted_waypoints
                    
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from key {key}: {e}")
                raise HTTPException(status_code=500, detail="Failed to parse mission data")
            
        elif key_type == 'zset':
            # Mission stored as sorted set
            try:
                zset_data = redis_client.zrange(key, 0, -1, withscores=True)
                
                logger.info(f"Found {len(zset_data)} items in zset")
                
                mission_data['metadata']['zset_count'] = len(zset_data)
                mission_data['metadata']['data_type'] = 'sorted_set'
                
                formatted_waypoints = []
                
                for member, score in zset_data:
                    try:
                        # Decode if bytes
                        if isinstance(member, bytes):
                            member = member.decode('utf-8')
                        
                        wp_data = json.loads(member)
                        
                        formatted_wp = {
                            'latitude': float(wp_data.get('lat') or wp_data.get('latitude', 0)),
                            'longitude': float(wp_data.get('lon') or wp_data.get('longitude', 0)),
                            'altitude': float(wp_data.get('alt') or wp_data.get('altitude', 0)),
                            'timestamp': score,
                            'action': wp_data.get('action', 'waypoint')
                        }
                        formatted_waypoints.append(formatted_wp)
                        
                    except json.JSONDecodeError:
                        logger.warning(f"Non-JSON member in zset: {member}")
                        continue
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid waypoint data: {member}, error: {e}")
                        continue
                
                # Sort by timestamp
                formatted_waypoints.sort(key=lambda x: x.get('timestamp', 0))
                
                # Assign actions based on position if not set
                for i, wp in enumerate(formatted_waypoints):
                    if wp['action'] == 'waypoint':
                        if i == 0:
                            wp['action'] = 'takeoff'
                        elif i == len(formatted_waypoints) - 1:
                            wp['action'] = 'land'
                
                mission_data['waypoints'] = formatted_waypoints
                logger.info(f"Successfully parsed {len(formatted_waypoints)} waypoints from zset")
                    
            except Exception as e:
                logger.error(f"Failed to parse zset: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to parse mission zset data: {str(e)}")
        
        elif key_type == 'hash':
            # Original hash format
            data = redis_client.hgetall(key)
            
            # Decode hash data
            decoded_data = {}
            for k, v in data.items():
                key_str = k.decode('utf-8') if isinstance(k, bytes) else k
                val_str = v.decode('utf-8') if isinstance(v, bytes) else v
                decoded_data[key_str] = val_str
            
            mission_data['metadata'] = decoded_data
            
            if 'waypoints' in decoded_data:
                try:
                    waypoints = json.loads(decoded_data['waypoints'])
                    formatted_waypoints = []
                    
                    for i, wp in enumerate(waypoints):
                        if isinstance(wp, dict):
                            formatted_wp = {
                                'latitude': float(wp.get('lat') or wp.get('latitude', 0)),
                                'longitude': float(wp.get('lon') or wp.get('longitude', 0)),
                                'altitude': float(wp.get('alt') or wp.get('altitude', 0)),
                                'action': wp.get('action', 'takeoff' if i == 0 else ('land' if i == len(waypoints)-1 else 'waypoint'))
                            }
                            formatted_waypoints.append(formatted_wp)
                    
                    mission_data['waypoints'] = formatted_waypoints
                except Exception as e:
                    logger.error(f"Failed to parse waypoints from hash: {e}")
        
        else:
            logger.warning(f"Unsupported key type: {key_type} for key: {key}")
            raise HTTPException(status_code=400, detail=f"Unsupported mission data type: {key_type}")
        
        logger.info(f"Retrieved mission {mission_id} with {len(mission_data['waypoints'])} waypoints")
        
        return JSONResponse({
            'status': 'success',
            'mission': mission_data
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting mission details for {mission_id}: {e}")
        logger.exception(e)
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        }, status_code=500)

@app.get("/api/telemetry")
async def get_telemetry_sessions():
    """Get all telemetry sessions"""
    try:
        all_keys = []
        cursor = 0
        
        while True:
            cursor, keys = redis_client.scan(cursor=cursor, match="*telemetry*", count=100)
            all_keys.extend(keys)
            if cursor == 0:
                break
        
        sessions = []
        for key in all_keys:
            try:
                key_type = redis_client.type(key)
                parts = key.split(':')[-1].split('_')
                
                session = {
                    'key': key,
                    'type': key_type,
                    'mission_id': parts[0] if len(parts) > 0 else 'unknown',
                    'vehicle_id': parts[1] if len(parts) > 1 else 'unknown',
                    'record_count': 0
                }
                
                if key_type == 'zset':
                    session['record_count'] = redis_client.zcard(key)
                elif key_type == 'list':
                    session['record_count'] = redis_client.llen(key)
                
                sessions.append(session)
            except Exception as e:
                logger.error(f"Error processing telemetry {key}: {e}")
        
        return JSONResponse({
            'status': 'success',
            'sessions': sessions,
            'count': len(sessions)
        })
    except Exception as e:
        logger.error(f"Error getting telemetry: {e}")
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        })

@app.get("/api/telemetry/{session_key:path}")
async def get_telemetry_data(session_key: str):
    """Get telemetry data for a session"""
    try:
        key_type = redis_client.type(session_key)
        
        if key_type == 'none':
            raise HTTPException(status_code=404, detail="Telemetry session not found")
        
        data = []
        
        if key_type == 'zset':
            records = redis_client.zrange(session_key, 0, -1, withscores=False)
            for record in records:
                try:
                    parsed_record = json.loads(record)
                    sanitized_record = sanitize_float_values(parsed_record)
                    data.append(sanitized_record)
                except Exception as e:
                    logger.warning(f"Failed to parse telemetry record: {e}")
                    pass
        elif key_type == 'list':
            records = redis_client.lrange(session_key, 0, -1)
            for record in records:
                try:
                    parsed_record = json.loads(record)
                    sanitized_record = sanitize_float_values(parsed_record)
                    data.append(sanitized_record)
                except Exception as e:
                    logger.warning(f"Failed to parse telemetry record: {e}")
                    pass
        
        return JSONResponse({
            'status': 'success',
            'data': data,
            'count': len(data)
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting telemetry data: {e}")
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        }, status_code=500)
    
@app.get("/api/v1/missions/{mission_id}/details")
async def get_mission_details_client_format(mission_id: str):
    """
    Get mission details in client-compatible format
    
    This endpoint returns mission data in the format expected by the Qt client:
    - Flat structure with mission_id, name, description, created_at, status
    - Waypoints with sequence, latitude, longitude, altitude, command
    """
    try:
        # Find mission key
        all_keys = []
        cursor = 0
        
        # Search patterns
        search_patterns = [
            f"mavlink:missions:{mission_id}",
            f"mavlink:missions:*{mission_id}*",
            f"mission:{mission_id}",
            f"*{mission_id}*"
        ]
        
        for pattern in search_patterns:
            cursor = 0
            pattern_keys = []
            
            while True:
                cursor, keys = redis_client.scan(cursor=cursor, match=pattern, count=100)
                pattern_keys.extend(keys)
                if cursor == 0:
                    break
            
            if pattern_keys:
                all_keys = pattern_keys
                logger.info(f"Found {len(all_keys)} keys matching pattern: {pattern}")
                break
        
        if not all_keys:
            raise HTTPException(status_code=404, detail=f"Mission not found: {mission_id}")
        
        # Use first matching key
        key = all_keys[0]
        
        # Decode key if bytes
        if isinstance(key, bytes):
            key = key.decode('utf-8')
        
        key_type = redis_client.type(key)
        
        # Decode key_type if bytes
        if isinstance(key_type, bytes):
            key_type = key_type.decode('utf-8')
        
        logger.info(f"Processing mission key: {key}, type: {key_type}")
        
        # Initialize client-compatible response structure
        mission_response = {
            'mission_id': mission_id,
            'name': '',
            'description': '',
            'created_at': '',
            'status': 'uploaded',
            'waypoints': []
        }
        
        # Parse different Redis data types
        if key_type == 'string':
            # Mission stored as JSON string
            try:
                data_str = redis_client.get(key)
                
                # Decode if bytes
                if isinstance(data_str, bytes):
                    data_str = data_str.decode('utf-8')
                
                data = json.loads(data_str)
                
                # Extract metadata
                mission_response['mission_id'] = data.get('mission_id', mission_id)
                mission_response['name'] = data.get('name', f'Mission {mission_id}')
                mission_response['description'] = data.get('description', '')
                mission_response['created_at'] = data.get('uploaded_at', data.get('created_at', ''))
                mission_response['status'] = data.get('status', 'uploaded')
                
                # Extract and format waypoints
                if 'waypoints' in data:
                    waypoints = data['waypoints']
                    formatted_waypoints = []
                    
                    for i, wp in enumerate(waypoints):
                        if isinstance(wp, dict):
                            formatted_wp = {
                                'sequence': i,
                                'latitude': float(wp.get('lat') or wp.get('latitude', 0)),
                                'longitude': float(wp.get('lon') or wp.get('longitude', 0)),
                                'altitude': float(wp.get('alt') or wp.get('altitude', 0)),
                                'command': 'WAYPOINT'  # Default command
                            }
                            
                            # Determine action/command based on position
                            if i == 0:
                                formatted_wp['command'] = 'TAKEOFF'
                            elif i == len(waypoints) - 1:
                                formatted_wp['command'] = 'LAND'
                            
                            formatted_waypoints.append(formatted_wp)
                    
                    mission_response['waypoints'] = formatted_waypoints
                    
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from key {key}: {e}")
                raise HTTPException(status_code=500, detail="Failed to parse mission data")
        
        elif key_type == 'hash':
            # Original hash format
            data = redis_client.hgetall(key)
            
            # Decode hash data
            decoded_data = {}
            for k, v in data.items():
                key_str = k.decode('utf-8') if isinstance(k, bytes) else k
                val_str = v.decode('utf-8') if isinstance(v, bytes) else v
                decoded_data[key_str] = val_str
            
            # Extract metadata
            mission_response['mission_id'] = decoded_data.get('mission_id', mission_id)
            mission_response['name'] = decoded_data.get('name', f'Mission {mission_id}')
            mission_response['description'] = decoded_data.get('description', '')
            mission_response['created_at'] = decoded_data.get('uploaded_at', decoded_data.get('created_at', ''))
            mission_response['status'] = decoded_data.get('status', 'uploaded')
            
            # Extract waypoints
            if 'waypoints' in decoded_data:
                try:
                    waypoints = json.loads(decoded_data['waypoints'])
                    formatted_waypoints = []
                    
                    for i, wp in enumerate(waypoints):
                        if isinstance(wp, dict):
                            formatted_wp = {
                                'sequence': i,
                                'latitude': float(wp.get('lat') or wp.get('latitude', 0)),
                                'longitude': float(wp.get('lon') or wp.get('longitude', 0)),
                                'altitude': float(wp.get('alt') or wp.get('altitude', 0)),
                                'command': 'WAYPOINT'
                            }
                            
                            # Determine command based on position
                            if i == 0:
                                formatted_wp['command'] = 'TAKEOFF'
                            elif i == len(waypoints) - 1:
                                formatted_wp['command'] = 'LAND'
                            
                            formatted_waypoints.append(formatted_wp)
                    
                    mission_response['waypoints'] = formatted_waypoints
                except Exception as e:
                    logger.error(f"Failed to parse waypoints from hash: {e}")
        
        elif key_type == 'zset':
            # Mission stored as sorted set
            try:
                zset_data = redis_client.zrange(key, 0, -1, withscores=True)
                
                logger.info(f"Found {len(zset_data)} items in zset")
                
                formatted_waypoints = []
                
                for member, score in zset_data:
                    try:
                        # Decode if bytes
                        if isinstance(member, bytes):
                            member = member.decode('utf-8')
                        
                        wp_data = json.loads(member)
                        
                        formatted_wp = {
                            'sequence': len(formatted_waypoints),
                            'latitude': float(wp_data.get('lat') or wp_data.get('latitude', 0)),
                            'longitude': float(wp_data.get('lon') or wp_data.get('longitude', 0)),
                            'altitude': float(wp_data.get('alt') or wp_data.get('altitude', 0)),
                            'command': 'WAYPOINT'
                        }
                        formatted_waypoints.append(formatted_wp)
                        
                    except json.JSONDecodeError:
                        logger.warning(f"Non-JSON member in zset: {member}")
                        continue
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid waypoint data: {member}, error: {e}")
                        continue
                
                # Sort by timestamp (score)
                formatted_waypoints.sort(key=lambda x: x.get('sequence', 0))
                
                # Renumber sequences and assign commands
                for i, wp in enumerate(formatted_waypoints):
                    wp['sequence'] = i
                    if i == 0:
                        wp['command'] = 'TAKEOFF'
                    elif i == len(formatted_waypoints) - 1:
                        wp['command'] = 'LAND'
                    else:
                        wp['command'] = 'WAYPOINT'
                
                mission_response['waypoints'] = formatted_waypoints
                mission_response['name'] = f'Mission {mission_id}'
                mission_response['created_at'] = datetime.now().isoformat()
                
            except Exception as e:
                logger.error(f"Failed to parse zset: {e}")
                raise HTTPException(status_code=500, detail=f"Failed to parse mission zset data: {str(e)}")
        
        else:
            logger.warning(f"Unsupported key type: {key_type} for key: {key}")
            raise HTTPException(status_code=400, detail=f"Unsupported mission data type: {key_type}")
        
        # Ensure we have default values if still empty
        if not mission_response['name']:
            mission_response['name'] = f'Mission {mission_id}'
        if not mission_response['created_at']:
            mission_response['created_at'] = datetime.now().isoformat()
        
        logger.info(f"✓ Retrieved mission {mission_id} with {len(mission_response['waypoints'])} waypoints (client format)")
        logger.info(f"   Name: {mission_response['name']}")
        logger.info(f"   Status: {mission_response['status']}")
        logger.info(f"   Created: {mission_response['created_at']}")
        
        return JSONResponse(mission_response)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting mission details for {mission_id}: {e}")
        logger.exception(e)
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        }, status_code=500)


# ═══════════════════════════════════════════════════════════════════════════════
# APPLICATION ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "═" * 80)
    print("🚁 COMBINED DRONE CONTROL API v5.0.0")
    print("═" * 80)
    print("📡 Ensure PX4 SITL is running: make px4_sitl gazebo")
    print("🌐 API will be available at: http://127.0.0.1:7000")
    print("📚 Documentation: http://127.0.0.1:7000/docs")
    print("🔄 WebSocket: ws://127.0.0.1:7000/ws/telemetry")
    print("═" * 80 + "\n")
    
    uvicorn.run(
        "gazebo_rest_api:app",
        host="127.0.0.1",
        port=7000,
        reload=True,
        log_level="info"
    )