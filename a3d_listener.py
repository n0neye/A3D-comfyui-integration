import threading
import json
import time
import os
# import copy # If deep copying of data is needed
import numpy as np
import torch
import base64
from PIL import Image
from io import BytesIO
import asyncio # Use asyncio's queue
from server import PromptServer
from aiohttp import web

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
data_lock = threading.Lock() # Keep threading lock for synchronous access to latest_received_data

# --- SSE Specific Globals ---
sse_clients = {}  # Dictionary to store client connections
sse_clients_lock = asyncio.Lock() # Use asyncio Lock for async context
sse_message_queue = asyncio.Queue()  # Use asyncio's Queue
_sse_processor_started = False # Flag to ensure processor starts only once
_sse_processor_task = None # Hold a reference to the task

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
    global latest_received_data, data_lock, sse_message_queue
    start_time = time.time()
    print(f"[A3D Handler {start_time:.2f}] Received request.")

    # Ensure the SSE processor task is running
    ensure_sse_processor_running() # Call the check here as well

    try:
        content_type = request.headers.get('Content-Type', '')
        
        if content_type.startswith('application/json'):
            data = await request.json()
            print(f"[A3D Handler {start_time:.2f}] Received JSON data: {type(data)}")
            
            # Extract data from the request
            color_image_b64 = data.get("color_image_base64")
            depth_image_b64 = data.get("depth_image_base64")
            openpose_image_b64 = data.get("openpose_image_base64")
            
            # Extract metadata if available
            metadata = data.get("metadata", {})
            prompt = metadata.get("prompt")
            negative_prompt = metadata.get("negative_prompt")
            seed = metadata.get("seed")
            
            # Update the global data store (still use threading.Lock here as it might be accessed by node's IS_CHANGED)
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
            print(f"[A3D Handler {start_time:.2f}] Data stored in latest_received_data.")
            
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
                # "payload": data # Avoid sending large raw payload via SSE if possible
            }
            
            await sse_message_queue.put(sse_payload) # Use await put for asyncio.Queue
            print(f"[A3D Handler {start_time:.2f}] Data queued for SSE broadcast (Queue size: {sse_message_queue.qsize()}).")
            
            # Return success response
            response = web.json_response({'status': 'success', 'message': f'Data received at {current_timestamp}'})
            return add_cors_headers(response)
            
        else:
            # Handle non-JSON content (binary data)
            body = await request.read()
            print(f"[A3D Handler {start_time:.2f}] Received non-JSON data of length {len(body)}")
            
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
                sse_payload = {"type": "new_binary_data", "timestamp": current_timestamp, "size": len(body)}
                await sse_message_queue.put(sse_payload)
                print(f"[A3D Handler {start_time:.2f}] Binary data info queued for SSE broadcast (Queue size: {sse_message_queue.qsize()}).")
                
                # Return success response
                response = web.json_response({'status': 'success', 'message': f'Binary data received at {current_timestamp}'})
                return add_cors_headers(response)
                
            except Exception as e:
                print(f"[A3D Handler {start_time:.2f}] Error processing binary data: {e}")
                response = web.json_response({'status': 'error', 'message': f'Error processing binary data: {e}'}, status=400)
                return add_cors_headers(response)
    
    except Exception as e:
        print(f"[A3D Handler {start_time:.2f}] Error processing request: {e}")
        response = web.json_response({'status': 'error', 'message': str(e)}, status=500)
        return add_cors_headers(response)
    finally:
        end_time = time.time()
        print(f"[A3D Handler {start_time:.2f}] Request processing finished in {end_time - start_time:.3f} seconds.")

# --- SSE endpoint ---
@routes.get('/a3d_events')
async def sse_handler(request):
    global sse_clients, sse_clients_lock
    
    # Ensure the SSE processor task is running
    ensure_sse_processor_running() # Call the check when a client connects
    
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
    async with sse_clients_lock:
        sse_clients[client_id] = response
        print(f"[SSE Handler] Client {client_id} connected. Total clients: {len(sse_clients)}")
    
    try:
        # Keep connection alive by sending heartbeats or waiting indefinitely
        while True:
            # Send a heartbeat comment every 15 seconds to keep connection alive
            await response.write(b':heartbeat\n\n')
            await asyncio.sleep(15)
    except ConnectionResetError:
        print(f"[SSE Handler] Client {client_id} disconnected (connection reset)")
    except asyncio.CancelledError:
         print(f"[SSE Handler] Client {client_id} connection task cancelled.")
    except Exception as e:
        print(f"[SSE Handler] Error in SSE connection for client {client_id}: {e}")
    finally:
        # Remove client when disconnected
        async with sse_clients_lock:
            if client_id in sse_clients:
                del sse_clients[client_id]
                print(f"[SSE Handler] Client {client_id} removed. Total clients: {len(sse_clients)}")
    
    return response

