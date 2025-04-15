// Add a log at the very top to confirm the script is loaded at all
console.log("[Electron Listener JS] Script loaded (SSE Version).");

import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js"; // Keep api import for potential future use

// --- Global variable for the EventSource connection ---
let sseSource = null;
let sseRetryTimeout = null; // To handle reconnection attempts

// --- Function to update the image widget in a specific node ---
function updateNodeImagePreview(node, base64Data, contentType) {
    if (!node) return;
    console.log(`[Electron Listener JS] Updating preview for node: ${node.title} (ID: ${node.id})`);

    // Find or create the image widget
    let imgWidget = node.widgets?.find(w => w.name === "http_preview_image");

    if (!imgWidget) {
        console.log("[Electron Listener JS] Image widget not found. Creating new one...");
        const img = document.createElement("img");
        img.style.width = "100%";
        img.style.objectFit = "contain";
        img.style.maxHeight = "256px";
        img.style.display = "block";
        img.alt = "HTTP Preview";
        img.title = "Preview from HTTP Listener (via SSE)";

        try {
            imgWidget = node.addDOMWidget("http_preview_image", "img", img, {});
            imgWidget.element = img;
            imgWidget.computeSize = function(width) {
                // Add logging inside computeSize
                if (this.element?.naturalWidth && this.element?.naturalHeight) {
                    const ratio = this.element.naturalHeight / this.element.naturalWidth;
                    const height = width * ratio;
                    const computedHeight = Math.min(height, 256);
                    // console.log(`[ComputeSize] Node: ${node.id}, Width: ${width}, Natural H: ${this.element.naturalHeight}, Natural W: ${this.element.naturalWidth}, Computed H: ${computedHeight}`);
                    return [width, computedHeight + 4];
                }
                 // console.log(`[ComputeSize] Node: ${node.id}, Defaulting size [${width}, 100]`);
                return [width, 100];
            };
            console.log("[Electron Listener JS] Successfully added DOM widget.");
            node.setSize(node.computeSize());
        } catch (e) {
            console.error("[Electron Listener JS] Error adding DOM widget:", e);
            return;
        }
    } else {
         console.log("[Electron Listener JS] Found existing image widget.");
    }

    // Update the image source using Data URI
    if (imgWidget && imgWidget.element) {
        let mimeType = "image/jpeg";
        if (contentType && contentType.startsWith('image/')) {
            mimeType = contentType;
        } else if (base64Data.startsWith('data:image/')) {
             mimeType = '';
        }
        const dataUri = mimeType ? `data:${mimeType};base64,${base64Data}` : base64Data;

        console.log(`[Electron Listener JS] Setting image source (Data URI, length: ${dataUri.length})`);
        imgWidget.element.src = dataUri; // Set the source

        imgWidget.element.onload = () => {
            console.log("[Electron Listener JS] Image loaded successfully via SSE.");
            const w = imgWidget.element.naturalWidth;
            const h = imgWidget.element.naturalHeight;
            const clientW = imgWidget.element.clientWidth;
            const clientH = imgWidget.element.clientHeight;
            // Log dimensions *before* potential resize/redraw
            console.log(`[Electron Listener JS] Image dimensions (before resize attempt): natural=${w}x${h}, client=${clientW}x${clientH}`);

            // --- Delay resize and redraw using requestAnimationFrame ---
            requestAnimationFrame(() => {
                try {
                    // Recalculate the node size based on the now-loaded image dimensions
                    const newSize = node.computeSize();
                    console.log(`[Electron Listener JS] Computed new node size: [${newSize[0]}, ${newSize[1]}]`);
                    node.setSize(newSize);

                    // Log dimensions *after* setSize attempt
                    const postClientW = imgWidget.element.clientWidth;
                    const postClientH = imgWidget.element.clientHeight;
                    console.log(`[Electron Listener JS] Image dimensions (after setSize): client=${postClientW}x${postClientH}`);

                    // Request redraw *after* setting the size
                    node.setDirtyCanvas(true, true);
                    console.log("[Electron Listener JS] Requested redraw inside requestAnimationFrame.");
                } catch (e) {
                     console.error("[Electron Listener JS] Error during delayed resize/redraw:", e);
                }
            });
            // --- End of delayed logic ---
        }

        imgWidget.element.onerror = () => {
            console.error("[Electron Listener JS] Error loading image from Data URI.");
            imgWidget.element.alt = "Error loading preview";
            node.setDirtyCanvas(true, true);
        }
    } else {
        console.error("[Electron Listener JS] Image widget or element not found after creation/find attempt.");
    }
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
    name: "Comfy.ElectronHttpListener.ImagePreviewSSE", // Renamed slightly
    setup() {
        // This function runs once when the ComfyUI app is ready.
        // It's a good place to establish the initial SSE connection.
        console.log("[Electron Listener JS] Extension setup(). Connecting SSE...");
        connectSSE();
    },

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "ElectronHttpListener") {
            console.log("[Electron Listener JS] Matched node type: ElectronHttpListener.");

            // We don't need to override onExecuted for the preview anymore.
            // Keep onNodeCreated to potentially initialize the widget state or appearance.
            const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function() {
                console.log("[Electron Listener JS] onNodeCreated triggered for node:", this.title);
                originalOnNodeCreated?.apply(this, arguments);

                // Optionally add an empty placeholder widget immediately on creation
                let imgWidget = this.widgets?.find(w => w.name === "http_preview_image");
                if (!imgWidget) {
                     console.log("[Electron Listener JS] Adding initial placeholder widget on node creation.");
                     const img = document.createElement("img");
                     img.style.width = "100%";
                     img.style.objectFit = "contain";
                     img.style.maxHeight = "256px";
                     img.style.minHeight = "256px";
                     img.style.display = "block";
                     img.alt = "Waiting for preview...";
                     img.title = "Preview from HTTP Listener (via SSE)";
                     // Set a default small size or placeholder image?
                    //  img.src = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"; // Transparent pixel

                     try {
                         imgWidget = this.addDOMWidget("http_preview_image", "img", img, {});
                         imgWidget.element = img;
                         imgWidget.computeSize = function(width) { /* ... same computeSize as above ... */
                             if (this.element?.naturalWidth && this.element?.naturalHeight) {
                                 const ratio = this.element.naturalHeight / this.element.naturalWidth;
                                 const height = width * ratio;
                                 const computedHeight = Math.min(height, 256);
                                 return [width, computedHeight + 4];
                             }
                             return [width, 100]; // Default size
                         };
                         this.setSize(this.computeSize());
                     } catch (e) {
                         console.error("[Electron Listener JS] Error adding placeholder DOM widget:", e);
                     }
                }
                console.log("[Electron Listener JS] onNodeCreated finished for:", this.title);
            }
            console.log("[Electron Listener JS] onNodeCreated override applied.");
        }
    },
});

console.log("[Electron Listener JS] Extension registration call completed (SSE Version)."); 