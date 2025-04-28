// Add a log at the very top to confirm the script is loaded at all
console.log("[A3D Listener JS] Script loaded (SSE Version).");

import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js"; // Keep api import for potential future use

// --- Global variable for the EventSource connection ---
let sseSource = null;
let sseRetryTimeout = null; // To handle reconnection attempts

// --- Function to update the image previews in a specific node ---
function updateNodeImagePreviews(node, messageData) {
    if (!node || !messageData) return;
    console.log(`[A3D Listener JS] Updating previews for node: ${node.title} (ID: ${node.id})`);

    // Find the main container widget
    let containerWidget = node.widgets?.find(w => w.name === "http_preview_container");

    if (!containerWidget || !containerWidget.elements) {
        console.error("[A3D Listener JS] Preview container widget or elements not found for node:", node.id);
        // Attempt to recreate if missing
        addPreviewWidgets(node); // Call the function that creates the widgets
        containerWidget = node.widgets?.find(w => w.name === "http_preview_container");
        if (!containerWidget || !containerWidget.elements) {
             console.error("[A3D Listener JS] Failed to find/recreate preview container widget for node:", node.id);
             return;
        }
    }

    // Helper function to update a single div's background
    const updateDivBackground = (divElement, base64Data, defaultText = "N/A") => {
        if (!divElement) return;
        if (base64Data && typeof base64Data === 'string') {
            // Create an Image object in memory to check validity and potentially get size
            const img = new Image();
            img.onload = () => {
                const w = img.naturalWidth;
                const h = img.naturalHeight;
                let mimeType = "image/jpeg"; // Default assumption
                if (base64Data.startsWith('data:image/png')) mimeType = 'image/png';
                else if (base64Data.startsWith('data:image/webp')) mimeType = 'image/webp';
                else if (!base64Data.startsWith('data:image/')) {
                     // Assume jpeg if no prefix
                } else {
                     mimeType = ''; // Has prefix already
                }
                const dataUri = mimeType ? `data:${mimeType};base64,${base64Data}` : base64Data;

                divElement.style.backgroundImage = `url("${dataUri}")`;
                divElement.textContent = ''; // Clear placeholder text
                divElement.title = `Preview (${w}x${h})`;

                // --- Optional: Resize main node based ONLY on the main image ---
                if (divElement === containerWidget.elements.main) {
                    requestAnimationFrame(() => {
                        try {
                            const currentWidth = node.size[0];
                            const ratio = h / w;
                            // Calculate height based on main image, but consider a max overall height
                            const mainImageHeight = Math.min(currentWidth * ratio, 300); // Max height for main image area
                            // Calculate total node height (rough estimate)
                            const totalHeight = mainImageHeight + 100; // Add space for other previews + padding
                            const newSize = [currentWidth, totalHeight];

                            console.log(`[A3D Listener JS] Resizing node based on main image: [${newSize[0]}, ${newSize[1]}]`);
                            node.setSize(newSize);
                            node.setDirtyCanvas(true, true);
                        } catch(e) { console.error("Error resizing node:", e); }
                    });
                }
                // --- End Optional Resize ---

            };
            img.onerror = () => {
                console.error("[A3D Listener JS] Error loading base64 data for background.");
                divElement.style.backgroundImage = 'none';
                divElement.textContent = 'Error';
                divElement.title = 'Error loading image';
            };
            // Set src for the temporary image
            let tempMimeType = "image/jpeg";
            if (base64Data.startsWith('data:image/png')) tempMimeType = 'image/png';
            else if (base64Data.startsWith('data:image/webp')) tempMimeType = 'image/webp';
            else if (!base64Data.startsWith('data:image/')) { }
            else { tempMimeType = ''; }
            const tempDataUri = tempMimeType ? `data:${tempMimeType};base64,${base64Data}` : base64Data;
            img.src = tempDataUri;

        } else {
            // No base64 data provided for this slot
            divElement.style.backgroundImage = 'none';
            divElement.textContent = defaultText;
            divElement.title = defaultText;
        }
    };

    // Update backgrounds for all divs
    console.log("[A3D Listener JS] Updating preview backgrounds...");
    updateDivBackground(containerWidget.elements.main, messageData.color_image_base64, "Waiting...");
    updateDivBackground(containerWidget.elements.depth, messageData.depth_image_base64, "Depth N/A");
    updateDivBackground(containerWidget.elements.openpose, messageData.openpose_image_base64, "Pose N/A");
    
    // Request redraw after attempting updates (might be redundant if resize happens)
    node.setDirtyCanvas(true, true);
}

