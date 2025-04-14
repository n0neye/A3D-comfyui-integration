import threading
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import socket # For checking if port is available
import os
# import copy # If deep copying of data is needed
import numpy as np
import torch
import base64
from PIL import Image
from io import BytesIO

# --- Global shared state ---
# Use a simple list (as thread-safe cache) and lock to store the latest data
# Queue.Queue could also be used, but if only the latest message matters, variable+lock is simpler
latest_received_data = {"payload": None, "timestamp": 0}
data_lock = threading.Lock()
server_instance = None
server_thread = None
server_started_flag = False  # Flag to mark whether the server has started successfully
# --- Configuration ---
DEFAULT_PORT = 8199 # Choose a port (avoid commonly used ports like 8188)
# Try to get port from environment variable for flexibility
LISTEN_PORT = int(os.environ.get('ELECTRON_LISTENER_PORT', DEFAULT_PORT))
LISTEN_HOST = '0.0.0.0' # Listen on all network interfaces

# --- HTTP Request Handler ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    """Handles POST requests from Electron"""
    def do_POST(self):
        global latest_received_data, data_lock
        try:
            # 1. Get the request body length
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                print("[Electron Listener] Received POST with no data.")
                self.send_response(400)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*') # Allow cross-origin
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': 'No data received'}).encode('utf-8'))
                return

            # 2. Read request body
            body = self.rfile.read(content_length)
            
            # Check content type
            content_type = self.headers.get('Content-Type', '')
            
            # Process image data
            if content_type.startswith('image/'):
                # Directly save binary image data
                with data_lock:
                    latest_received_data["payload"] = {
                        "type": "image",
                        "content_type": content_type,
                        "image_data": body
                    }
                    latest_received_data["timestamp"] = time.time()
            # Process JSON data containing base64 image
            elif content_type.startswith('application/json'):
                data_string = body.decode('utf-8')
                parsed_data = json.loads(data_string)
                
                # 3. Update shared data (thread-safe)
                with data_lock:
                    latest_received_data["payload"] = parsed_data
                    latest_received_data["timestamp"] = time.time()
            else:
                # Process unknown content type
                data_string = body.decode('utf-8', errors='ignore')
                with data_lock:
                    latest_received_data["payload"] = {
                        "type": "unknown",
                        "content_type": content_type,
                        "data": data_string[:1000]  # Limit length to prevent oversized data
                    }
                    latest_received_data["timestamp"] = time.time()
            
            # 5. Send success response
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*') # Allow cross-origin
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'success', 'message': 'Data received in ' + str(time.time())}).encode('utf-8'))

        except json.JSONDecodeError:
            print("[Electron Listener] Received invalid JSON.")
            self.send_response(400)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'error', 'message': 'Invalid JSON'}).encode('utf-8'))
        except Exception as e:
            print(f"[Electron Listener] Server error handling POST: {e}")
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'error', 'message': f'Server error: {e}'}).encode('utf-8'))

    # Optional: Handle GET requests for simple health checks
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(f'Electron Listener Node HTTP Server active on port {LISTEN_PORT}.'.encode('utf-8'))

    # Optional: Suppress or customize request logs
    def log_message(self, format, *args):
        # Comment out to keep console clean, or customize log format
        # print(f"[HTTP Log] {self.address_string()} - {format % args}")
        pass

# --- Check if port is in use ---
def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            # Try to bind to 127.0.0.1 instead of 0.0.0.0 for checking
            # Since 0.0.0.0 might succeed even if specific interfaces are occupied
            s.bind(('127.0.0.1', port))
        except socket.error as e:
            if e.errno == 98 or e.errno == 10048: # Address already in use (Linux/Windows)
                return True
            else:
                raise # Other errors need to be raised
        return False

# --- Function to start the HTTP server ---
def start_http_server(host, port):
    global server_instance, server_thread, server_started_flag
    # Check if server is already running (based on thread object)
    if server_thread and server_thread.is_alive():
        print(f"[Electron Listener] Server thread already running on port {port}.")
        server_started_flag = True # Ensure flag is True
        return

    # Check if port is already in use by another process
    if is_port_in_use(port):
         print(f"[Electron Listener] Error: Port {port} is already in use by another process. Cannot start server.")
         # If port is in use, we can't assume it's our own old instance, so mark as not started
         server_started_flag = False
         return # Don't start server

    try:
        server_address = (host, port)
        server_instance = HTTPServer(server_address, SimpleHTTPRequestHandler)
        print(f"[Electron Listener] Starting HTTP server on {host}:{port}...")

        # Run server in background thread
        # daemon=True ensures this thread exits when the ComfyUI main process exits
        server_thread = threading.Thread(target=server_instance.serve_forever, daemon=True)
        server_thread.start()
        server_started_flag = True # Mark server as successfully started
        print(f"[Electron Listener] HTTP server started successfully on port {port}.")

    except Exception as e:
        print(f"[Electron Listener] Failed to start HTTP server on port {port}: {e}")
        server_instance = None
        server_thread = None
        server_started_flag = False # Mark server start as failed

