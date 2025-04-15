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

# --- Global shared state ---
# Use a simple list (as thread-safe cache) and lock to store the latest data
# Queue.Queue could also be used, but if only the latest message matters, variable+lock is simpler
latest_received_data = {
    "payload": None,
    "timestamp": 0,
    "image_base64": None, # Main image for node output processing
    "color_image_base64": None,
    "depth_image_base64": None,
    "openpose_image_base64": None,
}
data_lock = threading.Lock()
server_instance = None
server_thread = None
server_started_flag = False  # Flag to mark whether the server has started successfully
# --- SSE Specific Globals ---
sse_clients = set() # Thread-safe set to store client connections (wfile objects)
sse_clients_lock = threading.Lock()
sse_message_queue = queue.Queue() # Queue to send messages from POST handler to SSE handler thread

# --- Configuration ---
DEFAULT_PORT = 8199 # Choose a port (avoid commonly used ports like 8188)
# Try to get port from environment variable for flexibility
LISTEN_PORT = int(os.environ.get('ELECTRON_LISTENER_PORT', DEFAULT_PORT))
LISTEN_HOST = '0.0.0.0' # Listen on all network interfaces

# --- Function to broadcast messages to SSE clients ---
def broadcast_sse_message(message_data):
    """Sends a message to all connected SSE clients."""
    global sse_clients, sse_clients_lock
    # Format message according to SSE spec (data: json_string\n\n)
    sse_formatted_message = f"data: {json.dumps(message_data)}\n\n"
    sse_message_bytes = sse_formatted_message.encode('utf-8')

    disconnected_clients = set()
    with sse_clients_lock:
        # print(f"[SSE Broadcast] Sending to {len(sse_clients)} clients. Message: {message_data.get('type')}") # Debug
        for client_wfile in sse_clients:
            try:
                client_wfile.write(sse_message_bytes)
                # Flushing might be necessary depending on server/client buffering
                # client_wfile.flush() # Be cautious with flush in loops, might block
            except socket.error as e:
                # Detect broken pipe or connection reset
                if e.errno == 32 or e.errno == 104: # Broken pipe (Linux), Connection reset by peer (Linux)
                    print(f"[SSE Broadcast] Client disconnected (socket error {e.errno}). Removing.")
                    disconnected_clients.add(client_wfile)
                else:
                    print(f"[SSE Broadcast] Error sending to client: {e}")
                    disconnected_clients.add(client_wfile) # Remove on other errors too
            except Exception as e:
                 print(f"[SSE Broadcast] Unexpected error sending to client: {e}")
                 disconnected_clients.add(client_wfile) # Remove on unexpected errors

        # Remove disconnected clients outside the iteration loop
        for client in disconnected_clients:
            sse_clients.discard(client) # Use discard to avoid error if already removed

# --- SSE Message Queue Processor Thread ---
def sse_queue_processor():
    """Dedicated thread to process messages and broadcast them via SSE."""
    print("[SSE Processor] Starting SSE message processor thread.")
    while True:
        try:
            # Wait indefinitely for a message
            message = sse_message_queue.get()
            if message is None: # Use None as a signal to stop the thread
                 print("[SSE Processor] Received stop signal. Exiting.")
                 break
            # print("[SSE Processor] Got message from queue, broadcasting...") # Debug
            broadcast_sse_message(message)
            sse_message_queue.task_done() # Mark task as complete
        except Exception as e:
            print(f"[SSE Processor] Error processing queue: {e}")
            # Avoid exiting the thread on error, just log it
    print("[SSE Processor] SSE message processor thread stopped.")

# Start the SSE processor thread globally
sse_processor_thread = threading.Thread(target=sse_queue_processor, daemon=True)
sse_processor_thread.start()

