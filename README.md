# Warranty Eval Agent

Event-driven, multi-agent system built on Google Cloud for Agentic Security, Safety, and Trust Whitepaper. It automates the warranty claim lifecycle by transforming raw user submissions into verified entitlement actions. Using a Zero-Trust Case Manager orchestrator, the system securely coordinates between specialized agents (entitlement & logistics) to verify purchase history and generate resolution outcomes—all without exposing sensitive customer PII to the public-facing entry point.

## Prerequisites

Before beginning the deployment, ensure the following requirements are met:

* Two distinct Google Cloud Projects: You must have Owner or Editor access to an "Image" project and an "App" project.
* Billing Enabled: Active billing accounts must be linked to both projects.
* Google Cloud CLI: Ensure gcloud is installed and authenticated (gcloud auth login).

### Enable Required APIs
Run the following commands to turn on the necessary services in each project.

For the Image Project:

```bash
gcloud services enable \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    containerscanning.googleapis.com \
    --project="IMAGE_PROJECT_ID"
```

For the App Project:

```bash
# Replace 'your-app-project-id' with your actual project ID before running
gcloud services enable \
    run.googleapis.com \
    pubsub.googleapis.com \
    iam.googleapis.com \
    binaryauthorization.googleapis.com \
    storage.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com \
    clouderrorreporting.googleapis.com \
    cloudtrace.googleapis.com \
    cloudresourcemanager.googleapis.com \
    modelarmor.googleapis.com \
    cloudfunctions.googleapis.com \
    cloudbuild.googleapis.com \
    eventarc.googleapis.com \
    --project="APP_PROJECT_ID"
```

# Deploy Customer Warranty Portal

## 1. Set Environment Variables

Configure your terminal session with your specific project details.

```bash
# Define Project IDs
export IMAGE_PROJECT="IMAGE_PROJECT_ID"
export APP_PROJECT="APP_PROJECT_ID" # <-- REPLACE THIS

# Define Resource Names
export REGION="us-central1"
export REPO_NAME="warranty-portal-repo"
export IMAGE_NAME="portal-app:v1"
export TOPIC_NAME="warranty-claims"
export SA_NAME="portal-identity"
export AGENT_REPO_NAME="agent-registry"
export BUCKET_NAME="agent-1-vault-${APP_PROJECT}"
export STAGING_BUCKET="agent-1-staging-${APP_PROJECT}"

# Dynamically fetch the App Project Number for IAM bindings
export APP_PROJECT_NUMBER=$(gcloud projects describe $APP_PROJECT --format="value(projectNumber)")
```

## 2. Build and Push the Container Image

Navigate to (or clone) the customer-portal/ directory in this repo. This contains the `Dockerfile`, `app.py`, and `requirements.txt`. Run the following command to package your source code and push the image to the central Image Project.

```bash
gcloud builds submit \
    --project=$IMAGE_PROJECT \
    --tag=${REGION}-docker.pkg.dev/${IMAGE_PROJECT}/${REPO_NAME}/${IMAGE_NAME}
```

## 3. Configure Cross-Project Access

Grant the Cloud Run Service Agent in the App Project permission to pull the container image from the Image Project.

```bash
gcloud artifacts repositories add-iam-policy-binding $REPO_NAME \
    --project=$IMAGE_PROJECT \
    --location=$REGION \
    --member="serviceAccount:service-${APP_PROJECT_NUMBER}@serverless-robot-prod.iam.gserviceaccount.com" \
    --role="roles/artifactregistry.reader"
```

## 4. Set Up Pub/Sub

Create the destination topic in the App Project where the portal will publish incoming JSON claims.

```bash
gcloud pubsub topics create $TOPIC_NAME \
    --project=$APP_PROJECT
```

## 5. Create the Service Identity

Create a dedicated Service Account for the Cloud Run application and grant it permission to publish messages to the Pub/Sub topic.

```bash
# Create the Service Account
gcloud iam service-accounts create $SA_NAME \
    --project=$APP_PROJECT \
    --display-name="Customer Portal Service Account"

# Grant the Pub/Sub Publisher role
gcloud pubsub topics add-iam-policy-binding $TOPIC_NAME \
    --project=$APP_PROJECT \
    --member="serviceAccount:${SA_NAME}@${APP_PROJECT}.iam.gserviceaccount.com" \
    --role="roles/pubsub.publisher"
```

## 6. Deploy to Cloud Run

Deploy the service using the cross-project image, attach the dedicated service account, and open it to public traffic.

