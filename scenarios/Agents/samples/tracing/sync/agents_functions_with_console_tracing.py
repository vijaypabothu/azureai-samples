# ------------------------------------
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# ------------------------------------

"""
FILE: sample_agents_functions_with_console_tracing.py

DESCRIPTION:
    This sample demonstrates how to use basic agent operations with function tools from
    the Azure Agents service using a synchronous client with tracing to console.

USAGE:
    python sample_agents_functions_with_console_tracing.py

    Before running the sample:

    pip install azure-ai-projects azure-identity opentelemetry-sdk azure-core-tracing-opentelemetry

    If you want to export telemetry to OTLP endpoint (such as Aspire dashboard
    https://learn.microsoft.com/dotnet/aspire/fundamentals/dashboard/standalone?tabs=bash)
    install:

    pip install opentelemetry-exporter-otlp-proto-grpc

    Set these environment variables with your own values:
    * PROJECT_CONNECTION_STRING - The Azure AI Project connection string, as found in your AI Studio Project.
    * AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED - Optional. Set to `true` to trace the content of chat
      messages, which may contain personal data. False by default.
"""
from typing import Any, Callable, Set

import os
import sys
import time
import json
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.projects.models import FunctionTool, RequiredFunctionToolCall, SubmitToolOutputsAction, ToolOutput
from opentelemetry import trace
from pathlib import Path


# Create an AI Project Client from a connection string, copied from your AI Studio project.
# At the moment, it should be in the format "<HostName>;<AzureSubscriptionId>;<ResourceGroup>;<HubName>"
# Customer needs to login to Azure subscription via Azure CLI and set the environment variables

project_client = AIProjectClient.from_connection_string(
    credential=DefaultAzureCredential(), conn_str=os.environ["PROJECT_CONNECTION_STRING"]
)

# Enable console tracing
# or, if you have local OTLP endpoint running, change it to
# project_client.telemetry.enable(destination="http://localhost:4317")
project_client.telemetry.enable(destination=sys.stdout)


scenario = Path(__file__).name
tracer = trace.get_tracer(__name__)


# The tracer.start_as_current_span decorator will trace the function call and enable adding additional attributes
# to the span in the function implementation. Note that this will trace the function parameters and their values.
@tracer.start_as_current_span("fetch_weather")  # type: ignore
def fetch_weather(location: str) -> str:
    """
    Fetches the weather information for the specified location.

    :param location (str): The location to fetch weather for.
    :return: Weather information as a JSON string.
    :rtype: str
    """
    # In a real-world scenario, you'd integrate with a weather API.
    # Here, we'll mock the response.
    mock_weather_data = {"New York": "Sunny, 25°C", "London": "Cloudy, 18°C", "Tokyo": "Rainy, 22°C"}

    # Adding attributes to the current span
    span = trace.get_current_span()
    span.set_attribute("requested_location", location)

    weather = mock_weather_data.get(location, "Weather data not available for this location.")
    return json.dumps({"weather": weather})


# Statically defined user functions for fast reference
user_functions: Set[Callable[..., Any]] = {
    fetch_weather,
}

# Initialize function tool with user function
functions = FunctionTool(functions=user_functions)

with tracer.start_as_current_span(scenario), project_client:
    # Create an agent and run user's request with function calls
    agent = project_client.agents.create_agent(
        model="gpt-4o",
        name="my-assistant",
        instructions="You are a helpful assistant",
        tools=functions.definitions,
    )
    print(f"Created agent, ID: {agent.id}")

    thread = project_client.agents.create_thread()
    print(f"Created thread, ID: {thread.id}")

    message = project_client.agents.create_message(
        thread_id=thread.id,
        role="user",
        content="Hello, what is the weather in New York?",
    )
    print(f"Created message, ID: {message.id}")

    run = project_client.agents.create_run(thread_id=thread.id, assistant_id=agent.id)
    print(f"Created run, ID: {run.id}")

    while run.status in ["queued", "in_progress", "requires_action"]:
        time.sleep(1)
        run = project_client.agents.get_run(thread_id=thread.id, run_id=run.id)

        if run.status == "requires_action" and isinstance(run.required_action, SubmitToolOutputsAction):
            tool_calls = run.required_action.submit_tool_outputs.tool_calls
            if not tool_calls:
                print("No tool calls provided - cancelling run")
                project_client.agents.cancel_run(thread_id=thread.id, run_id=run.id)
                break

            tool_outputs = []
            for tool_call in tool_calls:
                if isinstance(tool_call, RequiredFunctionToolCall):
                    try:
                        output = functions.execute(tool_call)
                        tool_outputs.append(
                            ToolOutput(
                                tool_call_id=tool_call.id,
                                output=output,
                            )
                        )
                    except Exception as e:
                        print(f"Error executing tool_call {tool_call.id}: {e}")

            print(f"Tool outputs: {tool_outputs}")
            if tool_outputs:
                project_client.agents.submit_tool_outputs_to_run(
                    thread_id=thread.id, run_id=run.id, tool_outputs=tool_outputs
                )

        print(f"Current run status: {run.status}")

    print(f"Run completed with status: {run.status}")

    # Delete the agent when done
    project_client.agents.delete_agent(agent.id)
    print("Deleted agent")

    # Fetch and log all messages
    messages = project_client.agents.list_messages(thread_id=thread.id)
    print(f"Messages: {messages}")