// --- Function to add the preview widgets (container with divs) ---
function addPreviewWidgets(node) {
    let containerWidget = node.widgets?.find(w => w.name === "http_preview_container");
    if (!containerWidget) {
        console.log("[A3D Listener JS] Adding preview container widget for node:", node.id);

        // --- Create Container ---
        const containerDiv = document.createElement("div");
        containerDiv.className = "A3D-http-preview-container";
        containerDiv.style.width = "100%";
        containerDiv.style.display = "flex";
        containerDiv.style.flexDirection = "column";
        containerDiv.style.gap = "4px";

        // --- Create Main Preview Div ---
        const mainDiv = document.createElement("div");
        mainDiv.className = "A3D-http-preview-main";
        mainDiv.style.width = "100%";
        mainDiv.style.height = "200px";
        mainDiv.style.backgroundColor = "#222";
        mainDiv.style.color = "#888";
        mainDiv.style.textAlign = "center";
        mainDiv.style.lineHeight = "200px";
        mainDiv.style.fontSize = "14px";
        mainDiv.textContent = "Waiting...";
        mainDiv.style.backgroundSize = "contain";
        mainDiv.style.backgroundPosition = "center";
        mainDiv.style.backgroundRepeat = "no-repeat";
        containerDiv.appendChild(mainDiv);


        // --- Create Optional Images Row ---
        const optionalRowDiv = document.createElement("div");
        optionalRowDiv.className = "A3D-http-preview-row";
        optionalRowDiv.style.display = "flex";
        optionalRowDiv.style.width = "100%";
        optionalRowDiv.style.height = "80px";
        optionalRowDiv.style.gap = "4px";
        containerDiv.appendChild(optionalRowDiv);

        // Create optional divs with helper function
        const createOptionalDiv = (label) => {
            const div = document.createElement("div");
            div.className = `A3D-http-preview-${label.toLowerCase()}`;
            div.style.flex = "1";
            div.style.height = "100%";
            div.style.backgroundColor = "#222";
            div.style.color = "#888";
            div.style.textAlign = "center";
            div.style.lineHeight = "80px";
            div.style.fontSize = "12px";
            div.textContent = label;
            div.style.backgroundSize = "contain";
            div.style.backgroundPosition = "center";
            div.style.backgroundRepeat = "no-repeat";
            optionalRowDiv.appendChild(div);
            return div;
        };
        
        // Only create depth and pose divs
        const depthDiv = createOptionalDiv("Depth");
        const openposeDiv = createOptionalDiv("Pose");

        try {
            containerWidget = node.addDOMWidget("http_preview_container", "div", containerDiv, {
                serialize: false, hideOnZoom: false,
            });
            // Store references to inner divs for later updates
            containerWidget.elements = {
                main: mainDiv,           // This is now the color image
                depth: depthDiv,
                openpose: openposeDiv
            };

            // Compute size based on container layout
            containerWidget.computeSize = function(width) {
                let mainHeight = 200; // Default
                let rowHeight = 80; // Default
                if (this.elements?.main?.style.height?.endsWith('px')) {
                    mainHeight = parseInt(this.elements.main.style.height, 10);
                }
                if (this.elements?.depth?.style.height?.endsWith('px')) {
                    rowHeight = parseInt(this.elements.depth.style.height, 10);
                }
                const totalHeight = mainHeight + rowHeight + 16; // Add gap + padding
                return [width, totalHeight];
            };

            // Set initial node size
            node.setSize(containerWidget.computeSize(node.size[0]));
            console.log("[A3D Listener JS] Successfully added preview container widget.");

        } catch (e) {
            console.error("[A3D Listener JS] Error adding preview container widget:", e);
        }
    }
    return containerWidget;
}