```bash
gcloud run deploy warranty-portal \
    --project=$APP_PROJECT \
    --image=${REGION}-docker.pkg.dev/${IMAGE_PROJECT}/${REPO_NAME}/${IMAGE_NAME} \
    --region=$REGION \
    --allow-unauthenticated \
    --service-account=${SA_NAME}@${APP_PROJECT}.iam.gserviceaccount.com \
    --set-env-vars=PUBSUB_TOPIC=$TOPIC_NAME,GOOGLE_CLOUD_PROJECT=$APP_PROJECT
```

After the app is deployed, navigate to the Service URL. The site should resemble the following

<img src="./images/warranty-portal-ui.png" width="400">

# Test Claim Submission

The application will not publish any of the claims yet. First, we need to create a subscription.

## 1. Create a Pull Subscription

Run this command to create a receiver for your messages in the App Project.

```bash
gcloud pubsub subscriptions create warranty-claims-sub \
    --topic=$TOPIC_NAME \
    --project=$APP_PROJECT
```

## 2. Submit a Test Claim

1. Open your Cloud Run URL in your browser
1. Fill out the form with these details:
    - Customer ID: C-552
    - Serial Number: SN-99812
    - Issue: The battery no longer holds a charge.
1. Click Submit Claim. You should see a success message on the site.

## 3. Pull and Decode the Message

Now, let's see if the message actually made it to the subscription. We'll pull the message and look at the data.

```bash
gcloud pubsub subscriptions pull warranty-claims-sub \
    --project=$APP_PROJECT \
    --auto-ack \
    --format="json"
```

To verify the data:

1. In the output, locate the "data" field (it will look like a long string of random characters e.g., `"data": "eyJldmVudCI6..."`).
1. Copy the string
1. Run the following command, replacing `PASTE_DATA_HERE` with your string:

```bash
echo "PASTE_DATA_HERE" | base64 --decode
```
You should see the original JSON payload:

<img src="./images/decoded-message.png" width="900">

# Deploy Agent 1

Agent 1 is our case manager. The purpose of this agent is to receive the claims, make A2A calls to our other two agents, and write interaction summaries to a Storage or BigQuery table. 

## 1. Secure Artifact Infrastructure

Before deploying the agent, we need to establish the private zone where Agent 1's artifacts will live and be scanned.

```bash
# 1. Create the Secure Repository
gcloud artifacts repositories create agent-registry \
    --project=$IMAGE_PROJECT \
    --repository-format=docker \
    --location=$REGION \
    --description="Secure vault for AI Agent images and manifests"

# 2 Create the Staging Bucket
gcloud storage buckets create gs://$STAGING_BUCKET --project=$APP_PROJECT --location=$REGION
```

## 2. Initialize Environment

I'm using a venv to run Python, but you don't have to:

```bash
# 1. Create and activate a fresh virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install the ADK
pip install --upgrade google-cloud-aiplatform[adk,agent_engines]
```

## 3. Provision the Agent Identity

