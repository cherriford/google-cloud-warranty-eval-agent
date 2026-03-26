import vertexai
from vertexai.agent_engines import AdkApp
from agent_logic import agent  # Import the brain from the other file

# Configuration
PROJECT = "YOUR_APP_PROJECT"
AGENT_ID = "ID_FROM_PROVISION_STEP"
BUCKET = "YOUR_STAGING_BUCKET"

client = vertexai.Client(project=PROJECT, location="us-central1", api_version="v1beta1")

# Wrap the agent logic into a deployable App
app = AdkApp(agent=agent)

# 'Update' pushes the local code to the cloud instance
remote_app = client.agent_engines.update(
    name=f"projects/{PROJECT}/locations/us-central1/reasoningEngines/{AGENT_ID}",
    agent=app,
    config={
        "staging_bucket": f"gs://{BUCKET}",
        "requirements": ["google-cloud-aiplatform[adk,agent_engines]"]
    }
)

print("Deployment Complete!")