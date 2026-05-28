import { createTool } from "@mastra/core/tools";
import { z } from "zod";

// Deterministic fake weather so the demo runs with no external API key.
function fakeForecast(city: string) {
  const seed = [...city.toLowerCase()].reduce((a, c) => a + c.charCodeAt(0), 0);
  const conditions = ["sunny", "cloudy", "rainy", "windy", "partly cloudy"];
  return {
    condition: conditions[seed % conditions.length],
    highC: 15 + (seed % 15),
    lowC: 5 + (seed % 10),
  };
}

export const weatherTool = createTool({
  id: "getWeather",
  description: "Get a short weather forecast for a city.",
  inputSchema: z.object({
    city: z.string().describe("City name, e.g. 'Lisbon'"),
  }),
  outputSchema: z.object({
    city: z.string(),
    condition: z.string(),
    highC: z.number(),
    lowC: z.number(),
  }),
  execute: async ({ context }) => {
    const { city } = context;
    return { city, ...fakeForecast(city) };
  },
});