# --- Function to broadcast SSE messages ---
async def broadcast_sse_message(message_data):
    global sse_clients, sse_clients_lock
    start_time = time.time()
    print(f"[SSE Broadcast {start_time:.2f}] Preparing to broadcast message: {message_data.get('type')}")

    # Format message for SSE
    sse_formatted_message = f"data: {json.dumps(message_data)}\n\n"
    sse_message_bytes = sse_formatted_message.encode('utf-8')
    
    disconnected_clients = []
    async with sse_clients_lock: # Lock while iterating/writing
        if not sse_clients:
            print(f"[SSE Broadcast {start_time:.2f}] No clients connected, skipping broadcast.")
            return

        print(f"[SSE Broadcast {start_time:.2f}] Broadcasting to {len(sse_clients)} client(s).")
        for client_id, response in sse_clients.items():
            try:
                await response.write(sse_message_bytes)
                # print(f"[SSE Broadcast {start_time:.2f}] Sent message to client {client_id}") # Can be noisy
            except ConnectionResetError:
                print(f"[SSE Broadcast {start_time:.2f}] Client {client_id} disconnected during write (ConnectionResetError). Marking for removal.")
                disconnected_clients.append(client_id)
            except Exception as e:
                # Handle other potential errors like broken pipe etc.
                print(f"[SSE Broadcast {start_time:.2f}] Error sending to client {client_id}: {e}. Marking for removal.")
                disconnected_clients.append(client_id)

        # Remove disconnected clients outside the iteration loop
        for client_id in disconnected_clients:
            if client_id in sse_clients:
                del sse_clients[client_id]
                print(f"[SSE Broadcast {start_time:.2f}] Removed disconnected client {client_id}. Remaining: {len(sse_clients)}")
    end_time = time.time()
    print(f"[SSE Broadcast {start_time:.2f}] Broadcast finished in {end_time - start_time:.3f} seconds.")

# --- SSE message processor task ---
async def sse_message_processor():
    print("[SSE Processor] Starting SSE message processor task.")
    while True:
        try:
            # Wait until an item is available in the queue
            # print("[SSE Processor] Waiting for message...") # Can be noisy
            message = await sse_message_queue.get()
            proc_start_time = time.time()
            print(f"[SSE Processor {proc_start_time:.2f}] Got message from queue (Type: {message.get('type')}). Queue size now: {sse_message_queue.qsize()}")

            await broadcast_sse_message(message)
            sse_message_queue.task_done() # Notify the queue that the task is complete
            print(f"[SSE Processor {proc_start_time:.2f}] Finished processing message.")

        except asyncio.CancelledError:
            print("[SSE Processor] Task cancelled.")
            break # Exit the loop if cancelled
        except Exception as e:
            print(f"[SSE Processor] Error processing message: {e}")
            await asyncio.sleep(1) # Sleep briefly on error before retrying

# --- Function to ensure the processor task is running ---
def ensure_sse_processor_running():
    global _sse_processor_started, _sse_processor_task
    if not _sse_processor_started:
        print("[A3D Listener] First check: SSE processor not started. Attempting to start...")
        loop = asyncio.get_event_loop()
        # Check if loop is already running, might be needed in some contexts
        if loop.is_running():
             print("[A3D Listener] Event loop is running. Creating SSE processor task.")
             _sse_processor_task = loop.create_task(sse_message_processor())
             _sse_processor_started = True
        else:
             # This case might happen if called very early, might need adjustment
             # depending on how/when ComfyUI integrates custom nodes/routes.
             # For now, just log it. Starting it later might be necessary.
             print("[A3D Listener] Warning: Event loop not running when trying to start SSE processor.")
             # Optionally, schedule it to run when the loop starts if possible,
             # but create_task usually handles this.

    # Optional: Check if the task is still running if it was started previously
    elif _sse_processor_task and _sse_processor_task.done():
         print("[A3D Listener] Warning: SSE processor task was started but is now done. Restarting...")
         # Log exception if task failed
         if _sse_processor_task.exception():
              print(f"[A3D Listener] SSE processor task failed with exception: {_sse_processor_task.exception()}")
         loop = asyncio.get_event_loop()
         _sse_processor_task = loop.create_task(sse_message_processor())

# --- Helper function to convert base64 to tensor ---
def base64_to_tensor(base64_str):
    if not base64_str or not isinstance(base64_str, str):
        return None
    try:
        # Remove potential data URI prefix
        if "base64," in base64_str:
            base64_str = base64_str.split("base64,")[1]

        image_data = base64.b64decode(base64_str)
        img = Image.open(BytesIO(image_data))
        
        # Handle different image modes
        if img.mode == 'RGBA':
            # Convert RGBA to RGB
            img_rgb = Image.new('RGB', img.size, (0, 0, 0))
            img_rgb.paste(img, mask=img.split()[3])  # Use alpha as mask
            img = img_rgb
        elif img.mode == 'L':
            # For grayscale, convert to RGB by duplicating the channel
            img = img.convert('RGB')
        elif img.mode != 'RGB':
            # Convert any other mode to RGB
            img = img.convert('RGB')
            
        # Convert to numpy array
        image_np = np.array(img).astype(np.float32) / 255.0 # Convert to float32 and normalize
        
        # Create tensor with batch dimension [B, H, W, C]
        image_tensor = torch.from_numpy(image_np).unsqueeze(0) # Now float32
        
        return image_tensor
    except Exception as e:
        print(f"[Tensor Conversion] Error converting base64 to tensor: {e}")
        return None

