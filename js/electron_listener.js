// Add a log at the very top to confirm the script is loaded at all
console.log("[Electron Listener JS] Script loaded (SSE Version).");

import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js"; // Keep api import for potential future use

// --- Global variable for the EventSource connection ---
let sseSource = null;
let sseRetryTimeout = null; // To handle reconnection attempts

// --- Function to update the image preview in a specific node ---
function updateNodeImagePreview(node, base64Data, contentType) {
    if (!node) return;
    console.log(`[Electron Listener JS] Updating preview for node: ${node.title} (ID: ${node.id})`);

    // Find the preview widget (which should be a div now)
    let previewWidget = node.widgets?.find(w => w.name === "http_preview_div"); // Changed name

    if (!previewWidget || !previewWidget.element) {
        console.error("[Electron Listener JS] Preview widget (div) or element not found for node:", node.id);
        // Attempt to recreate if missing (might happen on graph load/reload)
        addPreviewWidget(node); // Call the function that creates the widget
        previewWidget = node.widgets?.find(w => w.name === "http_preview_div");
        if (!previewWidget || !previewWidget.element) {
             console.error("[Electron Listener JS] Failed to find/recreate preview widget for node:", node.id);
             return; // Exit if still not found
        }
    }

    // Create an Image object in memory to get dimensions and trigger onload
    const img = new Image();

    img.onload = () => {
        console.log("[Electron Listener JS] Image loaded successfully in memory (for size check).");
        const w = img.naturalWidth;
        const h = img.naturalHeight;
        console.log(`[Electron Listener JS] Image dimensions: natural=${w}x${h}`);

        // Construct the Data URI for the background image
        let mimeType = "image/jpeg";
        if (contentType && contentType.startsWith('image/')) {
            mimeType = contentType;
        } else if (base64Data.startsWith('data:image/')) {
             mimeType = ''; // Already has prefix
        }
        const dataUri = mimeType ? `data:${mimeType};base64,${base64Data}` : base64Data;

        // --- Update the DIV's background ---
        if (previewWidget.element) {
             console.log(`[Electron Listener JS] Setting div background image (URI length: ${dataUri.length})`);
             previewWidget.element.style.backgroundImage = `url("${dataUri}")`;
             previewWidget.element.title = `Preview (${w}x${h})`; // Update title
        }
        // --- End background update ---
    };

    img.onerror = () => {
        console.error("[Electron Listener JS] Error loading image into memory object.");
         if (previewWidget.element) {
            previewWidget.element.textContent = "Error loading preview"; // Show error text in div
            previewWidget.element.style.backgroundImage = 'none'; // Clear background
         }
    };

    // Set the src of the temporary Image object to start loading
    // Use the full Data URI here as well
    let tempMimeType = "image/jpeg";
    if (contentType && contentType.startsWith('image/')) {
        tempMimeType = contentType;
    } else if (base64Data.startsWith('data:image/')) {
         tempMimeType = '';
    }
    const tempDataUri = tempMimeType ? `data:${tempMimeType};base64,${base64Data}` : base64Data;
    img.src = tempDataUri;

}

// --- Function to add the preview widget (now a div) ---
function addPreviewWidget(node) {
     let previewWidget = node.widgets?.find(w => w.name === "http_preview_div");
     if (!previewWidget) {
         console.log("[Electron Listener JS] Adding preview div widget for node:", node.id);
         const div = document.createElement("div");
         div.className = "electron-http-preview"; // Add a class for potential styling
         div.style.width = "100%";
         div.style.height = "256px"; // Initial height
         div.style.backgroundColor = "#222"; // Placeholder background
         div.style.color = "#888";
         div.style.textAlign = "center";
         div.style.lineHeight = "256px"; // Center placeholder text vertically
         div.style.fontSize = "14px";
         div.textContent = "Waiting...";
         // Background image styles
         div.style.backgroundSize = "contain";
         div.style.backgroundPosition = "center";
         div.style.backgroundRepeat = "no-repeat";

         try {
             previewWidget = node.addDOMWidget("http_preview_div", "div", div, { // Changed name and type
                 serialize: false, // Don't save preview state with workflow
                 hideOnZoom: false,
             });
             previewWidget.element = div; // Store reference to the div

             // Adjust computeSize if needed - it should return the desired height for the widget space
             // It no longer directly depends on a child img's naturalHeight
             previewWidget.computeSize = function(width) {
                 // Let's try returning the element's current height or a default/max
                 let targetHeight = 256; // Default/initial height
                 if (this.element?.style.height && this.element.style.height.endsWith('px')) {
                     targetHeight = parseInt(this.element.style.height, 10);
                 }
                 // Use the actual node size calculation from LiteGraph?
                 // Or just return a fixed/max height for the widget area?
                 // Let's base it on the div's styled height for now.
                 // console.log(`[ComputeSize - Div] Node: ${node.id}, Width: ${width}, Returning H: ${targetHeight}`);
                 return [width, targetHeight + 4]; // Add padding
             };
             // Set initial node size
             node.setSize(node.computeSize(node.size[0]));
             console.log("[Electron Listener JS] Successfully added preview div widget.");

         } catch (e) {
             console.error("[Electron Listener JS] Error adding preview div widget:", e);
         }
     }
     return previewWidget;
}