# --- ComfyUI Node Class ---
class ElectronHttpListenerNode:
    _server_started = False # Class-level flag to ensure server only starts once

    def __init__(self):
        # Use class-level flag to ensure start_http_server is only called once
        # This typically happens when ComfyUI loads the node
        if not ElectronHttpListenerNode._server_started:
            print("[Electron Listener Node] Initializing...")
            start_http_server(LISTEN_HOST, LISTEN_PORT)
            ElectronHttpListenerNode._server_started = server_started_flag # Update class flag to actual start status

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {}, # Usually no input needed, passively receives
            "optional": {
                # Add optional trigger for convenience in manually refreshing
                "trigger": ("*", {"forceInput": True}),
            }
        }

    # Force the node to refresh every time the user runs a queue
    @classmethod
    def IS_CHANGED(cls, trigger):
        return float("nan") 

    RETURN_TYPES = ("STRING", "FLOAT", "IMAGE") # Add IMAGE output
    RETURN_NAMES = ("received_data_json", "timestamp", "image")
    FUNCTION = "get_latest_data"
    CATEGORY = "Utils/Listeners" 

    # Show image preview in node
    OUTPUT_NODE = True  # This enables image to display in the node itself
    
    def get_latest_data(self, trigger=None):
        global latest_received_data, data_lock, server_started_flag

        if not server_started_flag:
            print("[Electron Listener Node] Warning: HTTP Server is not running or failed to start.", flush=True)
            # Return error or empty state
            error_msg = {"error": "HTTP server not running", "details": f"Check if port {LISTEN_PORT} is available."}
            # Return empty tensor as image, ensure BHWC format [1, H, W, C]
            empty_image = torch.zeros((1, 64, 64, 3), dtype=torch.uint8)
            return (json.dumps(error_msg), 0.0, empty_image)

        current_payload = None
        current_timestamp = 0.0
        image_tensor = None

        with data_lock:
            if latest_received_data["payload"] is not None:
                # Shallow copy payload for processing
                current_payload = latest_received_data["payload"]
                current_timestamp = latest_received_data["timestamp"]

                # Process image data
                try:
                    img = None # Initialize img variable

                    # Check if it's direct image data
                    if isinstance(current_payload, dict) and current_payload.get("type") == "image":
                        image_data = current_payload.get("image_data")
                        if image_data:
                            print("[Electron Listener Node] Processing direct image data...", flush=True)
                            img = Image.open(BytesIO(image_data)).convert('RGB')

                    # Check if JSON contains base64 encoded image
                    elif isinstance(current_payload, dict) and "image_base64" in current_payload:
                        base64_data = current_payload["image_base64"]
                        if isinstance(base64_data, str):
                            print("[Electron Listener Node] Processing base64 image data...", flush=True)
                            # Remove possible data:image/jpeg;base64, prefix
                            if "base64," in base64_data:
                                base64_data = base64_data.split("base64,")[1]

                            image_data = base64.b64decode(base64_data)
                            img = Image.open(BytesIO(image_data)).convert('RGB')

                    # If an image was successfully loaded, convert it to tensor
                    if img is not None:
                        image_np = np.array(img).astype(np.uint8)
                        # Ensure NumPy array is HWC (Height, Width, Channels)
                        if image_np.ndim == 3 and image_np.shape[2] == 3:
                             # Convert HWC numpy array to BHWC tensor [Batch, Height, Width, Channels]
                             image_tensor = torch.from_numpy(image_np).unsqueeze(0)
                             print(f"[Electron Listener Node] Image converted to tensor with shape: {image_tensor.shape}", flush=True)
                        else:
                             print(f"[Electron Listener Node] Warning: Processed image has unexpected shape {image_np.shape}", flush=True)


                except Exception as e:
                    print(f"[Electron Listener Node] Error processing image: {e}", flush=True)

            else:
                print("[Electron Listener Node] No new data received since last check or initial state.", flush=True)

        # If no image data or processing failed, return empty image
        if image_tensor is None:
            print("[Electron Listener Node] No valid image tensor generated, returning empty tensor.", flush=True)
            # Create a small empty image in BHWC format
            image_tensor = torch.zeros((1, 64, 64, 3), dtype=torch.uint8)

        # Determine the payload to return as JSON string
        payload_to_return = current_payload if current_payload is not None else {}

        # Avoid returning large binary image data in the JSON string if it was sent directly
        if isinstance(payload_to_return, dict) and payload_to_return.get("type") == "image":
             payload_summary = {
                 "type": "image",
                 "content_type": payload_to_return.get("content_type"),
                 "status": "received",
                 "size": len(payload_to_return.get("image_data", b""))
             }
             json_output = json.dumps(payload_summary)
        else:
             json_output = json.dumps(payload_to_return)


        return (json_output, current_timestamp, image_tensor)

# --- Ensure server starts when ComfyUI loads ---
# Try to start server when module loads, don't wait for node instantiation
# This is more reliable because ComfyUI might not instantiate all nodes immediately
if not ElectronHttpListenerNode._server_started:
    print("[Electron Listener Module] Attempting to start server on module load...")
    start_http_server(LISTEN_HOST, LISTEN_PORT)
    ElectronHttpListenerNode._server_started = server_started_flag # Update class flag