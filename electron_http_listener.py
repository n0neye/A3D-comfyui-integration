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
latest_received_data = {"payload": None, "timestamp": 0}
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
        """Handles incoming data via POST and pushes to SSE queue"""
        global latest_received_data, data_lock, sse_message_queue
        content_type = self.headers.get('Content-Type', '')
        content_length = int(self.headers.get('Content-Length', 0))
        response_status = 500
        response_message = {'status': 'error', 'message': 'Internal server error'}
        received_payload = None
        image_base64_for_sse = None # Store base64 data specifically for SSE push
        print(f"[POST Handler] Received POST request with content type: {content_type}")

        try:
            if content_length == 0:
                print("[POST Handler] Received POST with no data.")
                response_status = 400
                response_message = {'status': 'error', 'message': 'No data received'}
                return # Use finally block to send response

            body = self.rfile.read(content_length)
            current_timestamp = time.time()

            # Process based on content type
            if content_type.startswith('application/json'):
                print("[POST Handler] Received JSON data.")
                data_string = body.decode('utf-8')
                parsed_data = json.loads(data_string)
                received_payload = parsed_data # Store the parsed JSON

                # Check if JSON contains base64 image for SSE push
                if isinstance(parsed_data, dict) and "image_base64" in parsed_data:
                    base64_data = parsed_data["image_base64"]
                    if isinstance(base64_data, str):
                        # Remove potential data URI prefix for consistency if needed,
                        # but JS can handle it, so maybe keep it? Let's keep it for now.
                        image_base64_for_sse = base64_data
                        print("[POST Handler] Found base64 image in JSON for SSE.")
                        # Optionally remove large base64 from the payload stored for the node output
                        # received_payload["image_base64"] = "[removed for node output]"

            else:
                print(f"[POST Handler] Received unknown content type: {content_type}.")
                data_string = body.decode('utf-8', errors='ignore')
                received_payload = {
                    "type": "unknown",
                    "content_type": content_type,
                    "data_preview": data_string[:200] # Store only a preview
                }

            # Update shared state for the ComfyUI node (thread-safe)
            with data_lock:
                latest_received_data["payload"] = received_payload
                latest_received_data["timestamp"] = current_timestamp
                print("[POST Handler] Updated latest_received_data for node.")

            # --- Push message to SSE Queue ---
            if image_base64_for_sse:
                sse_payload = {
                    "type": "new_image",
                    "timestamp": current_timestamp,
                    "image_base64": image_base64_for_sse, # Send the actual base64
                    "content_type": content_type if content_type.startswith('image/') else 'image/jpeg' # Assume jpeg if from json for simplicity
                }
                sse_message_queue.put(sse_payload)
                print("[POST Handler] Image data pushed to SSE queue.")
            elif received_payload: # Push non-image data too? Optional.
                 sse_payload = {
                     "type": "new_data",
                     "timestamp": current_timestamp,
                     "payload": received_payload # Send the received payload
                 }
                 sse_message_queue.put(sse_payload)
                 print("[POST Handler] Non-image data pushed to SSE queue.")


            # Prepare success response
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
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(response_message).encode('utf-8'))

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

                    # Check if JSON contains base64 encoded image
                    if isinstance(current_payload, dict) and "image_base64" in current_payload:
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