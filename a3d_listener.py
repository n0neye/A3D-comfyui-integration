import threading
import json
import time
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from http.server import HTTPServer
import socket # For checking if port is available
import os
# import copy # If deep copying of data is needed
import numpy as np
import torch
import base64
from PIL import Image
from io import BytesIO
import queue # Using queue for thread-safe message passing to SSE clients
from server import PromptServer
from aiohttp import web
import asyncio

# --- Global shared state ---
# Use a simple list (as thread-safe cache) and lock to store the latest data
# Queue.Queue could also be used, but if only the latest message matters, variable+lock is simpler
latest_received_data = {
    "payload": None,
    "timestamp": 0,
    "color_image_base64": None,  # Now as main image
    "depth_image_base64": None,
    "openpose_image_base64": None,
    # Add metadata fields
    "prompt": None,
    "negative_prompt": None,
    "seed": None,
}
data_lock = threading.Lock()
# --- SSE Specific Globals ---
sse_clients = {}  # Dictionary to store client connections
sse_clients_lock = threading.Lock()
sse_message_queue = queue.Queue()  # Queue for message passing

# Set up the routes
routes = PromptServer.instance.routes

# --- Helper function for CORS headers ---
def add_cors_headers(response):
    response.headers.update({
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Content-Length, Accept, X-Requested-With'
    })
    return response

# --- Option request handler for CORS preflight ---
@routes.options('/a3d_data')
async def options_handler(request):
    response = web.Response(status=200)
    add_cors_headers(response)
    return response

# --- Main data receiver endpoint ---
@routes.post('/a3d_data')
async def receive_data(request):
    global latest_received_data, data_lock
    
    try:
        content_type = request.headers.get('Content-Type', '')
        
        if content_type.startswith('application/json'):
            data = await request.json()
            print(f"[A3D Handler] Received JSON data: {type(data)}")
            
            # Extract data from the request
            color_image_b64 = data.get("color_image_base64")
            depth_image_b64 = data.get("depth_image_base64")
            openpose_image_b64 = data.get("openpose_image_base64")
            
            # Extract metadata if available
            metadata = data.get("metadata", {})
            prompt = metadata.get("prompt")
            negative_prompt = metadata.get("negative_prompt")
            seed = metadata.get("seed")
            
            # Update the global data store
            current_timestamp = time.time()
            with data_lock:
                latest_received_data["payload"] = data
                latest_received_data["timestamp"] = current_timestamp
                latest_received_data["color_image_base64"] = color_image_b64
                latest_received_data["depth_image_base64"] = depth_image_b64
                latest_received_data["openpose_image_base64"] = openpose_image_b64
                latest_received_data["prompt"] = prompt
                latest_received_data["negative_prompt"] = negative_prompt
                latest_received_data["seed"] = seed
            
            # Queue message for SSE clients
            sse_payload = {
                "type": "new_images",
                "timestamp": current_timestamp,
                "color_image_base64": color_image_b64,
                "depth_image_base64": depth_image_b64,
                "openpose_image_base64": openpose_image_b64,
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "seed": seed,
                "payload": data
            }
            
            sse_message_queue.put_nowait(sse_payload)
            print("[A3D Handler] Data stored and queued for SSE broadcast")
            
            # Return success response
            response = web.json_response({'status': 'success', 'message': f'Data received at {current_timestamp}'})
            return add_cors_headers(response)
            
        else:
            # Handle non-JSON content (binary data)
            body = await request.read()
            print(f"[A3D Handler] Received non-JSON data of length {len(body)}")
            
            # Try to encode as base64 if it's image data
            try:
                color_image_b64 = base64.b64encode(body).decode('utf-8')
                if content_type.startswith('image/'):
                    color_image_b64 = f"data:{content_type};base64,{color_image_b64}"
                
                # Update the global data store
                current_timestamp = time.time()
                with data_lock:
                    latest_received_data["payload"] = {"type": "binary_data", "content_type": content_type}
                    latest_received_data["timestamp"] = current_timestamp
                    latest_received_data["color_image_base64"] = color_image_b64
                
                # Queue message for SSE clients
                sse_payload = {
                    "type": "new_images",
                    "timestamp": current_timestamp,
                    "color_image_base64": color_image_b64
                }
                
                sse_message_queue.put_nowait(sse_payload)
                print("[A3D Handler] Binary data stored and queued for SSE broadcast")
                
                # Return success response
                response = web.json_response({'status': 'success', 'message': f'Binary data received at {current_timestamp}'})
                return add_cors_headers(response)
                
            except Exception as e:
                print(f"[A3D Handler] Error processing binary data: {e}")
                response = web.json_response({'status': 'error', 'message': f'Error processing binary data: {e}'}, status=400)
                return add_cors_headers(response)
    
    except Exception as e:
        print(f"[A3D Handler] Error processing request: {e}")
        response = web.json_response({'status': 'error', 'message': str(e)}, status=500)
        return add_cors_headers(response)

# --- SSE endpoint ---
@routes.get('/a3d_events')
async def sse_handler(request):
    global sse_clients, sse_clients_lock
    
    # Prepare SSE response
    response = web.StreamResponse()
    response.headers.add('Content-Type', 'text/event-stream')
    response.headers.add('Cache-Control', 'no-cache')
    response.headers.add('Connection', 'keep-alive')
    add_cors_headers(response)
    
    await response.prepare(request)
    
    # Generate a unique client ID
    client_id = id(response)
    
    # Add client to our registry
    with sse_clients_lock:
        sse_clients[client_id] = response
        print(f"[SSE Handler] Client connected. Total clients: {len(sse_clients)}")
    
    try:
        # Keep connection alive until client disconnects
        while True:
            await response.write(b':heartbeat\n\n')
            await asyncio.sleep(15)
    except ConnectionResetError:
        print("[SSE Handler] Client disconnected (connection reset)")
    except Exception as e:
        print(f"[SSE Handler] Error in SSE connection: {e}")
    finally:
        # Remove client when disconnected
        with sse_clients_lock:
            if client_id in sse_clients:
                del sse_clients[client_id]
                print(f"[SSE Handler] Client removed. Total clients: {len(sse_clients)}")
    
    return response

