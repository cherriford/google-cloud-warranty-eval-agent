import vertexai
from vertexai import types

# Use v1beta1 for Agent Identity
client = vertexai.Client(project="YOUR_APP_PROJECT", location="us-central1", api_version="v1beta1")

# Create an 'Empty' instance
remote_app = client.agent_engines.create(
    config={"identity_type": types.IdentityType.AGENT_IDENTITY}
)

print(f"Success! Agent ID: {remote_app.api_resource.name.split('/')[-1]}")
print(f"Principal ID: {remote_app.api_resource.spec.effective_identity}")