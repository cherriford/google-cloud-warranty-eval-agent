from google.adk.agents import Agent

agent = Agent(
    model="gemini-2.0-flash",
    name="Case_Manager",
    instruction="Analyze the warranty claim and categorize the battery issue."
)