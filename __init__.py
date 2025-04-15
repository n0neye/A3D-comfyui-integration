# __init__.py in comfyui_electron_listener folder

# Import node class from your node file
from .electron_http_listener import ElectronHttpListenerNode

# Define mapping dictionary needed by ComfyUI
NODE_CLASS_MAPPINGS = {
    # "UniqueNodeName": ClassName
    "ElectronHttpListener": ElectronHttpListenerNode
}

# Define node display names in UI
NODE_DISPLAY_NAME_MAPPINGS = {
    # "UniqueNodeName": "Display Name in UI"
    "ElectronHttpListener": "Electron HTTP Listener"
}

# Define the web directory for JavaScript files
WEB_DIRECTORY = "./js"
# Ensure WEB_DIRECTORY is included in __all__
__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']

# Print a message to console confirming node has loaded (optional, for debugging)
print("-----------------------------------------")
print("### Loading: ComfyUI Electron Listener Node (with image support & JS UI) ###")
# Can print listening port and other info here
from .electron_http_listener import LISTEN_PORT, server_started_flag
if server_started_flag:
    print(f"### - Electron HTTP Listener potentially active on port {LISTEN_PORT} ###")
    print("### - Now supporting image display in node via JS ###")
else:
    print(f"### - Electron HTTP Listener failed to start or port {LISTEN_PORT} is busy. ###")
print("-----------------------------------------")