// --- Function to connect to SSE endpoint ---
function connectSSE() {
    // Clear any existing retry timeout
    if (sseRetryTimeout) {
        clearTimeout(sseRetryTimeout);
        sseRetryTimeout = null;
    }

    // Close existing connection if any
    if (sseSource) {
        console.log("[Electron Listener JS] Closing existing SSE connection.");
        sseSource.close();
        sseSource = null;
    }

    // --- Construct the correct absolute URL ---
    // Get the current hostname (e.g., 127.0.0.1 or localhost)
    const hostname = window.location.hostname;
    // Define the correct port for our listener server
    const listenerPort = 8199; // Make sure this matches LISTEN_PORT in Python
    const sseUrl = `http://${hostname}:${listenerPort}/events`;
    // --- End of URL construction ---


    console.log(`[Electron Listener JS] Connecting to SSE endpoint: ${sseUrl}`); // Log the correct URL

    try {
        sseSource = new EventSource(sseUrl);

        sseSource.onopen = function(event) {
            console.log("[Electron Listener JS] SSE Connection established.");
            // Reset retry delay on successful connection
        };

        sseSource.onmessage = function(event) {
            // console.log("[Electron Listener JS] SSE message received:", event.data); // Debug raw data
            try {
                const messageData = JSON.parse(event.data);
                // console.log("[Electron Listener JS] Parsed SSE message:", messageData); // Debug parsed data

                if (messageData.type === "new_image" && messageData.image_base64) {
                    console.log("[Electron Listener JS] Received new image via SSE.");
                    // Find all ElectronHttpListener nodes currently on the graph
                    const graph = app.graph;
                    const listenerNodes = graph.findNodesByType("ElectronHttpListener");

                    if (listenerNodes && listenerNodes.length > 0) {
                        console.log(`[Electron Listener JS] Found ${listenerNodes.length} listener node(s). Updating...`);
                        listenerNodes.forEach(node => {
                            updateNodeImagePreview(node, messageData.image_base64, messageData.content_type);
                        });
                    } else {
                        console.log("[Electron Listener JS] No ElectronHttpListener nodes found on the graph to update.");
                    }
                } else if (messageData.type === "new_data") {
                     console.log("[Electron Listener JS] Received non-image data via SSE:", messageData.payload);
                     // Optionally update a text widget or log it
                }

            } catch (e) {
                console.error("[Electron Listener JS] Error parsing SSE message data:", e, "Raw data:", event.data);
            }
        };

        sseSource.onerror = function(event) {
            console.error("[Electron Listener JS] SSE Connection error:", event);
            // Check if the error is due to connection refusal or other issues
            if (sseSource.readyState === EventSource.CLOSED) {
                 console.log("[Electron Listener JS] SSE state is CLOSED.");
            } else {
                 console.log("[Electron Listener JS] SSE state:", sseSource.readyState);
            }

            sseSource.close(); // Close the connection on error
            sseSource = null;

            // Implement exponential backoff for retries
            const retryDelay = 5000; // Retry after 5 seconds
            console.log(`[Electron Listener JS] SSE connection failed or lost. Retrying in ${retryDelay / 1000} seconds...`);
            if (!sseRetryTimeout) { // Avoid scheduling multiple retries
                 sseRetryTimeout = setTimeout(connectSSE, retryDelay);
            }
        };

    } catch (e) {
        console.error("[Electron Listener JS] Failed to create EventSource:", e);
        // Schedule a retry if creation fails
        const retryDelay = 10000; // Longer delay if initial creation fails
         console.log(`[Electron Listener JS] Retrying SSE connection in ${retryDelay / 1000} seconds...`);
         if (!sseRetryTimeout) {
             sseRetryTimeout = setTimeout(connectSSE, retryDelay);
         }
    }
}


// --- ComfyUI Extension Registration ---
app.registerExtension({
    name: "Comfy.ElectronHttpListener.ImagePreviewSSE",
    setup() {
        console.log("[Electron Listener JS] Extension setup(). Connecting SSE...");
        connectSSE();
    },

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "ElectronHttpListener") {
            console.log("[Electron Listener JS] Matched node type: ElectronHttpListener.");

            const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function() {
                console.log("[Electron Listener JS] onNodeCreated triggered for node:", this.title, "ID:", this.id);
                originalOnNodeCreated?.apply(this, arguments);
                // --- Ensure the preview widget (div) is created ---
                addPreviewWidget(this);
                // ---
                console.log("[Electron Listener JS] onNodeCreated finished for:", this.title);
            }
            console.log("[Electron Listener JS] onNodeCreated override applied (for div widget).");

            // Add onRemoved callback to potentially clean up? (Optional)
            const originalOnRemoved = nodeType.prototype.onRemoved;
            nodeType.prototype.onRemoved = function() {
                 console.log("[Electron Listener JS] Node removed:", this.id);
                 // Cleanup logic if needed
                 originalOnRemoved?.apply(this, arguments);
            };

        }
    },
});

console.log("[Electron Listener JS] Extension registration call completed (SSE Version)."); 