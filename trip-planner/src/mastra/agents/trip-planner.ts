import { Agent } from "@mastra/core/agent";
import { openai } from "@ai-sdk/openai";
import { weatherTool } from "../tools/weather-tool";

export const tripPlannerAgent = new Agent({
  name: "tripPlanner",
  instructions: `
You are a friendly trip-planning assistant.

You can:
- Check the weather for a city using the getWeather tool.
- Mutate the user's shared itinerary by calling the frontend action 'updateItinerary'
  (operations: 'add' a stop, 'remove' a stop by index, or 'clear' all stops).
- When the user wants to book or confirm the trip, ALWAYS call the frontend
  action 'confirmBooking' first and wait for their approval before saying it's booked.

Keep replies short. Narrate what you're doing as you call tools.
`,
  model: openai("gpt-4o-mini"),
  tools: { weatherTool },
});
