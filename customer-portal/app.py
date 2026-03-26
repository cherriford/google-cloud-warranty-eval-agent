import os
import json
from flask import Flask, render_template_string, request
from google.cloud import pubsub_v1

app = Flask(__name__)

# Pub/Sub Configuration
# These are pulled from environment variables set during Cloud Run deployment
PROJECT_ID = os.environ.get('GOOGLE_CLOUD_PROJECT', 'your-app-project-id')
TOPIC_ID = os.environ.get('PUBSUB_TOPIC', 'warranty-claims')

# Note: In production, instantiate the publisher client globally to reuse connections
publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(PROJECT_ID, TOPIC_ID)

# HTML Template (embedded for simplicity, using Tailwind CSS for a modern look)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Warranty Claim Portal</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 flex items-center justify-center h-screen">
    <div class="bg-white p-8 rounded-lg shadow-md w-full max-w-md">
        <h2 class="text-2xl font-bold mb-6 text-gray-800 text-center">Submit Warranty Claim</h2>
        
        {% if success %}
        <div class="bg-green-100 border border-green-400 text-green-700 px-4 py-3 rounded mb-4">
            Claim submitted successfully!
        </div>
        {% endif %}

        <form method="POST" action="/">
            <div class="mb-4">
                <label class="block text-gray-700 text-sm font-bold mb-2">Customer ID</label>
                <input type="text" name="customer_id" required class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>
            <div class="mb-4">
                <label class="block text-gray-700 text-sm font-bold mb-2">Serial Number</label>
                <input type="text" name="serial_number" required class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>
            <div class="mb-6">
                <label class="block text-gray-700 text-sm font-bold mb-2">Issue Description</label>
                <textarea name="issue_description" rows="4" required class="shadow appearance-none border rounded w-full py-2 px-3 text-gray-700 leading-tight focus:outline-none focus:ring-2 focus:ring-blue-500"></textarea>
            </div>
            <button type="submit" class="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded focus:outline-none w-full transition duration-150">
                Submit Claim
            </button>
        </form>
    </div>
</body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    success = False
    if request.method == 'POST':
        # Construct the exact JSON payload you requested
        event_data = {
            "event": "claim_submitted",
            "customer_id": request.form.get("customer_id"),
            "serial_number": request.form.get("serial_number"),
            "issue_description": request.form.get("issue_description")
        }
        
        # Publish to Pub/Sub
        try:
            data_bytes = json.dumps(event_data).encode("utf-8")
            future = publisher.publish(topic_path, data_bytes)
            future.result() # Wait for the publish to complete
            success = True
        except Exception as e:
            return f"Failed to publish claim: {e}", 500
            
    return render_template_string(HTML_TEMPLATE, success=success)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(debug=True, host="0.0.0.0", port=port)