# --- HTTP Request Handler (Handles POST and SSE GET) ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    """Handles POST requests and SSE GET requests"""

    def do_GET(self):
        """Handles SSE connections on /events and basic GET"""
        global sse_clients, sse_clients_lock
        if self.path == '/events':
            # --- Handle SSE Connection ---
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('Access-Control-Allow-Origin', '*') # Allow cross-origin
            self.end_headers()
            print(f"[SSE Handler - Thread {threading.get_ident()}] New SSE client connected.")

            client_wfile = self.wfile # Get the client's write stream

            # Add client to the set
            with sse_clients_lock:
                sse_clients.add(client_wfile)
                print(f"[SSE Handler] Total SSE clients: {len(sse_clients)}")

            try:
                # Keep the connection open. The actual sending happens in broadcast_sse_message.
                # We need a way to detect disconnection here. Reading is one way.
                # Reading blocks, so this might not be ideal if we need the handler thread for other things.
                # A simple heartbeat or just relying on write errors in broadcast might be sufficient.
                # Let's just keep it open and rely on write errors for now.
                while True:
                    # Send a heartbeat comment every 15 seconds to keep connection alive
                    # and help detect disconnects sooner on some proxies/browsers
                    time.sleep(15)
                    self.wfile.write(":heartbeat\n\n".encode('utf-8'))

            except socket.error as e:
                 # Client disconnected
                 print(f"[SSE Handler - Thread {threading.get_ident()}] SSE client disconnected (socket error: {e}).")
            except Exception as e:
                 print(f"[SSE Handler - Thread {threading.get_ident()}] Error in SSE keep-alive: {e}")
            finally:
                 # Remove client on disconnect or error
                 with sse_clients_lock:
                     sse_clients.discard(client_wfile)
                     print(f"[SSE Handler - Thread {threading.get_ident()}] SSE client removed. Total SSE clients: {len(sse_clients)}")

        else:
            # --- Handle other GET requests (e.g., health check) ---
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(f'Electron Listener Node HTTP Server active on port {LISTEN_PORT}. SSE endpoint at /events'.encode('utf-8'))

    def do_POST(self):
        """Handles incoming data via POST, stores optional images, and pushes to SSE queue"""
        global latest_received_data, data_lock, sse_message_queue
        content_type = self.headers.get('Content-Type', '')
        content_length = int(self.headers.get('Content-Length', 0))
        response_status = 500
        response_message = {'status': 'error', 'message': 'Internal server error'}
        received_payload = None
        # --- Variables to hold base64 data found in this request ---
        main_image_b64 = None
        color_image_b64 = None
        depth_image_b64 = None
        openpose_image_b64 = None
        # ---

        print(f"[POST Handler - Thread {threading.get_ident()}] Received POST request with content type: {content_type}")

        try:
            if content_length == 0:
                print("[POST Handler] Received POST with no data.")
                response_status = 400
                response_message = {'status': 'error', 'message': 'No data received'}
                return # Use finally block to send response

            body = self.rfile.read(content_length)
            current_timestamp = time.time()

            if content_type.startswith('application/json'):
                print("[POST Handler] Received JSON data.")
                data_string = body.decode('utf-8')
                parsed_data = json.loads(data_string)
                received_payload = parsed_data # Store the full original payload

                # --- Extract base64 images from JSON ---
                if isinstance(parsed_data, dict):
                    main_image_b64 = parsed_data.get("image_base64")
                    color_image_b64 = parsed_data.get("color_image_base64")
                    depth_image_b64 = parsed_data.get("depth_image_base64")
                    openpose_image_b64 = parsed_data.get("openpose_image_base64")

                    count = sum(1 for img in [main_image_b64, color_image_b64, depth_image_b64, openpose_image_b64] if img)
                    print(f"[POST Handler] Found {count} base64 image(s) in JSON for processing.")
                    # ---

            else:
                print(f"[POST Handler] Received non-JSON content type: {content_type}. Treating as main image if possible.")
                try:
                    # Assume it might be image data, try encoding to base64
                    main_image_b64 = base64.b64encode(body).decode('utf-8')
                    # Add data URI prefix based on content type if it's an image type
                    if content_type.startswith('image/'):
                         main_image_b64 = f"data:{content_type};base64,{main_image_b64}"
                    print("[POST Handler] Encoded non-JSON body to base64.")
                    received_payload = {"type": "binary_data", "content_type": content_type}
                except Exception as enc_e:
                     print(f"[POST Handler] Could not encode non-JSON body: {enc_e}")
                     received_payload = {"type": "unknown", "content_type": content_type}


            # --- Update shared state (including optional images) ---
            with data_lock:
                latest_received_data["payload"] = received_payload
                latest_received_data["timestamp"] = current_timestamp
                # Store base64 data separately for node processing and SSE
                latest_received_data["image_base64"] = main_image_b64
                latest_received_data["color_image_base64"] = color_image_b64
                latest_received_data["depth_image_base64"] = depth_image_b64
                latest_received_data["openpose_image_base64"] = openpose_image_b64
                print("[POST Handler] Updated latest_received_data for node and SSE.")
            # ---

            # --- Push message to SSE Queue ---
            # Send all available images in one message
            sse_payload = {
                "type": "new_images", # Changed type slightly
                "timestamp": current_timestamp,
                "image_base64": main_image_b64,
                "color_image_base64": color_image_b64,
                "depth_image_base64": depth_image_b64,
                "openpose_image_base64": openpose_image_b64,
                "payload": received_payload # Also include original payload if needed by JS
            }
            sse_message_queue.put(sse_payload)
            print("[POST Handler] Image data pushed to SSE queue.")
            # ---

            response_status = 200
            response_message = {'status': 'success', 'message': f'Data received at {current_timestamp}'}

        except json.JSONDecodeError:
            print("[POST Handler] Received invalid JSON.")
            response_status = 400
            response_message = {'status': 'error', 'message': 'Invalid JSON'}
        except Exception as e:
            print(f"[POST Handler - Thread {threading.get_ident()}] Server error handling POST: {e}")
            response_status = 500
            response_message = {'status': 'error', 'message': f'Server error: {e}'}
        finally:
            # Send HTTP response
            self.send_response(response_status)
            self.send_header('Content-type', 'application/json')
            
            # Add all CORS headers consistently
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Content-Length')
            
            self.end_headers()
            self.wfile.write(json.dumps(response_message).encode('utf-8'))

    def do_OPTIONS(self):
        """Handle preflight CORS requests"""
        print(f"[OPTIONS Handler] Received CORS preflight request from {self.address_string()}")
        
        # Send response with CORS headers
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Content-Length')
        self.send_header('Access-Control-Max-Age', '86400')  # Cache preflight for 24 hours
        self.end_headers()

    def log_message(self, format, *args):
        # Keep console cleaner
        print(f"[HTTP Log] {self.address_string()} - {format % args}")
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

