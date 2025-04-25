# Import node class from your node file
from .a3d_listener import A3DListenerNode

# Define mapping dictionary needed by ComfyUI
NODE_CLASS_MAPPINGS = {
    # "UniqueNodeName": ClassName
    "A3DListener": A3DListenerNode
}

# Define node display names in UI
NODE_DISPLAY_NAME_MAPPINGS = {
    # "UniqueNodeName": "Display Name in UI"
    "A3DListener": "A3D Listener"
}

# Define the web directory for JavaScript files
WEB_DIRECTORY = "./js"
# Ensure WEB_DIRECTORY is included in __all__
__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']

# Print a message to console confirming node has loaded (optional, for debugging)
print("-----------------------------------------")
print("### Loading: ComfyUI A3D Listener Node (with image support & JS UI) ###")
# Can print listening port and other info here
from .a3d_listener import LISTEN_PORT, server_started_flag
if server_started_flag:
    print(f"### - A3D Listener potentially active on port {LISTEN_PORT} ###")
    print("### - Now supporting image display in node via JS ###")
else:
    print(f"### - A3D Listener failed to start or port {LISTEN_PORT} is busy. ###")
print("-----------------------------------------")