// --- Function to connect to SSE endpoint ---
function connectSSE() {
    // Close existing connection if any
    if (sseSource) {
        console.log("[A3D Listener JS] Closing existing SSE connection.");
        sseSource.close();
        sseSource = null;
    }

    // Clear any existing retry timeout
    if (sseRetryTimeout) {
        clearTimeout(sseRetryTimeout);
        sseRetryTimeout = null;
    }

    // Use the ComfyUI integrated endpoint
    const sseUrl = `/a3d_events`;
    
    console.log(`[A3D Listener JS] Connecting to SSE endpoint: ${sseUrl}`);

    try {
        sseSource = new EventSource(sseUrl);

        sseSource.onopen = function(event) {
            console.log("[A3D Listener JS] SSE Connection established.");
            // Reset retry delay on successful connection
        };

        sseSource.onmessage = function(event) {
            // Log raw data arrival immediately
            console.log("[A3D Listener JS] Raw SSE message received");
            try {
                const messageData = JSON.parse(event.data);

                // --- Use new message type 'new_images' ---
                if (messageData.type === "new_images") {
                    console.log("[A3D Listener JS] Received new images via SSE.");
                    const graph = app.graph;
                    const listenerNodes = graph.findNodesByType("A3DListener");

                    if (listenerNodes && listenerNodes.length > 0) {
                        console.log(`[A3D Listener JS] Found ${listenerNodes.length} listener node(s). Updating...`);
                        listenerNodes.forEach(node => {
                            // --- Call the new update function ---
                            updateNodeImagePreviews(node, messageData);
                            // ---
                        });
                    } else {
                        console.log("[A3D Listener JS] No A3DListener nodes found on the graph to update.");
                    }
                // Handle old 'new_image' type for backward compatibility? Optional.
                } else if (messageData.type === "new_image" && messageData.image_base64) {
                     console.log("[A3D Listener JS] Received legacy 'new_image' via SSE. Updating main preview only.");
                     const graph = app.graph;
                     const listenerNodes = graph.findNodesByType("A3DListener");
                     if (listenerNodes && listenerNodes.length > 0) {
                         listenerNodes.forEach(node => updateNodeImagePreviews(node, messageData)); // Still use new func
                     }
                } else if (messageData.type === "new_data") {
                     console.log("[A3D Listener JS] Received non-image data via SSE:", messageData.payload);
                }

            } catch (e) {
                console.error("[A3D Listener JS] Error parsing SSE message data:", e, "Raw data:", event.data);
            }
        };

        sseSource.onerror = function(event) {
            console.error("[A3D Listener JS] SSE Connection error:", event);
            // Check if the error is due to connection refusal or other issues
            if (sseSource.readyState === EventSource.CLOSED) {
                 console.log("[A3D Listener JS] SSE state is CLOSED.");
            } else {
                 console.log("[A3D Listener JS] SSE state:", sseSource.readyState);
            }

            sseSource.close(); // Close the connection on error
            sseSource = null;

            // Implement exponential backoff for retries
            const retryDelay = 5000; // Retry after 5 seconds
            console.log(`[A3D Listener JS] SSE connection failed or lost. Retrying in ${retryDelay / 1000} seconds...`);
            if (!sseRetryTimeout) { // Avoid scheduling multiple retries
                 sseRetryTimeout = setTimeout(connectSSE, retryDelay);
            }
        };

    } catch (e) {
        console.error("[A3D Listener JS] Failed to create EventSource:", e);
        // Schedule a retry if creation fails
        const retryDelay = 10000; // Longer delay if initial creation fails
         console.log(`[A3D Listener JS] Retrying SSE connection in ${retryDelay / 1000} seconds...`);
         if (!sseRetryTimeout) {
             sseRetryTimeout = setTimeout(connectSSE, retryDelay);
         }
    }
}

// Update the function to send data to the server if needed
function sendDataToServer(data) {
    fetch('/a3d_data', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        },
        body: JSON.stringify(data)
    })
    .then(response => response.json())
    .then(data => console.log('Success:', data))
    .catch(error => console.error('Error:', error));
}

// --- ComfyUI Extension Registration ---
app.registerExtension({
    name: "Comfy.A3DListener.ImagePreviewSSE",
    setup() {
        console.log("[A3D Listener JS] Extension setup(). Connecting SSE...");
        connectSSE();
    },

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        if (nodeData.name === "A3DListener") {
            const originalOnNodeCreated = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function() {
                originalOnNodeCreated?.apply(this, arguments);
                addPreviewWidgets(this); // Use new function name
                console.log("[A3D Listener JS] onNodeCreated finished for:", this.title);
            }
        }
    },
});

console.log("[A3D Listener JS] Extension registration call completed (SSE Version)."); 