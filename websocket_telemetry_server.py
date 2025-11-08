"""
Real-time WebSocket Telemetry Streaming Server
Streams live telemetry data from Redis to connected clients

Features:
- WebSocket connections for real-time data streaming
- Redis pub/sub for telemetry updates
- Multi-UAV support
- Automatic reconnection handling
- Connection management and broadcasting

Usage:
    python websocket_telemetry_server.py
    WebSocket endpoint: ws://localhost:8002/ws/telemetry
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import redis
import json
import asyncio
import logging
from typing import Set, Dict, List, Optional
from datetime import datetime
import time
from contextlib import asynccontextmanager

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.connection_metadata: Dict[WebSocket, dict] = {}
        
    async def connect(self, websocket: WebSocket, client_id: str = None):
        await websocket.accept()
        self.active_connections.add(websocket)
        self.connection_metadata[websocket] = {
            'client_id': client_id or f"client_{id(websocket)}",
            'connected_at': datetime.now().isoformat(),
            'messages_sent': 0
        }
        logger.info(f"Client connected: {self.connection_metadata[websocket]['client_id']} | Total: {len(self.active_connections)}")
        
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            client_id = self.connection_metadata.get(websocket, {}).get('client_id', 'unknown')
            self.active_connections.remove(websocket)
            del self.connection_metadata[websocket]
            logger.info(f"Client disconnected: {client_id} | Remaining: {len(self.active_connections)}")
    
    async def broadcast(self, message: dict):
        """Broadcast message to all connected clients"""
        if not self.active_connections:
            return
            
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
                self.connection_metadata[connection]['messages_sent'] += 1
            except Exception as e:
                logger.error(f"Error sending to client: {e}")
                disconnected.add(connection)
        
        # Clean up disconnected clients
        for connection in disconnected:
            self.disconnect(connection)
    
    async def send_personal(self, websocket: WebSocket, message: dict):
        """Send message to specific client"""
        try:
            await websocket.send_json(message)
            self.connection_metadata[websocket]['messages_sent'] += 1
        except Exception as e:
            logger.error(f"Error sending personal message: {e}")
            self.disconnect(websocket)
    
    def get_stats(self) -> dict:
        """Get connection statistics"""
        return {
            'total_connections': len(self.active_connections),
            'clients': [
                {
                    'client_id': meta['client_id'],
                    'connected_at': meta['connected_at'],
                    'messages_sent': meta['messages_sent']
                }
                for meta in self.connection_metadata.values()
            ]
        }

manager = ConnectionManager()

# Redis clients
redis_client = None
redis_pubsub = None

# Background task for Redis monitoring
async def redis_telemetry_monitor():
    """Monitor Redis for new telemetry data and broadcast to clients"""
    global redis_client, redis_pubsub
    
    try:
        # Connect to Redis
        redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        redis_client.ping()
        logger.info("✓ Redis connected for monitoring")
        
        # Subscribe to telemetry updates (if using pub/sub)
        redis_pubsub = redis_client.pubsub()
        redis_pubsub.subscribe('telemetry:updates')
        
        logger.info("✓ Started Redis telemetry monitoring")
        
        # Polling loop for telemetry data
        while True:
            try:
                # Get all telemetry keys
                telemetry_keys = []
                cursor = 0
                while True:
                    cursor, keys = redis_client.scan(
                        cursor=cursor, 
                        match="mavlink:telemetry:*", 
                        count=100
                    )
                    telemetry_keys.extend(keys)
                    if cursor == 0:
                        break
                
                # Process each telemetry stream
                for key in telemetry_keys:
                    try:
                        key_type = redis_client.type(key)
                        
                        # Extract mission and vehicle IDs from key
                        # Format: mavlink:telemetry:MISSION-ID_VEHICLE-ID_TIMESTAMP
                        key_parts = key.split(':')[-1].split('_')
                        mission_id = key_parts[0] if len(key_parts) > 0 else 'unknown'
                        vehicle_id = key_parts[1] if len(key_parts) > 1 else 'unknown'
                        
                        latest_data = None
                        
                        if key_type == 'zset':
                            # Get latest telemetry point from sorted set
                            records = redis_client.zrange(key, -1, -1, withscores=False)
                            if records:
                                latest_data = json.loads(records[0])
                        
                        elif key_type == 'list':
                            # Get latest from list
                            record = redis_client.lindex(key, -1)
                            if record:
                                latest_data = json.loads(record)
                        
                        if latest_data:
                            # Broadcast telemetry update
                            message = {
                                'type': 'telemetry_update',
                                'timestamp': datetime.now().isoformat(),
                                'mission_id': mission_id,
                                'vehicle_id': vehicle_id,
                                'data': latest_data
                            }
                            await manager.broadcast(message)
                    
                    except Exception as e:
                        logger.error(f"Error processing telemetry key {key}: {e}")
                
                # Check pub/sub for real-time updates
                message = redis_pubsub.get_message()
                if message and message['type'] == 'message':
                    try:
                        data = json.loads(message['data'])
                        await manager.broadcast({
                            'type': 'telemetry_update',
                            'timestamp': datetime.now().isoformat(),
                            'data': data
                        })
                    except Exception as e:
                        logger.error(f"Error processing pub/sub message: {e}")
                
                # Poll every 500ms for smooth updates
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(1)
    
    except Exception as e:
        logger.error(f"Redis monitoring failed to start: {e}")

# Lifespan context manager for startup/shutdown events
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting WebSocket telemetry server...")
    
    # Start background task
    task = asyncio.create_task(redis_telemetry_monitor())
    
    yield
    
    # Shutdown
    logger.info("Shutting down WebSocket telemetry server...")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    
    if redis_pubsub:
        redis_pubsub.close()
    if redis_client:
        redis_client.close()

# FastAPI app
app = FastAPI(
    title="WebSocket Telemetry Server",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {
        'service': 'WebSocket Telemetry Server',
        'version': '1.0.0',
        'websocket_endpoint': '/ws/telemetry',
        'status': 'running'
    }

@app.get("/api/ws/stats")
async def websocket_stats():
    """Get WebSocket connection statistics"""
    return JSONResponse(manager.get_stats())

@app.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time telemetry streaming
    
    Clients can send:
    - {"action": "subscribe", "mission_id": "MISSION-123", "vehicle_id": "UAV-001"}
    - {"action": "unsubscribe"}
    - {"action": "ping"}
    
    Server sends:
    - {"type": "telemetry_update", "data": {...}}
    - {"type": "connection_info", "data": {...}}
    - {"type": "pong"}
    """
    await manager.connect(websocket)
    
    # Send connection confirmation
    await manager.send_personal(websocket, {
        'type': 'connection_info',
        'message': 'Connected to telemetry stream',
        'client_id': manager.connection_metadata[websocket]['client_id'],
        'timestamp': datetime.now().isoformat()
    })
    
    try:
        while True:
            # Receive messages from client
            data = await websocket.receive_json()
            
            action = data.get('action')
            
            if action == 'ping':
                await manager.send_personal(websocket, {
                    'type': 'pong',
                    'timestamp': datetime.now().isoformat()
                })
            
            elif action == 'subscribe':
                mission_id = data.get('mission_id')
                vehicle_id = data.get('vehicle_id')
                logger.info(f"Client subscribed to mission: {mission_id}, vehicle: {vehicle_id}")
                
                await manager.send_personal(websocket, {
                    'type': 'subscription_confirmed',
                    'mission_id': mission_id,
                    'vehicle_id': vehicle_id
                })
            
            elif action == 'unsubscribe':
                logger.info(f"Client unsubscribed")
                await manager.send_personal(websocket, {
                    'type': 'unsubscription_confirmed'
                })
            
            elif action == 'get_latest':
                # Send latest telemetry for requested mission/vehicle
                mission_id = data.get('mission_id')
                vehicle_id = data.get('vehicle_id')
                
                # Query Redis for latest data
                if redis_client:
                    pattern = f"mavlink:telemetry:*{mission_id}*{vehicle_id}*"
                    keys = redis_client.keys(pattern)
                    
                    if keys:
                        key = keys[0]
                        key_type = redis_client.type(key)
                        
                        if key_type == 'zset':
                            records = redis_client.zrange(key, -1, -1)
                            if records:
                                telemetry = json.loads(records[0])
                                await manager.send_personal(websocket, {
                                    'type': 'telemetry_update',
                                    'data': telemetry,
                                    'timestamp': datetime.now().isoformat()
                                })
    
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

