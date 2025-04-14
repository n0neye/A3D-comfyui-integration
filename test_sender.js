const fs = require('fs');
const path = require('path');
const http = require('http');
const { promisify } = require('util');

// Configuration
const SERVER_URL = 'http://localhost:8199';
const IMAGE_PATH = 'example.jpg';

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
async function sendImageAsBase64Json(imagePath) {
    console.log(`Sending image as base64 in JSON: ${imagePath}`);
    
    // Read and encode image
    const imageData = fs.readFileSync(imagePath);
    const base64Data = imageData.toString('base64');
    
    // Create payload
    const payload = {
        image_base64: base64Data,
        metadata: {
            filename: path.basename(imagePath),
            timestamp: Date.now() / 1000,
            test_mode: true
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
    // Check if image exists
    if (!fs.existsSync(IMAGE_PATH)) {
        console.error(`Error: Image file not found: ${IMAGE_PATH}`);
        console.error('Please place an example.jpg file in the same directory as this script.');
        process.exit(1);
    }
    
    // Check image properties
    checkImageResolution(IMAGE_PATH);
    
    // Send the image using all methods
    await sendImageDirect(IMAGE_PATH);
    await sendImageAsBase64Json(IMAGE_PATH);
    await sendImageAsDataUri(IMAGE_PATH);
    
    console.log('All test methods completed!');
}

// Run the program
main().catch(console.error); 