# --- Function to broadcast SSE messages ---
async def broadcast_sse_message(message_data):
    global sse_clients, sse_clients_lock
    
    # Format message for SSE
    sse_formatted_message = f"data: {json.dumps(message_data)}\n\n"
    sse_message_bytes = sse_formatted_message.encode('utf-8')
    
    disconnected_clients = []
    with sse_clients_lock:
        for client_id, response in sse_clients.items():
            try:
                await response.write(sse_message_bytes)
            except Exception as e:
                print(f"[SSE Broadcast] Error sending to client: {e}")
                disconnected_clients.append(client_id)
        
        # Remove disconnected clients
        for client_id in disconnected_clients:
            if client_id in sse_clients:
                del sse_clients[client_id]

# --- SSE message processor task ---
async def sse_message_processor():
    print("[SSE Processor] Starting SSE message processor task")
    while True:
        try:
            # Check if there are messages in the queue
            if not sse_message_queue.empty():
                message = sse_message_queue.get_nowait()
                await broadcast_sse_message(message)
                sse_message_queue.task_done()
            # Sleep briefly to avoid busy waiting
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[SSE Processor] Error processing message: {e}")
            await asyncio.sleep(1)  # Sleep longer on error

# --- Helper function to convert base64 to tensor ---
def base64_to_tensor(base64_str):
    if not base64_str or not isinstance(base64_str, str):
        return None
    try:
        # Remove potential data URI prefix
        if "base64," in base64_str:
            base64_str = base64_str.split("base64,")[1]

        image_data = base64.b64decode(base64_str)
        img = Image.open(BytesIO(image_data)).convert('RGB')
        image_np = np.array(img).astype(np.uint8)

        if image_np.ndim == 3 and image_np.shape[2] == 3:
            # Convert HWC numpy array to BHWC tensor [Batch, Height, Width, Channels]
            image_tensor = torch.from_numpy(image_np).unsqueeze(0)
            return image_tensor
        else:
            print(f"[Tensor Conversion] Warning: Decoded image has unexpected shape {image_np.shape}")
            return None
    except Exception as e:
        print(f"[Tensor Conversion] Error converting base64 to tensor: {e}")
        return None

# --- ComfyUI Node Class ---
class A3DListenerNode:
    _server_started = False
    
    def __init__(self):
        if not A3DListenerNode._server_started:
            print("[A3D Listener Node] Initializing...")
            # Start the SSE message processor task
            asyncio.create_task(sse_message_processor())
            A3DListenerNode._server_started = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {}, 
            "optional": {
                "trigger": ("*", {"forceInput": True}),
            }
        }
    
    @classmethod
    def IS_CHANGED(cls, trigger):
        return float("nan")
    
    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "STRING", "STRING", "INT")
    RETURN_NAMES = ("color_image", "depth_image", "openpose_image", "prompt", "negative_prompt", "seed")
    FUNCTION = "get_latest_data"
    CATEGORY = "Utils/Listeners"
    OUTPUT_NODE = True
    
    def get_latest_data(self, trigger=None):
        global latest_received_data, data_lock
        
        # Create a default empty tensor for missing images
        empty_image = torch.zeros((1, 64, 64, 3), dtype=torch.uint8)
        
        # --- Variables for tensors and metadata ---
        color_tensor = None
        depth_tensor = None
        openpose_tensor = None
        prompt_value = None
        negative_prompt_value = None
        seed_value = None
        # ---
        
        with data_lock:
            # Get data stored by the last request
            current_timestamp = latest_received_data["timestamp"]
            # Get base64 strings and metadata
            color_b64 = latest_received_data["color_image_base64"]
            depth_b64 = latest_received_data["depth_image_base64"]
            op_b64 = latest_received_data["openpose_image_base64"]
            prompt_value = latest_received_data["prompt"] or ""
            negative_prompt_value = latest_received_data["negative_prompt"] or ""
            seed_value = latest_received_data["seed"] or 0
        
        print(f"[Node Execute] Processing data from timestamp: {current_timestamp}", flush=True)
        
        # --- Convert base64 to Tensors ---
        color_tensor = base64_to_tensor(color_b64)
        depth_tensor = base64_to_tensor(depth_b64)
        openpose_tensor = base64_to_tensor(op_b64)
        # ---
        
        # Use empty tensor if conversion failed or data was None
        color_tensor = color_tensor if color_tensor is not None else empty_image
        depth_tensor = depth_tensor if depth_tensor is not None else empty_image
        openpose_tensor = openpose_tensor if openpose_tensor is not None else empty_image
        
        # Convert seed to integer if present
        if isinstance(seed_value, (float, str)):
            try:
                seed_value = int(seed_value)
            except (ValueError, TypeError):
                seed_value = 0
        elif seed_value is None:
            seed_value = 0
        
        print("[Node Execute] Returning image tensors and metadata.", flush=True)
        return (color_tensor, depth_tensor, openpose_tensor, prompt_value, negative_prompt_value, seed_value)

# Initialize the SSE processor when the module loads
print("[A3D Listener Module] Initializing with ComfyUI routes")
PromptServer.instance.loop.create_task(sse_message_processor())