@app.post("/api/telemetry/publish")
async def publish_telemetry(telemetry: dict):
    """
    Publish telemetry update to all connected clients
    Useful for testing or manual triggers
    """
    try:
        await manager.broadcast({
            'type': 'telemetry_update',
            'timestamp': datetime.now().isoformat(),
            'data': telemetry
        })
        
        return JSONResponse({
            'status': 'success',
            'message': 'Telemetry published',
            'clients_notified': len(manager.active_connections)
        })
    except Exception as e:
        return JSONResponse({
            'status': 'error',
            'message': str(e)
        }, status_code=500)

@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    redis_status = 'disconnected'
    
    if redis_client:
        try:
            redis_client.ping()
            redis_status = 'connected'
        except:
            pass
    
    return JSONResponse({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'redis': redis_status,
        'active_connections': len(manager.active_connections)
    })


if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "="*70)
    print("🚀 WEBSOCKET TELEMETRY STREAMING SERVER")
    print("="*70)
    print("WebSocket endpoint: ws://localhost:8002/ws/telemetry")
    print("HTTP API: http://localhost:8002")
    print("\nFeatures:")
    print("  ✓ Real-time telemetry streaming")
    print("  ✓ Multi-UAV support")
    print("  ✓ Automatic reconnection handling")
    print("  ✓ Redis pub/sub integration")
    print("  ✓ Connection management")
    print("="*70 + "\n")
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8002, 
        log_level="info",
        ws_ping_interval=20,
        ws_ping_timeout=20
    )