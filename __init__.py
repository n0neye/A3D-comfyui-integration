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

# Print a message to console confirming node has loaded
print("-----------------------------------------")
print("### Loading: ComfyUI A3D Listener Node (with image support & JS UI) ###")
print("### - A3D Listener active on ComfyUI routes (/a3d_data and /a3d_events) ###")
print("### - Now supporting image display in node via JS ###")
print("-----------------------------------------")