In order to set up IAM policies before deploying our agent, we need to create an agent identity without deploying agent code. To do so, we create an Agent Engine instance with just the `identity_type` field per [our documentation](https://docs.cloud.google.com/agent-builder/agent-engine/agent-identity#create-agent-identity). 

Run the following command to automatically create the `provisioning.py` file from the /agent-1 directory:

```bash
cat << 'EOF' > provision.py
import vertexai
from vertexai import types
import os

PROJECT = os.environ.get("APP_PROJECT")

# Initialize client using v1beta1 for Agent Identity support
client = vertexai.Client(
    project=PROJECT, 
    location="us-central1", 
    http_options=dict(api_version="v1beta1")
)

# Create an empty instance
remote_app = client.agent_engines.create(
    config={"identity_type": types.IdentityType.AGENT_IDENTITY}
)

print("\n--- SAVE THESE VALUES ---")
print(f"AGENT_ENGINE_ID: {remote_app.api_resource.name.split('/')[-1]}")
print(f"PRINCIPAL_ID: {remote_app.api_resource.spec.effective_identity}")
print("-------------------------\n")
EOF
```

Run the script you just created. This will take a few minutes to deploy:

```bash
python3 provision.py
```

Now, copy the AGENT_ENGINE_ID and the PRINCIPAL_ID values and save them as environment variables

```bash
export AGENT_PRINCIPAL="principal://PASTE_YOUR_PRINCIPAL_ID_HERE"
export ENGINE_ID="PASTE_YOUR_AGENT_ENGINE_ID_HERE"
```

## 4. Grant IAM Access

Now that we have the Principal ID, we can apply IAM boundaries. These commands grant the agent exactly what it needs and nothing more.

```bash
# Grant standard Agent Engine operational roles
for ROLE in "roles/aiplatform.expressUser" "roles/serviceusage.serviceUsageConsumer" "roles/browser"; do
  gcloud projects add-iam-policy-binding $APP_PROJECT \
    --member="$AGENT_PRINCIPAL" --role="$ROLE"
done

# Grant read-only access to the Pub/Sub claims topic
gcloud pubsub topics add-iam-policy-binding warranty-claims \
    --project=$APP_PROJECT \
    --member="$AGENT_PRINCIPAL" \
    --role="roles/pubsub.viewer"

# Grant write access to your staging bucket
gcloud storage buckets add-iam-policy-binding gs://$STAGING_BUCKET \
    --project=$APP_PROJECT \
    --member="$AGENT_PRINCIPAL" \
    --role="roles/storage.objectUser"
```

## 5. Create the Agent Logic and Deployment Scripts

Next, create the files that contain the agent's logic and the deployment mechanism. 

Run this to create `agent_logic.py` file from the /agent-1 directory:

```bash
cat << 'EOF' > agent_logic.py
from google.adk.agents import Agent

agent = Agent(
    model="gemini-2.5-flash",
    name="Case_Manager_Agent_1",
    instruction="""You are the Diagnostic Orchestrator. 
    1. Categorize the failure from the issue_description. 
    2. Realize you need to check if the product is under warranty. 
    3. You have NO access to customer PII or financial data. 
    4. Call Agent 2 for warranty verification and Agent 3 for logistics."""
)
EOF
```

Run this to create `deploy.py` file from the /agent-1 directory:

```bash
cat << 'EOF' > deploy.py
import vertexai
from agent_logic import agent
import os

PROJECT = os.environ.get("APP_PROJECT")
BUCKET = os.environ.get("STAGING_BUCKET")
AGENT_ID = os.environ.get("ENGINE_ID") 

client = vertexai.Client(
    project=PROJECT, 
    location="us-central1", 
    http_options=dict(api_version="v1beta1")
)

print("Deploying Native ADK Agent...")

# Update the instance directly with your ADK 'agent' variable
remote_app = client.agent_engines.update(
    name=f"projects/{PROJECT}/locations/us-central1/reasoningEngines/{AGENT_ID}",
    agent=agent,
    config={
        "display_name": "Case_Manager_Agent_1",
        "identity_type": vertexai.types.IdentityType.AGENT_IDENTITY,
        "requirements": [
            "google-cloud-aiplatform[adk,agent_engines]", 
            "pydantic", 
            "cloudpickle"
        ],
        "staging_bucket": f"gs://{BUCKET}",
    }
)
print("ADK Agent successfully deployed and secured!")
EOF
```

## 6. Deploy the Agent

Install the final dependencies:

```bash
pip install pydantic cloudpickle
```

Then run the deployment. This may take around 5-10 minutes to deploy:

```bash
python3 deploy.py
```

## 7. Test the Agent

For this, we'll write a quick Python script to act like the Customer Portal and send a mock claim directly to agent 1.

```bash
cat << 'EOF' > test.py
import os
import vertexai

PROJECT = os.environ.get("APP_PROJECT")
AGENT_ID = os.environ.get("ENGINE_ID") 

# Initialize Vertex AI using the specific v1beta1 Client
client = vertexai.Client(
    project=PROJECT, 
    location="us-central1",
    http_options=dict(api_version="v1beta1")
)

print("Connecting to Case Manager Agent...")
# For ADK, we use client.agent_engines.get() instead of ReasoningEngine()
remote_agent = client.agent_engines.get(
    name=f"projects/{PROJECT}/locations/us-central1/reasoningEngines/{AGENT_ID}"
)

mock_claim = """
New Claim Event:
- Customer ID: C-552
- Serial Number: SN-99812
- Issue Description: The battery no longer holds a charge.
"""

print("Sending mock claim...")

# ADK requires a user_id and uses stream_query
events = remote_agent.stream_query(
    user_id="test_user_001",
    message=mock_claim
)

print("\n=== Agent 1 Output ===")
# Since it's a stream, we iterate through the chunks
for event in events:
    # ADK returns dictionaries; we just want to print the generated text
    if "text" in event:
        print(event["text"], end="")
    else:
        print(event, end="")
print("\n======================\n")
EOF
```

Run the test:

```bash
python3 test.py
```

The agent should output a response acknowledging the battery issue, stating that it needs to check the warranty status and call agent 2. For example:

```
{'model_version': 'gemini-2.5-flash', 'content': {'parts': [{'text': 'Okay, I understand.\n\n**1. Categorize the failure:**\nThe issue description "The battery no longer holds a charge" indicates a **Battery Failure / Power Issue**.\n\n**2. Warranty Check Requirement:**\nBefore proceeding with any repair or replacement options, I need to verify if the product (SN-99812) is still under warranty.\n\n**3. Action:**\nI cannot access warranty information directly. I need to consult another agent.\n\nCalling **Agent 2 (Warranty_Verification_Agent)** to check the warranty status for **Serial Number: SN-99812**.'}], 'role': 'model'}...
```

# Deploy Pub/Sub Dispatcher

## 1. Create a Dedicated Service Account

Instead of using the default compute service account, we will create a custom, least-privilege service account specifically for this Dispatcher.

```bash
# 1. Set your variables
export SA_NAME="dispatcher-sa"
export SA_EMAIL="${SA_NAME}@${APP_PROJECT}.iam.gserviceaccount.com"

# 2. Create the dedicated Service Account
gcloud iam service-accounts create $SA_NAME \
    --description="Least-privilege SA for the Pub/Sub Dispatcher" \
    --display-name="Dispatcher Service Account" \
    --project=$APP_PROJECT
```

## 2. Create the Model Armor Template

The Dispatcher will send all content to Model Armor to scan against prompt injections, malicious URLs, and toxic content.

```bash
gcloud model-armor templates create claim-sanitizer \
    --project=$APP_PROJECT \
    --location=us \
    --basic-config-filter-enforcement=enabled \
    --pi-and-jailbreak-filter-settings-enforcement=enabled \
    --pi-and-jailbreak-filter-settings-confidence-level=HIGH \
    --malicious-uri-filter-settings-enforcement=enabled \
    --rai-settings-filters='[{"filterType":"HATE_SPEECH","confidenceLevel":"MEDIUM_AND_ABOVE"},{"filterType":"DANGEROUS","confidenceLevel":"MEDIUM_AND_ABOVE"}]' \
    --template-metadata-log-operations \
    --template-metadata-log-sanitize-operations
```

NOTE: Revisit for SDP de-identify options.

## 3. Write the Dispatcher Code

Create the files included in the /dispatcher directory: `requirements.txt` and `main.py`:

```bash
mkdir ~/secure-dispatcher
cd ~/secure-dispatcher

# Create dependencies file
cat << 'EOF' > requirements.txt
functions-framework>=3.8.0
google-cloud-aiplatform[adk,agent_engines]>=1.50.0
google-cloud-modelarmor>=0.4.0
EOF
```

Run this to create `main.py`:

```bash
cat << 'EOF' > main.py
import base64
import json
import os
from google.cloud import modelarmor_v1
from google.cloud import aiplatform_v1beta1
import functions_framework

PROJECT = os.environ.get("APP_PROJECT")
PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER")
LOCATION = "us-central1"
AGENT_ID = os.environ.get("ENGINE_ID")

exec_client = aiplatform_v1beta1.ReasoningEngineExecutionServiceClient()

ma_client = modelarmor_v1.ModelArmorClient(
    client_options={"api_endpoint": "modelarmor.us.rep.googleapis.com"}
)

@functions_framework.cloud_event
def process_claim(cloud_event):
    msg_data = base64.b64decode(cloud_event.data["message"]["data"]).decode("utf-8")
    
    try:
        claim_data = json.loads(msg_data)
        formatted_claim = f"""
        New Claim Event:
        - Customer ID: {claim_data.get("customer_id", "Unknown")}
        - Serial Number: {claim_data.get("serial_number", "Unknown")}
        - Issue Description: {claim_data.get("issue_description", "No description")}
        """
        claim_id = claim_data.get("claim_id", "default_001")
    except json.JSONDecodeError:
        formatted_claim = f"New Claim Event:\n{msg_data}"
        claim_id = "fallback_001"

    print(f"Sending claim {claim_id} to Model Armor...")
    
    template_name = f"projects/{PROJECT}/locations/us/templates/claim-sanitizer"
    
    request = modelarmor_v1.SanitizeUserPromptRequest(
        name=template_name,
        user_prompt_data=modelarmor_v1.DataItem(text=formatted_claim)
    )
    
    ma_response = ma_client.sanitize_user_prompt(request=request)
    
    if ma_response.sanitization_result.filter_match_state.name == "MATCH_FOUND":
        print(f"SECURITY ALERT: Malicious input detected. Dropping claim {claim_id}.")
        return "Blocked by Model Armor", 400

    print("Security check passed. Forwarding to ADK Agent 1...")
    
    engine_name = f"projects/{PROJECT_NUMBER}/locations/{LOCATION}/reasoningEngines/{AGENT_ID}"
    
    exec_request = aiplatform_v1beta1.StreamQueryReasoningEngineRequest(
        name=engine_name,
        class_method="stream_query",  
        input={
            "message": formatted_claim,
            "user_id": claim_id  
        }
    )
    
    response_stream = exec_client.stream_query_reasoning_engine(request=exec_request)

    print("\n=== Agent 1 Output ===")
    for chunk in response_stream:
        if hasattr(chunk, 'data'):
            try:
                # 1. Decode bytes to string
                raw_str = chunk.data.decode("utf-8")
                
                # 2. Parse the ADK JSON payload
                payload = json.loads(raw_str)
                
                # 3. Safely drill down to the "text" key
                parts = payload.get("content", {}).get("parts", [])
                for part in parts:
                    text_val = part.get("text")
                    if text_val:
                        # Print the text cleanly to Cloud Logging
                        print(text_val, end="", flush=True)
                        
            except json.JSONDecodeError:
                # If it isn't JSON for some reason, ignore it so it doesn't crash the loop
                pass
                
    # Print a final newline to ensure Cloud Logging registers the completed block
    print("\n======================\n", flush=True)
    
    return "Success", 200
EOF    
```    
    
## 4. Deploy the Cloud Function

Before the function can run, Google Cloud uses Cloud Build to package the Python code into a container. By default, Cloud Build uses the default compute service account to do this lifting, and it needs permission to build that container.

```bash
export PROJECT_NUMBER=$(gcloud projects describe $APP_PROJECT --format="value(projectNumber)")
export COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
export PUBSUB_SA="service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding $APP_PROJECT \
  --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/cloudbuild.builds.builder"

gcloud projects add-iam-policy-binding $APP_PROJECT \
    --member="serviceAccount:${PUBSUB_SA}" \
    --role="roles/iam.serviceAccountTokenCreator"  
```  

We also need to grant Eventarc permissions to push to Cloud Functions:

```
# 1. Invoker permission so Pub/Sub can knock on the door
gcloud run services add-iam-policy-binding pubsub-dispatcher \
    --region=us-central1 \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/run.invoker" \
    --project=$APP_PROJECT
```

This deployment command attaches the dedicated Service Account to the function and restricts network access via the --ingress-settings flag.

```bash
export APP_PROJECT=$(gcloud config get-value project)
export PROJECT_NUMBER=$(gcloud projects describe $APP_PROJECT --format="value(projectNumber)")
export SA_EMAIL="dispatcher-sa@${APP_PROJECT}.iam.gserviceaccount.com"


gcloud run deploy pubsub-dispatcher \
    --source . \
    --function process_claim \
    --region us-central1 \
    --memory 1024Mi \
    --service-account $SA_EMAIL \
    --no-allow-unauthenticated \
    --set-env-vars="APP_PROJECT=${APP_PROJECT},PROJECT_NUMBER=${PROJECT_NUMBER},ENGINE_ID=${ENGINE_ID}" \
    --project $APP_PROJECT
```

## 5. Dispatcher Permissions

Grant the Dispatcher Service Account the permissions it needs to call Model Armor and Agent Engine

```bash
# 1. Allow the Dispatcher to use Model Armor
gcloud projects add-iam-policy-binding $APP_PROJECT \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/modelarmor.user"

# 2. Allow the Dispatcher to call Vertex AI Agents
gcloud projects add-iam-policy-binding $APP_PROJECT \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/aiplatform.user"
```

## 6. Test Dispatcher

Send a claim into the Pub/Sub topic (either through the customer portal UI or directly):

```bash
gcloud pubsub topics publish warranty-claims \
    --project=$APP_PROJECT \
    --message='{"claim_id": "CLM-9921", "customer_id": "C-881", "serial_number": "SN-4451X", "issue_description": "The device screen is cracked and unresponsive."}'
```

Because Cloud Functions process messages almost instantly, you can jump straight to the logs to see your Model Armor and Agent Engine outputs. Run this command to read the latest logs:

```bash
gcloud run services logs read pubsub-dispatcher \
    --project=$APP_PROJECT \
    --region=us-central1 \
    --limit=30
```

The logs should resemble the following:

```json
2026-03-29 20:17:41 POST 200 https://pubsub-dispatcher-7hjhclnihq-uc.a.run.app/?__GCP_CloudEventsMode=CUSTOM_PUBSUB_projects%2Fprj-lab-multi-agent-b4%2Ftopics%2Fwarranty-claims
2026-03-29 20:17:41 Sending claim CLM-9921 to Model Armor...
2026-03-29 20:17:41 Security check passed. Forwarding to ADK Agent 1...
2026-03-29 20:17:47 === Agent 1 Output ===
2026-03-29 20:17:47 Okay, I've received the new claim event.
2026-03-29 20:17:47 **1. Categorizing the Failure:**
2026-03-29 20:17:47 Based on the description "The device screen is cracked and unresponsive," this is a **Physical Damage / Hardware Failure (Screen Damage)**.
2026-03-29 20:17:47 **2. Warranty Check Necessity:**
2026-03-29 20:17:47 Physical damage like a cracked screen often falls under specific warranty clauses or might be an out-of-warranty repair. Therefore, checking the warranty status is a critical next step to determine the appropriate service path.
2026-03-29 20:17:47 **3. Data Access Acknowledgment:**
2026-03-29 20:17:47 As Case_Manager_Agent_1, I do not have access to customer PII or financial data.
2026-03-29 20:17:47 **4. Orchestrating Next Steps:**
2026-03-29 20:17:47 To proceed, I need to verify the warranty status and prepare for potential logistics.
2026-03-29 20:17:47 *   **Calling Agent 2 (Warranty Verification):**
....
2026-03-29 20:17:47 ======================
```

# Deploy Agent 2

## 1. Create Agent Logic

In a new directory, /agent-2, create the agent logic.

```bash
cat << 'EOF' > agent2_logic.py
import json
from google.adk.agents import Agent

def check_warranty_status(serial_number: str) -> str:
    """
    Queries the secure BigQuery financial silo to check warranty status.
    
    Args:
        serial_number: The unique serial number of the customer's device.
    """
    # Mock BigQuery Database
    mock_db = {
        "SN-DEF": {"status": "Covered", "expiration_date": "2027-10-12", "deductible_required": False, "_hidden_cc": "4111-1111-1111-1111", "_home_address": "123 Main St"},
        "SN-XYZ": {"status": "Expired", "expiration_date": "2024-01-01", "deductible_required": True, "_hidden_cc": "5555-5555-5555-5555", "_home_address": "456 Oak Ave"},
        "SN-ALPHA": {"status": "Covered", "expiration_date": "2028-05-20", "deductible_required": False, "_hidden_cc": "4242-4242-4242-4242", "_home_address": "789 Pine Rd"}
    }

    record = mock_db.get(serial_number, {"status": "Unknown", "expiration_date": "N/A", "deductible_required": False})

    # THE SECURITY PRIMITIVE: We physically construct a new dictionary to ensure 
    # PII NEVER leaves this Python function.
    safe_response = {
        "status": record.get("status"),
        "expiration_date": record.get("expiration_date"),
        "deductible_required": record.get("deductible_required")
    }

    return json.dumps(safe_response)

# Define Agent 2
agent2 = Agent(
    model="gemini-2.5-flash",
    name="Agent_Warranty_Agent_2",
    instruction="""You are the Entitlement Guardian.
    You operate in a High-Trust Financial Silo. Your ONLY job is to take a serial_number, use your `check_warranty_status` tool to query the secure database, and return the exact JSON result.
    
    CRITICAL SECURITY PRIMITIVE: You must NEVER output, request, or handle customer names, addresses, credit card numbers, or purchase prices. Output ONLY the safe warranty status JSON.""",
    tools=[check_warranty_status]
)
EOF
```

## 2. Create an Agent Card

When Vertex AI packages your deployment, it will see this file in the root directory and automatically mount it to the /.well-known/agent-card.json endpoint.

Create a file named agent.json in the same directory:

```bash
cat << 'EOF' > agent.json
{
  "name": "Agent_Warranty_Agent_2",
  "description": "The Entitlement Guardian: Secure Data Analyst operating in a High-Trust Financial Silo.",
  "defaultInputModes": ["text/plain"],
  "skills": [
    {
      "id": "check_warranty_status",
      "name": "Check Warranty Status",
      "description": "Queries the secure database for warranty entitlement given a device serial_number. Returns strict, safe JSON without PII.",
      "tags": ["warranty", "entitlement", "secure", "A2A"]
    }
  ]
}
EOF
```

## 3. Create the Deployment Script

We are going to use client.agent_engines.create() to spin up a brand new, isolated container just for Agent 2. By deploying the directory that contains both agent2_logic.py and agent.json, the ADK wrapper will natively expose your Agent Card.

Create `deploy_agent2.py`:

```bash
cat << 'EOF' > deploy_agent2.py
import json
import vertexai
import os
from google.adk.agents import Agent

# ==========================================
# 1. THE ENTITLEMENT GUARDIAN LOGIC
# ==========================================
def check_warranty_status(serial_number: str) -> str:
    """Queries the secure database for warranty entitlement."""
    mock_db = {
        "SN-DEF": {"status": "Covered", "expiration_date": "2027-10-12", "deductible_required": False, "_hidden_cc": "4111-1111-1111-1111", "_home_address": "123 Main St"},
        "SN-XYZ": {"status": "Expired", "expiration_date": "2024-01-01", "deductible_required": True, "_hidden_cc": "5555-5555-5555-5555", "_home_address": "456 Oak Ave"},
        "SN-ALPHA": {"status": "Covered", "expiration_date": "2028-05-20", "deductible_required": False, "_hidden_cc": "4242-4242-4242-4242", "_home_address": "789 Pine Rd"}
    }

    record = mock_db.get(serial_number, {"status": "Unknown", "expiration_date": "N/A", "deductible_required": False})

    # The Zero-Trust Primitive
    safe_response = {
        "status": record.get("status"),
        "expiration_date": record.get("expiration_date"),
        "deductible_required": record.get("deductible_required")
    }
    return json.dumps(safe_response)

agent2 = Agent(
    model="gemini-2.5-flash",
    name="Agent_Warranty_Agent_2",
    instruction="""You are the Entitlement Guardian.
    You operate in a High-Trust Financial Silo. Your ONLY job is to take a serial_number, use your `check_warranty_status` tool to query the secure database, and return the exact JSON result.
    
    CRITICAL SECURITY PRIMITIVE: You must NEVER output, request, or handle customer names, addresses, credit card numbers, or purchase prices. Output ONLY the safe warranty status JSON.""",
    tools=[check_warranty_status]
)

# ==========================================
# 2. THE DEPLOYMENT EXECUTION
# ==========================================
PROJECT = os.environ.get("APP_PROJECT")
BUCKET = os.environ.get("STAGING_BUCKET")

client = vertexai.Client(
    project=PROJECT, 
    location="us-central1", 
    http_options=dict(api_version="v1beta1")
)

print("Deploying Agent 2: The Entitlement Guardian...")

remote_app = client.agent_engines.create(
    agent=agent2,
    config={
        "display_name": "Agent_Warranty_Agent_2",
        "identity_type": vertexai.types.IdentityType.AGENT_IDENTITY,
        "requirements": [
            "google-cloud-aiplatform[adk,agent_engines]", 
            "pydantic", 
            "cloudpickle"
        ],
        "staging_bucket": f"gs://{BUCKET}",
    }
)

print("\n--- SAVE THIS VALUE FOR A2A ---")
print(f"AGENT_2_ENGINE_ID: {remote_app.api_resource.name.split('/')[-1]}")
print("-------------------------------\n")
print("Agent 2 successfully deployed and broadcasting its Agent Card!")
EOF
```

## 4. Execute the Deployment

Run the deployment script in your terminal:

```bash
export APP_PROJECT=$(gcloud config get-value project)
export STAGING_BUCKET="agent-2-staging-${APP_PROJECT}"
gcloud storage buckets create gs://$STAGING_BUCKET --project=$APP_PROJECT --location=us-central1 || true

pip3 install --upgrade "google-cloud-aiplatform[adk,agent_engines]" google-adk google-cloud-storage

python3 -m pip install --upgrade google-genai google-cloud-aiplatform

python3 deploy_agent2.py
```

Then save the value for the Engine ID of our Agent 2: Self reference: 792370201382354944

## 5. Allow Agent 1 to call Agent 2

Open your original agent_logic.py (the one for Agent 1). We are going to add a Python function that uses the exact same secure gRPC streaming client we built for Cloud Run, but this time, it's running inside Agent 1 to talk to Agent 2.

Overwrite `agent_logic.py`:

```bash
cat << 'EOF' > agent_logic.py
import os
import json
from google.adk.agents import Agent

def call_entitlement_guardian(serial_number: str) -> str:
    """
    A2A Client: Calls Agent 2 (The Entitlement Guardian) to securely verify warranty status.
    Agent 1 has NO access to PII; it only receives the safe JSON response from Agent 2.
    """
    from google.cloud import aiplatform_v1beta1
    
    # We dynamically grab the project number from the container's environment
    PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER")
    AGENT_2_ID = "792370201382354944" # Your newly deployed Agent 2 ID!
    
    client = aiplatform_v1beta1.ReasoningEngineExecutionServiceClient()
    engine_name = f"projects/{PROJECT_NUMBER}/locations/us-central1/reasoningEngines/{AGENT_2_ID}"
    
    # We use the exact same streaming protocol we perfected earlier
    request = aiplatform_v1beta1.StreamQueryReasoningEngineRequest(
        name=engine_name,
        class_method="stream_query",
        input={
            "message": serial_number,
            "user_id": f"a2a-internal-{serial_number}"
        }
    )
    
    response_stream = client.stream_query_reasoning_engine(request=request)
    
    result_text = ""
    for chunk in response_stream:
        if hasattr(chunk, 'data'):
            try:
                payload = json.loads(chunk.data.decode("utf-8"))
                parts = payload.get("content", {}).get("parts", [])
                for part in parts:
                    if "text" in part:
                        result_text += part["text"]
            except json.JSONDecodeError:
                pass
                
    return result_text

# Define Agent 1 with the new A2A tool
agent = Agent(
    model="gemini-2.5-flash",
    name="Case_Manager_Agent_1",
    instruction="""You are the Diagnostic Orchestrator. 
    1. Categorize the hardware/software failure from the issue_description. 
    2. You MUST check if the product is under warranty. 
    3. You have NO access to customer PII or financial data. 
    4. Call the `call_entitlement_guardian` tool with the serial_number to get the warranty status.
    5. Output the diagnostic category and the exact warranty status JSON returned by the Guardian.""",
    tools=[call_entitlement_guardian]
)
EOF
```

Update the deploy.py for Agent 1 as well:

```bash
cat << 'EOF' > deploy.py
import os
import json
import vertexai
from google.adk.agents import Agent

# ==========================================
# 1. THE ORCHESTRATOR LOGIC (AGENT 1)
# ==========================================
def call_entitlement_guardian(serial_number: str) -> str:
    """
    A2A Client: Calls Agent 2 (The Entitlement Guardian) to securely verify warranty status.
    Agent 1 has NO access to PII; it only receives the safe JSON response from Agent 2.
    """
    from google.cloud import aiplatform_v1beta1
    
    # We dynamically grab the project number from the container's environment
    PROJECT_NUMBER = os.environ.get("PROJECT_NUMBER")
    AGENT_2_ID = "792370201382354944" # Your newly deployed Agent 2 ID!
    
    client = aiplatform_v1beta1.ReasoningEngineExecutionServiceClient()
    engine_name = f"projects/{PROJECT_NUMBER}/locations/us-central1/reasoningEngines/{AGENT_2_ID}"
    
    # Secure gRPC Streaming Protocol
    request = aiplatform_v1beta1.StreamQueryReasoningEngineRequest(
        name=engine_name,
        class_method="stream_query",
        input={
            "message": serial_number,
            "user_id": f"a2a-internal-{serial_number}"
        }
    )
    
    response_stream = client.stream_query_reasoning_engine(request=request)
    
    result_text = ""
    for chunk in response_stream:
        if hasattr(chunk, 'data'):
            try:
                payload = json.loads(chunk.data.decode("utf-8"))
                parts = payload.get("content", {}).get("parts", [])
                for part in parts:
                    if "text" in part:
                        result_text += part["text"]
            except json.JSONDecodeError:
                pass
                
    return result_text

# Define Agent 1 with the new A2A tool
agent = Agent(
    model="gemini-2.5-flash",
    name="Case_Manager_Agent_1",
    instruction="""You are the Diagnostic Orchestrator. 
    1. Categorize the hardware/software failure from the issue_description. 
    2. You MUST check if the product is under warranty. 
    3. You have NO access to customer PII or financial data. 
    4. Call the `call_entitlement_guardian` tool with the serial_number to get the warranty status.
    5. Output the diagnostic category and the exact warranty status JSON returned by the Guardian.""",
    tools=[call_entitlement_guardian]
)

# ==========================================
# 2. THE DEPLOYMENT EXECUTION
# ==========================================
PROJECT = os.environ.get("APP_PROJECT")
PROJECT_NUMBER = str(os.environ.get("PROJECT_NUMBER")) 
BUCKET = os.environ.get("STAGING_BUCKET")

client = vertexai.Client(
    project=PROJECT, 
    location="us-central1", 
    http_options=dict(api_version="v1beta1")
)

print("Deploying a FRESH Agent 1 with Python 3.12 and A2A capabilities...")

# Using .create() forces a brand new Python 3.12 container
remote_app = client.agent_engines.create(
    agent=agent,
    config={
        "display_name": "Case_Manager_Agent_1",
        "identity_type": vertexai.types.IdentityType.AGENT_IDENTITY,
        "env_vars": {
            "PROJECT_NUMBER": PROJECT_NUMBER
        },
        "requirements": [
            "google-cloud-aiplatform[adk,agent_engines]", 
            "pydantic", 
            "cloudpickle"
        ],
        "staging_bucket": f"gs://{BUCKET}",
    }
)

print("\n--- NEW AGENT 1 DEPLOYED! ---")
print(f"NEW_AGENT_1_ID: {remote_app.api_resource.name.split('/')[-1]}")
print("-------------------------------\n")
EOF
```

Redeploy Agent 1:

```bash
python3 deploy.py
```