# --- Define ThreadingHTTPServer ---
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle requests in a separate thread."""
    daemon_threads = True # Ensure threads exit when main process exits

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
        # Use the combined handler
        server_instance = ThreadingHTTPServer(server_address, SimpleHTTPRequestHandler)
        print(f"[Electron Listener] Starting Threading HTTP server on {host}:{port}...")

        # Run server in background thread
        # daemon=True ensures this thread exits when the ComfyUI main process exits
        server_thread = threading.Thread(target=server_instance.serve_forever, daemon=True)
        server_thread.start()
        server_started_flag = True # Mark server as successfully started
        print(f"[Electron Listener] Threading HTTP server started successfully on port {port}. SSE at /events")

    except Exception as e:
        print(f"[Electron Listener] Failed to start Threading HTTP server on port {port}: {e}")
        server_instance = None
        server_thread = None
        server_started_flag = False # Mark server start as failed

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
            # print(f"[Tensor Conversion] Success. Shape: {image_tensor.shape}") # Debug
            return image_tensor
        else:
            print(f"[Tensor Conversion] Warning: Decoded image has unexpected shape {image_np.shape}")
            return None
    except Exception as e:
        print(f"[Tensor Conversion] Error converting base64 to tensor: {e}")
        return None

# --- ComfyUI Node Class ---
class ElectronHttpListenerNode:
    _server_started = False
    # _last_processed_timestamp = 0.0 # IS_CHANGED logic might need review if using timestamp

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
        # Always trigger execution for simplicity with external updates for now
        # Timestamp logic can be added back if needed for optimization
        return float("nan")

    # --- Updated Return Types and Names ---
    RETURN_TYPES = ("STRING", "FLOAT", "IMAGE", "IMAGE", "IMAGE", "IMAGE")
    RETURN_NAMES = ("received_data_json", "timestamp", "image", "color_image", "depth_image", "openpose_image")
    # ---
    FUNCTION = "get_latest_data"
    CATEGORY = "Utils/Listeners"
    OUTPUT_NODE = True # Keep True for the JS preview widget
    
    def get_latest_data(self, trigger=None):
        global latest_received_data, data_lock, server_started_flag

        # Create a default empty tensor for missing images
        empty_image = torch.zeros((1, 64, 64, 3), dtype=torch.uint8)

        if not server_started_flag:
            print("[Node Execute] Warning: HTTP Server is not running.", flush=True)
            error_msg = {"error": "HTTP server not running", "details": f"Check port {LISTEN_PORT}."}
            return (json.dumps(error_msg), 0.0, empty_image, empty_image, empty_image, empty_image)

        current_payload = None
        current_timestamp = 0.0
        # --- Variables for tensors ---
        main_tensor = None
        color_tensor = None
        depth_tensor = None
        openpose_tensor = None
        # ---

        with data_lock:
            # Get data stored by the last POST request
            current_payload = latest_received_data["payload"]
            current_timestamp = latest_received_data["timestamp"]
            # Get base64 strings
            main_b64 = latest_received_data["image_base64"]
            color_b64 = latest_received_data["color_image_base64"]
            depth_b64 = latest_received_data["depth_image_base64"]
            op_b64 = latest_received_data["openpose_image_base64"]
            # Update last processed timestamp if using IS_CHANGED logic
            # ElectronHttpListenerNode._last_processed_timestamp = current_timestamp

        print(f"[Node Execute] Executing. Processing data from timestamp: {current_timestamp}", flush=True)

        # --- Convert base64 to Tensors ---
        main_tensor = base64_to_tensor(main_b64)
        color_tensor = base64_to_tensor(color_b64)
        depth_tensor = base64_to_tensor(depth_b64)
        openpose_tensor = base64_to_tensor(op_b64)
        # ---

        # Use empty tensor if conversion failed or data was None
        main_tensor = main_tensor if main_tensor is not None else empty_image
        color_tensor = color_tensor if color_tensor is not None else empty_image
        depth_tensor = depth_tensor if depth_tensor is not None else empty_image
        openpose_tensor = openpose_tensor if openpose_tensor is not None else empty_image

        payload_to_return = current_payload if current_payload is not None else {}
        # Optionally remove large base64 strings from the JSON output if they exist
        if isinstance(payload_to_return, dict):
             payload_to_return.pop("image_base64", None)
             payload_to_return.pop("color_image_base64", None)
             payload_to_return.pop("depth_image_base64", None)
             payload_to_return.pop("openpose_image_base64", None)
        json_output = json.dumps(payload_to_return)

        print("[Node Execute] Returning JSON, timestamp, and 4 image tensors.", flush=True)
        return (json_output, current_timestamp, main_tensor, color_tensor, depth_tensor, openpose_tensor)

# --- Ensure server starts when ComfyUI loads ---
# Try to start server when module loads, don't wait for node instantiation
# This is more reliable because ComfyUI might not instantiate all nodes immediately
if not ElectronHttpListenerNode._server_started:
    print("[Electron Listener Module] Attempting server start on module load...")
    start_http_server(LISTEN_HOST, LISTEN_PORT)
    ElectronHttpListenerNode._server_started = server_started_flag