# --- ComfyUI Node Class ---
class A3DListenerNode:
    _last_processed_timestamp = 0  # Track the last timestamp we processed
    
    def __init__(self):
        print("[A3D Listener Node] Initializing node instance.")
        # Ensure the SSE processor is running when a node is created
        ensure_sse_processor_running()

    @classmethod
    def INPUT_TYPES(cls):
        # Ensure the SSE processor is running when INPUT_TYPES is accessed (early check)
        ensure_sse_processor_running()
        return {
            "required": {}, 
            "optional": {}
        }
    
    @classmethod
    def IS_CHANGED(cls, **kwargs): # Accept arbitrary kwargs
        global latest_received_data, data_lock
        
        with data_lock:
            current_timestamp = latest_received_data["timestamp"]
        
        # Compare the current timestamp with the last processed one
        # Use a small epsilon to handle potential float comparison issues if needed
        if current_timestamp > cls._last_processed_timestamp:
            print(f"[A3D IS_CHANGED] New data detected. Timestamp: {current_timestamp}")
            # New data is available - return the current timestamp 
            # to signal that the node should execute
            return current_timestamp # Returning a changing value triggers execution
        
        # No new data, return the last processed timestamp
        # to signal that the node shouldn't execute
        return cls._last_processed_timestamp
    
    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "STRING", "STRING", "INT")
    RETURN_NAMES = ("color_image", "depth_image", "openpose_image", "prompt", "negative_prompt", "seed")
    FUNCTION = "get_latest_data"
    CATEGORY = "Utils/Listeners"
    OUTPUT_NODE = True # Keep True if it should display outputs in UI previews
    
    def get_latest_data(self, **kwargs): # Accept arbitrary kwargs
        global latest_received_data, data_lock
        exec_start_time = time.time()
        print(f"[Node Execute {exec_start_time:.2f}] get_latest_data called.")

        # Create a default empty tensor for missing images
        # Ensure it matches the expected float32 type and normalized range
        empty_image = torch.zeros((1, 64, 64, 3), dtype=torch.float32)
        
        # --- Variables for tensors and metadata ---
        color_tensor = None
        depth_tensor = None
        openpose_tensor = None
        prompt_value = None
        negative_prompt_value = None
        seed_value = None
        current_timestamp = 0 # Initialize timestamp
        # ---
        
        with data_lock:
            # Get data stored by the last request
            current_timestamp = latest_received_data["timestamp"]
            # Update the class-level last processed timestamp ONLY when executing
            A3DListenerNode._last_processed_timestamp = current_timestamp
            # Get base64 strings and metadata
            color_b64 = latest_received_data["color_image_base64"]
            depth_b64 = latest_received_data["depth_image_base64"]
            op_b64 = latest_received_data["openpose_image_base64"]
            prompt_value = latest_received_data["prompt"] or ""
            negative_prompt_value = latest_received_data["negative_prompt"] or ""
            seed_value = latest_received_data["seed"] # Keep as is, handle conversion below
        
        print(f"[Node Execute {exec_start_time:.2f}] Processing data from timestamp: {current_timestamp}", flush=True)
        
        # --- Convert base64 to Tensors ---
        color_tensor = base64_to_tensor(color_b64)
        depth_tensor = base64_to_tensor(depth_b64)
        openpose_tensor = base64_to_tensor(op_b64)
        # ---
        
        # Use empty tensor if conversion failed or data was None
        color_tensor = color_tensor if color_tensor is not None else empty_image
        depth_tensor = depth_tensor if depth_tensor is not None else empty_image
        openpose_tensor = openpose_tensor if openpose_tensor is not None else empty_image
        
        # Convert seed to integer if present, default to 0
        try:
            # Handle None, empty string, or convert float/string
            if seed_value is None or seed_value == "":
                seed_value_int = 0
            else:
                seed_value_int = int(float(seed_value)) # Convert via float first for robustness
        except (ValueError, TypeError):
             print(f"[Node Execute {exec_start_time:.2f}] Warning: Could not convert seed '{seed_value}' to int. Defaulting to 0.")
             seed_value_int = 0
        
        print(f"[Node Execute {exec_start_time:.2f}] Returning image tensors and metadata (Seed: {seed_value_int}).", flush=True)
        exec_end_time = time.time()
        print(f"[Node Execute {exec_start_time:.2f}] Execution finished in {exec_end_time - exec_start_time:.3f} seconds.")
        return (color_tensor, depth_tensor, openpose_tensor, prompt_value, negative_prompt_value, seed_value_int)

# --- Initial Check ---
# Call the check function once when the module is loaded.
# This is likely the best place to ensure it starts early.
print("[A3D Listener Module] Module loading. Ensuring SSE processor is running.")
ensure_sse_processor_running()