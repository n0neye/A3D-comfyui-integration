const fs = require('fs');
const path = require('path');
const http = require('http');
const { promisify } = require('util');

// Configuration
const SERVER_URL = 'http://localhost:8199';
const EXAMPLES_DIR = 'examples/';
const IMAGE_PATHS = ['example.jpg', 'example_2.jpg'];
const IMAGE_PATH = EXAMPLES_DIR + IMAGE_PATHS[ Math.floor(Math.random() * IMAGE_PATHS.length) ];
const MAIN_IMAGE_PATH = EXAMPLES_DIR + 'example.jpg';
const COLOR_IMAGE_PATH = EXAMPLES_DIR + 'example.jpg';
const DEPTH_IMAGE_PATH = EXAMPLES_DIR + 'depth.jpg';
const OPENPOSE_IMAGE_PATH = EXAMPLES_DIR + 'openpose.png';

// Helper function to make HTTP requests
async function sendRequest(options, data) {
    return new Promise((resolve, reject) => {
        const req = http.request(options, (res) => {
            let responseData = '';
            res.on('data', (chunk) => {
                responseData += chunk;
            });
            res.on('end', () => {
                resolve({
                    statusCode: res.statusCode,
                    headers: res.headers,
                    body: responseData
                });
            });
        });

        req.on('error', (error) => {
            reject(error);
        });

        if (data) {
            req.write(data);
        }
        req.end();
    });
}

// Function to read and encode an image, returns null if file doesn't exist
function getImageBase64(imagePath) {
    if (fs.existsSync(imagePath)) {
        try {
            const imageData = fs.readFileSync(imagePath);
            const base64Data = imageData.toString('base64');
            // Determine mime type to potentially add prefix (optional, Python handles removal)
            const ext = path.extname(imagePath).toLowerCase();
            let mimeType = '';
            if (ext === '.png') mimeType = 'image/png';
            else if (ext === '.jpg' || ext === '.jpeg') mimeType = 'image/jpeg';
            else if (ext === '.webp') mimeType = 'image/webp';

            // Return with prefix for clarity, though Python might strip it
            // return mimeType ? `data:${mimeType};base64,${base64Data}` : base64Data;
            return base64Data; // Send raw base64
        } catch (e) {
            console.error(`Error reading/encoding image ${imagePath}: ${e.message}`);
            return null;
        }
    } else {
        console.warn(`Optional image not found: ${imagePath}`);
        return null;
    }
}

// Send image directly
async function sendImageDirect(imagePath) {
    console.log(`Sending image directly: ${imagePath}`);
    
    // Determine content type
    const contentType = path.extname(imagePath).toLowerCase() === '.png' 
        ? 'image/png' 
        : 'image/jpeg';
    
    // Read image as binary
    const imageData = fs.readFileSync(imagePath);
    
    // Prepare request options
    const options = {
        hostname: 'localhost',
        port: 8199,
        path: '/',
        method: 'POST',
        headers: {
            'Content-Type': contentType,
            'Content-Length': imageData.length
        }
    };
    
    // Send request
    try {
        const response = await sendRequest(options, imageData);
        console.log(`Status Code: ${response.statusCode}`);
        console.log(`Response: ${response.body}`);
    } catch (error) {
        console.error(`Error sending direct image: ${error.message}`);
    }
    console.log('--------------------------------');
}

// Send image as base64 in JSON
async function sendImagesAsBase64Json() {
    console.log(`Sending images as base64 in JSON`);

    // --- Get base64 for all images ---
    const mainB64 = getImageBase64(MAIN_IMAGE_PATH);
    const colorB64 = getImageBase64(COLOR_IMAGE_PATH);
    const depthB64 = getImageBase64(DEPTH_IMAGE_PATH);
    const openposeB64 = getImageBase64(OPENPOSE_IMAGE_PATH);
    // ---

    if (!mainB64) {
        console.error(`Error: Main image file not found or failed to encode: ${MAIN_IMAGE_PATH}`);
        return;
    }

    // Create payload, only include keys if data exists
    const payload = {
        image_base64: mainB64, // Main image is required here
        metadata: {
            filename: path.basename(MAIN_IMAGE_PATH),
            timestamp: Date.now() / 1000,
            test_mode: true
        }
    };
    if (colorB64) payload.color_image_base64 = colorB64;
    if (depthB64) payload.depth_image_base64 = depthB64;
    if (openposeB64) payload.openpose_image_base64 = openposeB64;
    // ---

    const jsonData = JSON.stringify(payload);

    // Prepare request options
    const options = {
        hostname: 'localhost',
        port: 8199, // Ensure this matches Python LISTEN_PORT
        path: '/',
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(jsonData)
        }
    };

    // Send request
    try {
        console.log(`Sending request with ${Object.keys(payload).length -1} image(s)...`); // -1 for metadata
        const response = await sendRequest(options, jsonData);
        console.log(`Status Code: ${response.statusCode}`);
        console.log(`Response: ${response.body}`);
    } catch (error) {
        console.error(`Error sending base64 JSON: ${error.message}`);
    }
    console.log('--------------------------------');
}

// Send image as data URI in JSON
async function sendImageAsDataUri(imagePath) {
    console.log(`Sending image as data URI in JSON: ${imagePath}`);
    
    // Determine content type
    const contentType = path.extname(imagePath).toLowerCase() === '.png' 
        ? 'image/png' 
        : 'image/jpeg';
    
    // Read and encode image
    const imageData = fs.readFileSync(imagePath);
    const base64Data = imageData.toString('base64');
    const dataUri = `data:${contentType};base64,${base64Data}`;
    
    // Create payload
    const payload = {
        image_base64: dataUri,
        metadata: {
            filename: path.basename(imagePath),
            format: 'data_uri'
        }
    };
    
    const jsonData = JSON.stringify(payload);
    
    // Prepare request options
    const options = {
        hostname: 'localhost',
        port: 8199,
        path: '/',
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(jsonData)
        }
    };
    
    // Send request
    try {
        const response = await sendRequest(options, jsonData);
        console.log(`Status Code: ${response.statusCode}`);
        console.log(`Response: ${response.body}`);
    } catch (error) {
        console.error(`Error sending data URI: ${error.message}`);
    }
    console.log('--------------------------------');
}

// Check image properties
function checkImageResolution(imagePath) {
    try {
        console.log(`Image path: ${imagePath}`);
        console.log(`File size: ${fs.statSync(imagePath).size} bytes`);
        console.log('Note: Node.js version cannot check image dimensions without additional libraries');
    } catch (error) {
        console.error(`Error checking image: ${error.message}`);
    }
}

// Main function
async function main() {
    // Check if main image exists
    if (!fs.existsSync(MAIN_IMAGE_PATH)) {
        console.error(`Error: Main image file not found: ${MAIN_IMAGE_PATH}`);
        process.exit(1);
    }

    // Send the images
    await sendImagesAsBase64Json();

    console.log('Test completed!');
}

// Run the program
main().catch(console.error); 