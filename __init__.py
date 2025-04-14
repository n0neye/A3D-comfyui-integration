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

# Print a message to console confirming node has loaded (optional, for debugging)
print("-----------------------------------------")
print("### Loading: ComfyUI Electron Listener Node ###")
# Can print listening port and other info here
from .electron_http_listener import LISTEN_PORT, server_started_flag
if server_started_flag:
    print(f"### - Electron HTTP Listener potentially active on port {LISTEN_PORT} ###")
else:
    print(f"### - Electron HTTP Listener failed to start or port {LISTEN_PORT} is busy. ###")
print("-----------------------------------------")


# Optional: If your node package contains web/javascript files
# WEB_DIRECTORY = "./web"
# __all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS']